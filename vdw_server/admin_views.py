"""Admin-only views for operational tooling."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connections
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "db_backups/manual_backups"


def _backup_prefix_path(filename: str) -> str:
    return f"{BACKUP_PREFIX}/{filename}"


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

    backup_bytes = _build_sqlite_snapshot(db_path)

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    s3_path = f"db_backups/manual_backups/backup_{timestamp}.sqlite3"

    storage_class = default_storage.__class__.__name__
    if "S3" not in storage_class:
        raise RuntimeError(
            f"WRONG STORAGE BACKEND: Using {storage_class} - not an S3 storage backend"
        )

    saved_path = default_storage.save(s3_path, ContentFile(backup_bytes))
    if saved_path != s3_path:
        raise RuntimeError(
            f"S3 PATH MISMATCH: Requested '{s3_path}' but got '{saved_path}'"
        )

    if not default_storage.exists(saved_path):
        raise RuntimeError(
            f"UPLOAD FAILED: File does not exist in S3 after save: {saved_path}"
        )

    logger.info("Manual SQLite backup uploaded to S3 at %s", saved_path)
    messages.success(request, f"Backup uploaded to S3: {saved_path}")
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
                messages.success(request, f"Restored backup: {chosen_backup}")
                return redirect(reverse("admin:index"))

    context = admin.site.each_context(request)
    context.update({
        "backup_entries": backups,
        "selected_backup": request.POST.get("backup_path", ""),
        "title": "Restore Manual Backup",
    })
    return TemplateResponse(request, "admin/manual_restore.html", context)


def _build_sqlite_snapshot(db_path: Path) -> bytes:
    """Copy SQLite DB to a temp file and return its bytes."""
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    fd, tmp_path_str = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    tmp_path = Path(tmp_path_str)

    try:
        dest = sqlite3.connect(tmp_path_str)
        try:
            source.backup(dest)
        finally:
            dest.close()
        backup_bytes = tmp_path.read_bytes()
    finally:
        source.close()
        tmp_path.unlink(missing_ok=True)

    return backup_bytes


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
        _swap_in_backup(temp_path, s3_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _download_backup_to_tempfile(s3_path: str) -> Path:
    fd, tmp_name = tempfile.mkstemp(suffix=".sqlite3")
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

    with _maintenance_lock("manual restore in progress"):
        connections.close_all()
        logger.info("Renaming active DB %s to %s", db_path, pre_restore_path)
        db_path.replace(pre_restore_path)
        try:
            logger.info("Installing backup from %s", s3_path)
            temp_path.replace(db_path)
        except Exception:
            logger.exception("Failed to install backup, restoring original DB")
            if pre_restore_path.exists():
                pre_restore_path.replace(db_path)
            raise


@contextmanager
def _maintenance_lock(reason: str):
    tmp_dir = Path(settings.BASE_DIR) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_dir / "maintenance.lock"
    if sentinel.exists():
        raise RuntimeError("Maintenance already in progress")

    sentinel.write_text(
        f"{timezone.now().isoformat()}\n{reason}\n",
        encoding="utf-8",
    )
    try:
        yield
    finally:
        sentinel.unlink(missing_ok=True)
