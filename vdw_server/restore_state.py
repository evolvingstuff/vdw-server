"""Shared SQLite restore validation and maintenance-state helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone

MAINTENANCE_LOCK_FILENAME = "maintenance.lock"
PENDING_RESTORE_FILENAME = "pending_restore_restart.lock"
REQUIRED_SQLITE_TABLES = {
    "django_migrations",
    "pages_page",
    "posts_post",
}


@dataclass(frozen=True)
class RestoreValidationSummary:
    homepage_id: int
    homepage_title: str
    table_names: tuple[str, ...]


def maintenance_lock_path() -> Path:
    return _tmp_dir() / MAINTENANCE_LOCK_FILENAME


def pending_restore_marker_path() -> Path:
    return _tmp_dir() / PENDING_RESTORE_FILENAME


def validate_sqlite_database(db_path: Path) -> RestoreValidationSummary:
    assert isinstance(db_path, Path), f"db_path must be Path, got {type(db_path)}"
    if not db_path.exists():
        raise RuntimeError(f"SQLITE DB MISSING: expected file at {db_path}")

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity_row is None:
            raise RuntimeError("SQLITE INTEGRITY CHECK RETURNED NO RESULT")
        integrity_result = str(integrity_row[0]).strip().lower()
        if integrity_result != "ok":
            raise RuntimeError(f"SQLITE INTEGRITY CHECK FAILED: {integrity_row[0]}")

        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        table_names = tuple(sorted(row[0] for row in table_rows))
        missing_tables = sorted(REQUIRED_SQLITE_TABLES.difference(table_names))
        if missing_tables:
            raise RuntimeError(
                "RESTORED SQLITE DB IS MISSING TABLES: " + ", ".join(missing_tables)
            )

        homepage_rows = connection.execute(
            """
            SELECT id, title, slug, is_published
            FROM pages_page
            WHERE page_type = 'homepage'
            """
        ).fetchall()
        if len(homepage_rows) != 1:
            raise RuntimeError(
                f"HOMEPAGE INVARIANT FAILED: expected 1 homepage row, found {len(homepage_rows)}"
            )

        homepage_id, homepage_title, homepage_slug, homepage_published = homepage_rows[0]
        if homepage_slug != "home":
            raise RuntimeError(
                f"HOMEPAGE SLUG INVARIANT FAILED: expected 'home', got {homepage_slug!r}"
            )
        if homepage_published != 1:
            raise RuntimeError("HOMEPAGE PUBLISH INVARIANT FAILED: homepage is not published")

        return RestoreValidationSummary(
            homepage_id=homepage_id,
            homepage_title=homepage_title,
            table_names=table_names,
        )
    finally:
        connection.close()


@contextmanager
def maintenance_lock(reason: str):
    sentinel = maintenance_lock_path()
    if sentinel.exists():
        raise RuntimeError("Maintenance already in progress")

    sentinel.write_text(
        f"{timezone.now().isoformat()}\n{reason}\n",
        encoding="utf-8",
    )
    state = {"clear_on_exit": True}
    try:
        yield state
    finally:
        if state["clear_on_exit"] and sentinel.exists():
            sentinel.unlink(missing_ok=True)


def mark_pending_restore_restart(source_path: str) -> Path:
    marker = pending_restore_marker_path()
    marker.write_text(
        f"{timezone.now().isoformat()}\n{source_path}\n",
        encoding="utf-8",
    )
    return marker


def clear_pending_restore_restart() -> None:
    pending_restore_marker_path().unlink(missing_ok=True)


def finalize_pending_restore() -> RestoreValidationSummary | None:
    marker = pending_restore_marker_path()
    if not marker.exists():
        return None

    db_path = Path(settings.DATABASES["default"]["NAME"])
    summary = validate_sqlite_database(db_path)
    clear_pending_restore_restart()
    maintenance_lock_path().unlink(missing_ok=True)
    return summary
def _tmp_dir() -> Path:
    tmp_dir = Path(settings.BASE_DIR) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir
