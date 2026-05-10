"""Admin-only views for operational tooling."""

from __future__ import annotations

import logging
import shutil
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import connections
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from pages.models import Page
from site_pages.models import SitePage
from vdw_server.restore_state import (
    clear_pending_restore_restart,
    maintenance_lock,
    mark_pending_restore_restart,
    validate_sqlite_database,
)
from vdw_server.sitemap_utils import refresh_sitemap as regenerate_sitemap

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "db_backups/manual_backups"
CODE_SEARCH_RESULT_LIMIT = 100
CODE_SEARCH_CONTEXT_CHARS = 140

PAGE_CODE_SEARCH_FIELDS = (
    ("content_md", "Markdown source"),
    ("content_html", "Generated HTML"),
    ("original_tiki", "Original Tiki"),
    ("notes", "Internal notes"),
    ("aliases", "Legacy aliases"),
    ("front_matter", "Front matter"),
    ("meta_description", "Meta description"),
)
SITE_PAGE_CODE_SEARCH_FIELDS = (
    ("content_md", "Markdown source"),
    ("content_html", "Generated HTML"),
    ("meta_description", "Meta description"),
)


class AdminCodeSearchForm(forms.Form):
    q = forms.CharField(
        label="Text string",
        min_length=2,
        max_length=500,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Paste a URL, HTML tag, markdown fragment, or other source text",
                "size": 96,
            }
        ),
    )


def _backup_prefix_path(filename: str) -> str:
    return f"{BACKUP_PREFIX}/{filename}"


def _build_code_search_filter(field_specs: tuple[tuple[str, str], ...], query: str) -> Q:
    assert field_specs, "At least one code search field is required"
    assert query, "Code search query must not be empty"

    field_name = field_specs[0][0]
    query_filter = Q(**{f"{field_name}__icontains": query})
    for field_name, _label in field_specs[1:]:
        query_filter |= Q(**{f"{field_name}__icontains": query})
    return query_filter


def _value_contains_query(raw_value: object, query: str) -> bool:
    assert query, "Code search query must not be empty"
    if raw_value is None:
        return False
    assert isinstance(raw_value, str), f"Searchable value must be str, got {type(raw_value)}"
    return query.lower() in raw_value.lower()


def _build_code_search_snippet(value: str, query: str) -> str:
    assert isinstance(value, str), f"value must be str, got {type(value)}"
    assert query, "Code search query must not be empty"

    match_index = value.lower().find(query.lower())
    assert match_index >= 0, "Cannot build snippet for a value that does not contain the query"

    start = max(0, match_index - CODE_SEARCH_CONTEXT_CHARS)
    end = min(len(value), match_index + len(query) + CODE_SEARCH_CONTEXT_CHARS)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(value) else ""
    snippet = value[start:end].replace("\r\n", "\n").replace("\r", "\n")
    return f"{prefix}{snippet}{suffix}"


def _matched_code_fields(
    instance: object,
    field_specs: tuple[tuple[str, str], ...],
    query: str,
) -> list[dict[str, str]]:
    matches = []
    for field_name, label in field_specs:
        raw_value = getattr(instance, field_name)
        if not _value_contains_query(raw_value, query):
            continue
        assert isinstance(raw_value, str), f"{field_name} must be str once matched"
        matches.append(
            {
                "label": label,
                "snippet": _build_code_search_snippet(raw_value, query),
            }
        )
    return matches


def _search_page_code(query: str) -> tuple[list[dict[str, object]], bool]:
    field_names = [field_name for field_name, _label in PAGE_CODE_SEARCH_FIELDS]
    pages = list(
        Page.objects.filter(_build_code_search_filter(PAGE_CODE_SEARCH_FIELDS, query))
        .only("id", "title", "slug", "status", "modified_date", *field_names)
        .order_by("-modified_date", "pk")[: CODE_SEARCH_RESULT_LIMIT + 1]
    )
    has_more = len(pages) > CODE_SEARCH_RESULT_LIMIT

    results = []
    for page in pages[:CODE_SEARCH_RESULT_LIMIT]:
        assert page.slug, f"Page {page.pk} has no slug"
        if page.status == "published":
            public_url = reverse("page_detail", args=[page.slug])
        else:
            public_url = reverse("page_preview", args=[page.slug])
        results.append(
            {
                "type_label": "Page",
                "title": page.title,
                "admin_url": reverse("admin:posts_page_change", args=[page.pk]),
                "public_url": public_url,
                "modified_date": page.modified_date,
                "matched_fields": _matched_code_fields(page, PAGE_CODE_SEARCH_FIELDS, query),
            }
        )
    return results, has_more


def _search_site_page_code(query: str) -> tuple[list[dict[str, object]], bool]:
    field_names = [field_name for field_name, _label in SITE_PAGE_CODE_SEARCH_FIELDS]
    site_pages = list(
        SitePage.objects.filter(_build_code_search_filter(SITE_PAGE_CODE_SEARCH_FIELDS, query))
        .only("id", "title", "slug", "page_type", "is_published", "modified_date", *field_names)
        .order_by("-modified_date", "pk")[: CODE_SEARCH_RESULT_LIMIT + 1]
    )
    has_more = len(site_pages) > CODE_SEARCH_RESULT_LIMIT

    results = []
    for site_page in site_pages[:CODE_SEARCH_RESULT_LIMIT]:
        results.append(
            {
                "type_label": "Site page",
                "title": site_page.title,
                "admin_url": reverse("admin:pages_sitepage_change", args=[site_page.pk]),
                "public_url": site_page.get_absolute_url(),
                "modified_date": site_page.modified_date,
                "matched_fields": _matched_code_fields(
                    site_page,
                    SITE_PAGE_CODE_SEARCH_FIELDS,
                    query,
                ),
            }
        )
    return results, has_more


@staff_member_required
def code_search(request: HttpRequest) -> HttpResponse:
    has_searched = "q" in request.GET
    form = AdminCodeSearchForm(request.GET if has_searched else None)

    page_results = []
    site_page_results = []
    page_has_more = False
    site_page_has_more = False
    if has_searched and form.is_valid():
        query = form.cleaned_data["q"]
        page_results, page_has_more = _search_page_code(query)
        site_page_results, site_page_has_more = _search_site_page_code(query)

    context = admin.site.each_context(request)
    context.update(
        {
            "title": "Code Search",
            "form": form,
            "has_searched": has_searched,
            "page_results": page_results,
            "site_page_results": site_page_results,
            "page_has_more": page_has_more,
            "site_page_has_more": site_page_has_more,
            "result_count": len(page_results) + len(site_page_results),
            "result_limit": CODE_SEARCH_RESULT_LIMIT,
        }
    )
    return TemplateResponse(request, "admin/code_search.html", context)


