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
CODE_SEARCH_PAGE_SIZE = 100
CODE_SEARCH_CONTEXT_CHARS = 140
PAGE_CODE_SEARCH_PAGE_PARAM = "page_results_page"
SITE_PAGE_CODE_SEARCH_PAGE_PARAM = "site_page_results_page"

PAGE_CODE_SEARCH_FIELDS = (
    ("content_md", "Markdown source"),
    ("content_html", "Generated HTML"),
    ("original_tiki", "Original Tiki"),
    ("notes", "Internal notes"),
    ("aliases", "Legacy aliases"),
    ("front_matter", "Front matter"),
    ("meta_description", "Meta description"),
)
PAGE_CODE_SEARCH_EXCLUDE_FIELDS = (
    *PAGE_CODE_SEARCH_FIELDS,
    ("title", "Title"),
    ("slug", "Slug"),
    ("content_text", "Plain text"),
)
SITE_PAGE_CODE_SEARCH_FIELDS = (
    ("content_md", "Markdown source"),
    ("content_html", "Generated HTML"),
    ("meta_description", "Meta description"),
)
SITE_PAGE_CODE_SEARCH_EXCLUDE_FIELDS = (
    *SITE_PAGE_CODE_SEARCH_FIELDS,
    ("title", "Title"),
    ("slug", "Slug"),
    ("content_text", "Plain text"),
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
    exclude_q = forms.CharField(
        label="Exclude text",
        required=False,
        max_length=500,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Optional text, such as fluoride or -fluoride",
                "size": 64,
            }
        ),
    )

    def clean_exclude_q(self) -> str:
        exclude_query = self.cleaned_data["exclude_q"]
        if exclude_query.startswith("-"):
            exclude_query = exclude_query[1:].strip()
        if exclude_query and len(exclude_query) < 2:
            raise forms.ValidationError("Exclude text must be at least 2 characters.")
        return exclude_query


def _backup_prefix_path(filename: str) -> str:
    return f"{BACKUP_PREFIX}/{filename}"


def _requested_page_number(request: HttpRequest, page_param: str) -> int:
    assert page_param, "page_param must not be empty"

    raw_page_number = request.GET.get(page_param, "1")
    try:
        page_number = int(raw_page_number)
    except ValueError:
        return 1
    if page_number < 1:
        return 1
    return page_number


def _code_search_page_url(request: HttpRequest, page_param: str, page_number: int) -> str:
    assert page_param, "page_param must not be empty"
    assert page_number > 0, f"page_number must be positive, got {page_number}"

    query_params = request.GET.copy()
    query_params[page_param] = str(page_number)
    return f"?{query_params.urlencode()}"


def _code_search_pagination(total_count: int, requested_page: int) -> dict[str, object]:
    assert total_count >= 0, f"total_count must not be negative, got {total_count}"
    assert requested_page > 0, f"requested_page must be positive, got {requested_page}"

    if total_count == 0:
        return {
            "total_count": 0,
            "total_pages": 0,
            "current_page": 1,
            "offset": 0,
            "start_index": 0,
            "end_index": 0,
            "has_previous": False,
            "has_next": False,
            "previous_page_number": 0,
            "next_page_number": 0,
        }

    total_pages = (total_count + CODE_SEARCH_PAGE_SIZE - 1) // CODE_SEARCH_PAGE_SIZE
    current_page = min(requested_page, total_pages)
    offset = (current_page - 1) * CODE_SEARCH_PAGE_SIZE
    end_index = min(offset + CODE_SEARCH_PAGE_SIZE, total_count)
    return {
        "total_count": total_count,
        "total_pages": total_pages,
        "current_page": current_page,
        "offset": offset,
        "start_index": offset + 1,
        "end_index": end_index,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
        "previous_page_number": current_page - 1,
        "next_page_number": current_page + 1,
    }


def _code_search_pagination_urls(
    request: HttpRequest,
    page_param: str,
    pagination: dict[str, object],
) -> dict[str, object]:
    assert page_param, "page_param must not be empty"

    previous_url = ""
    next_url = ""
    if pagination["has_previous"]:
        previous_url = _code_search_page_url(
            request,
            page_param,
            pagination["previous_page_number"],
        )
    if pagination["has_next"]:
        next_url = _code_search_page_url(
            request,
            page_param,
            pagination["next_page_number"],
        )

    return {
        **pagination,
        "previous_url": previous_url,
        "next_url": next_url,
    }


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


def _filtered_code_search_queryset(
    queryset,
    field_specs: tuple[tuple[str, str], ...],
    exclude_field_specs: tuple[tuple[str, str], ...],
    query: str,
    exclude_query: str,
):
    assert query, "Code search query must not be empty"
    assert isinstance(exclude_query, str), f"exclude_query must be str, got {type(exclude_query)}"

    filtered_queryset = queryset.filter(_build_code_search_filter(field_specs, query))
    if exclude_query:
        filtered_queryset = filtered_queryset.exclude(
            _build_code_search_filter(exclude_field_specs, exclude_query)
        )
    return filtered_queryset


def _search_page_code(
    query: str,
    exclude_query: str,
    requested_page: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    assert requested_page > 0, f"requested_page must be positive, got {requested_page}"

    field_names = [field_name for field_name, _label in PAGE_CODE_SEARCH_FIELDS]
    filtered_pages = _filtered_code_search_queryset(
        Page.objects,
        PAGE_CODE_SEARCH_FIELDS,
        PAGE_CODE_SEARCH_EXCLUDE_FIELDS,
        query,
        exclude_query,
    )
    total_count = filtered_pages.count()
    pagination = _code_search_pagination(total_count, requested_page)
    offset = pagination["offset"]
    pages = list(
        filtered_pages.only("id", "title", "slug", "status", "modified_date", *field_names)
        .order_by("-modified_date", "pk")[offset : offset + CODE_SEARCH_PAGE_SIZE]
    )

    results = []
    for page in pages:
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
    return results, pagination


def _search_site_page_code(
    query: str,
    exclude_query: str,
    requested_page: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    assert requested_page > 0, f"requested_page must be positive, got {requested_page}"

    field_names = [field_name for field_name, _label in SITE_PAGE_CODE_SEARCH_FIELDS]
    filtered_site_pages = _filtered_code_search_queryset(
        SitePage.objects,
        SITE_PAGE_CODE_SEARCH_FIELDS,
        SITE_PAGE_CODE_SEARCH_EXCLUDE_FIELDS,
        query,
        exclude_query,
    )
    total_count = filtered_site_pages.count()
    pagination = _code_search_pagination(total_count, requested_page)
    offset = pagination["offset"]
    site_pages = list(
        filtered_site_pages.only("id", "title", "slug", "page_type", "is_published", "modified_date", *field_names)
        .order_by("-modified_date", "pk")[offset : offset + CODE_SEARCH_PAGE_SIZE]
    )

    results = []
    for site_page in site_pages:
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
    return results, pagination


@staff_member_required
def code_search(request: HttpRequest) -> HttpResponse:
    has_searched = "q" in request.GET
    form = AdminCodeSearchForm(request.GET if has_searched else None)

    page_results = []
    site_page_results = []
    page_pagination = _code_search_pagination(0, 1)
    site_page_pagination = _code_search_pagination(0, 1)
    if has_searched and form.is_valid():
        query = form.cleaned_data["q"]
        exclude_query = form.cleaned_data["exclude_q"]
        page_results, page_pagination = _search_page_code(
            query,
            exclude_query,
            _requested_page_number(request, PAGE_CODE_SEARCH_PAGE_PARAM),
        )
        site_page_results, site_page_pagination = _search_site_page_code(
            query,
            exclude_query,
            _requested_page_number(request, SITE_PAGE_CODE_SEARCH_PAGE_PARAM),
        )

    page_pagination = _code_search_pagination_urls(
        request,
        PAGE_CODE_SEARCH_PAGE_PARAM,
        page_pagination,
    )
    site_page_pagination = _code_search_pagination_urls(
        request,
        SITE_PAGE_CODE_SEARCH_PAGE_PARAM,
        site_page_pagination,
    )

    context = admin.site.each_context(request)
    context.update(
        {
            "title": "Code Search",
            "form": form,
            "has_searched": has_searched,
            "page_results": page_results,
            "site_page_results": site_page_results,
            "page_pagination": page_pagination,
            "site_page_pagination": site_page_pagination,
            "result_count": page_pagination["total_count"] + site_page_pagination["total_count"],
            "result_page_size": CODE_SEARCH_PAGE_SIZE,
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
