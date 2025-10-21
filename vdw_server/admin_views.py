"""Admin-only views for operational tooling."""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


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