@staff_member_required
def manual_backup(request: HttpRequest) -> HttpResponse:
    """Create a point-in-time SQLite backup and push it to S3."""
    if request.method != "POST":
        return redirect(reverse("admin:index"))

    db_settings = settings.DATABASES["default"]
    engine = db_settings.get("ENGINE")
    if engine != "django.db.backends.sqlite3":
        raise RuntimeError(
            f"MANUAL BACKUP ONLY SUPPORTS SQLITE: configured engine is {engine}"
        )

    db_path = Path(db_settings.get("NAME"))
    assert db_path.exists(), f"SQLITE DB MISSING: expected file at {db_path}"

    # Ensure enough free space on the DB filesystem for the temporary snapshot
    usage = shutil.disk_usage(str(db_path.parent))
    db_bytes = db_path.stat().st_size
    # Keep overhead modest to avoid false negatives on tight disks.
    # Empirically, SQLite backup requires ~DB size plus small metadata slack.
    # 16 MiB buffer is typically sufficient; SQLite will still raise on ENOSPC.
    overhead = 16 * 1024 * 1024  # 16 MiB overhead buffer
    required = db_bytes + overhead
    if usage.free < required:
        raise RuntimeError(
            f"INSUFFICIENT DISK: free={usage.free}B required={required}B on {db_path.parent}"
        )

    tmp_snapshot = _build_sqlite_snapshot(db_path)

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    s3_path = f"db_backups/manual_backups/backup_{timestamp}.sqlite3"

    storage_class = default_storage.__class__.__name__
    if "S3" not in storage_class:
        raise RuntimeError(
            f"WRONG STORAGE BACKEND: Using {storage_class} - not an S3 storage backend"
        )

    # Stream upload from file (avoid loading entire DB into memory)
    with tmp_snapshot.open("rb") as fp:
        saved_path = default_storage.save(s3_path, File(fp))
    if saved_path != s3_path:
        raise RuntimeError(
            f"S3 PATH MISMATCH: Requested '{s3_path}' but got '{saved_path}'"
        )

    if not default_storage.exists(saved_path):
        raise RuntimeError(
            f"UPLOAD FAILED: File does not exist in S3 after save: {saved_path}"
        )

    # Remove local snapshot file now that upload is confirmed
    tmp_snapshot.unlink(missing_ok=True)

    logger.info("Manual SQLite backup uploaded to S3 at %s", saved_path)
    messages.success(request, f"Backup uploaded to S3: {saved_path}")
    return redirect(reverse("admin:index"))


def _resolve_sitemap_base_url(request: HttpRequest | None = None) -> str:
    configured = (getattr(settings, "SITE_BASE_URL", "") or "").strip()
    if configured:
        return configured
    if request is None:
        raise RuntimeError("SITE_BASE_URL is not configured; unable to refresh sitemap automatically")
    return request.build_absolute_uri('/')


@staff_member_required
def refresh_sitemap(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect(reverse("admin:index"))

    base_url = _resolve_sitemap_base_url(request)
    sitemap_path = regenerate_sitemap(base_url)

    messages.success(request, f"Sitemap refreshed at {sitemap_path}")
    return redirect(reverse("admin:index"))


@staff_member_required
def manual_restore(request: HttpRequest) -> HttpResponse:
    backups = _list_available_backups()
    if request.method == "POST":
        chosen_backup = request.POST.get("backup_path", "").strip()
        if not chosen_backup:
            messages.error(request, "Select a backup to restore.")
        else:
            try:
                _restore_backup(chosen_backup)
            except Exception as exc:  # Crash loudly only after logging
                logger.exception("Manual restore failed for %s", chosen_backup)
                messages.error(request, f"Restore failed: {exc}")
            else:
                messages.success(
                    request,
                    (
                        f"Restored backup: {chosen_backup}. "
                        "Public traffic stays in maintenance mode until Django is restarted cleanly."
                    ),
                )
                return redirect(reverse("admin:index"))

    context = admin.site.each_context(request)
    context.update({
        "backup_entries": backups,
        "selected_backup": request.POST.get("backup_path", ""),
        "title": "Restore Manual Backup",
    })
    return TemplateResponse(request, "admin/manual_restore.html", context)


def _build_sqlite_snapshot(db_path: Path) -> Path:
    """Copy SQLite DB to a temp file on the DB's filesystem and return its path.

    Rationale: Using the default system temp (often the container rootfs) can
    run out of space. Writing the snapshot next to the live DB keeps I/O on the
    data volume, which typically has more space. We also disable journaling and
    use in-memory temp storage for the destination connection to reduce extra
    disk usage during the copy.
    """
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    fd, tmp_path_str = tempfile.mkstemp(suffix=".sqlite3", dir=str(db_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_path_str)

    try:
        dest = sqlite3.connect(tmp_path_str)
        try:
            # Minimize extra disk usage for the throwaway copy
            with dest:
                dest.execute("PRAGMA journal_mode=OFF")
                dest.execute("PRAGMA synchronous=OFF")
                dest.execute("PRAGMA temp_store=MEMORY")
            source.backup(dest)
        except Exception:
            # Best-effort cleanup of partial snapshot on failure
            try:
                dest.close()
            finally:
                tmp_path.unlink(missing_ok=True)
            raise
        finally:
            dest.close()
        # Return the on-disk snapshot path to the caller for streaming upload
    finally:
        source.close()
    return tmp_path


def _list_available_backups() -> List[Dict[str, object]]:
    try:
        _, filenames = default_storage.listdir(BACKUP_PREFIX)
    except FileNotFoundError:
        return []

    entries: List[Dict[str, object]] = []
    for name in filenames:
        path = _backup_prefix_path(name)
        if "/" in name:
            logger.warning("Skipping nested backup entry: %s", path)
            continue
        try:
            size_bytes = default_storage.size(path)
            modified = default_storage.get_modified_time(path)
        except Exception as exc:
            logger.exception("Failed to read metadata for %s", path)
            raise RuntimeError(f"Unable to read metadata for {path}: {exc}") from exc

        entries.append(
            {
                "path": path,
                "name": name,
                "size_bytes": size_bytes,
                "modified": modified,
            }
        )

    return sorted(entries, key=lambda item: item["modified"], reverse=True)


def _restore_backup(s3_path: str) -> None:
    if not s3_path.startswith(f"{BACKUP_PREFIX}/"):
        raise RuntimeError("Invalid backup path: outside manual backups directory")

    if ".." in s3_path:
        raise RuntimeError("Invalid backup path: traversal detected")

    if not default_storage.exists(s3_path):
        raise RuntimeError(f"Backup not found: {s3_path}")

    temp_path = _download_backup_to_tempfile(s3_path)
    try:
        validate_sqlite_database(temp_path)
        _swap_in_backup(temp_path, s3_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _download_backup_to_tempfile(s3_path: str) -> Path:
    db_settings = settings.DATABASES["default"]
    db_path = Path(db_settings.get("NAME"))
    restore_dir = db_path.parent
    restore_dir.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        suffix=".sqlite3",
        prefix=f".{db_path.stem}_restore_",
        dir=str(restore_dir),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)

    with default_storage.open(s3_path, "rb") as remote, tmp_path.open("wb") as local:
        for chunk in iter(lambda: remote.read(1024 * 1024), b""):
            if not chunk:
                break
            local.write(chunk)

    return tmp_path


def _swap_in_backup(temp_path: Path, s3_path: str) -> None:
    db_settings = settings.DATABASES["default"]
    engine = db_settings.get("ENGINE")
    if engine != "django.db.backends.sqlite3":
        raise RuntimeError(
            f"MANUAL RESTORE ONLY SUPPORTS SQLITE: configured engine is {engine}"
        )

    db_path = Path(db_settings.get("NAME"))
    if not db_path.exists():
        raise RuntimeError(f"SQLITE DB MISSING: expected file at {db_path}")

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    pre_restore_path = db_path.with_suffix(f".pre_restore_{timestamp}.sqlite3")

    with maintenance_lock("manual restore in progress") as maintenance_state:
        connections.close_all()
        logger.info("Renaming active DB %s to %s", db_path, pre_restore_path)
        db_path.replace(pre_restore_path)
        try:
            logger.info("Installing backup from %s", s3_path)
            temp_path.replace(db_path)
            summary = validate_sqlite_database(db_path)
            mark_pending_restore_restart(s3_path)
            maintenance_state["clear_on_exit"] = False
            logger.warning(
                "Manual restore installed %s and kept maintenance mode active until restart; homepage=%s (%s)",
                s3_path,
                summary.homepage_id,
                summary.homepage_title,
            )
        except Exception:
            logger.exception("Failed to install backup, restoring original DB")
            clear_pending_restore_restart()
            if pre_restore_path.exists():
                pre_restore_path.replace(db_path)
            raise
