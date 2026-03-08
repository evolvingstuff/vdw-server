import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from vdw_server.admin_views import _swap_in_backup
from vdw_server.restore_state import (
    finalize_pending_restore,
    maintenance_lock_path,
    pending_restore_marker_path,
    validate_sqlite_database,
)


def _write_sqlite_db(
    path: Path,
    *,
    homepage_title: str = "Home",
    include_homepage: bool = True,
    homepage_published: bool = True,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)"
        )
        connection.execute(
            """
            CREATE TABLE pages_page (
                id INTEGER PRIMARY KEY,
                title TEXT,
                slug TEXT,
                page_type TEXT,
                is_published INTEGER,
                modified_date TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE posts_post (
                id INTEGER PRIMARY KEY,
                title TEXT,
                slug TEXT,
                status TEXT,
                created_date TEXT
            )
            """
        )
        if include_homepage:
            connection.execute(
                """
                INSERT INTO pages_page (id, title, slug, page_type, is_published, modified_date)
                VALUES (?, ?, 'home', 'homepage', ?, '2026-03-08T00:00:00Z')
                """,
                [1, homepage_title, 1 if homepage_published else 0],
            )
        connection.execute(
            """
            INSERT INTO posts_post (id, title, slug, status, created_date)
            VALUES (1, 'Published page', 'published-page', 'published', '2026-03-08T00:00:00Z')
            """
        )
        connection.commit()
    finally:
        connection.close()


class RestoreStateTests(SimpleTestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base_dir = Path(self.tempdir.name)

    def test_validate_sqlite_database_accepts_expected_schema_and_homepage(self):
        db_path = self.base_dir / "valid.sqlite3"
        _write_sqlite_db(db_path, homepage_title="Restored Home")

        summary = validate_sqlite_database(db_path)

        self.assertEqual(summary.homepage_title, "Restored Home")
        self.assertIn("pages_page", summary.table_names)
        self.assertIn("posts_post", summary.table_names)

    def test_finalize_pending_restore_clears_pending_markers(self):
        db_path = self.base_dir / "db.sqlite3"
        _write_sqlite_db(db_path, homepage_title="Fresh Home")

        with override_settings(
            BASE_DIR=self.base_dir,
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(db_path)}},
        ):
            maintenance_lock_path().write_text("locked\n", encoding="utf-8")
            pending_restore_marker_path().write_text("pending\n", encoding="utf-8")

            summary = finalize_pending_restore()

            self.assertEqual(summary.homepage_title, "Fresh Home")
            self.assertFalse(maintenance_lock_path().exists())
            self.assertFalse(pending_restore_marker_path().exists())

    def test_swap_in_backup_preserves_maintenance_until_restart(self):
        current_db = self.base_dir / "db.sqlite3"
        incoming_db = self.base_dir / "incoming.sqlite3"
        _write_sqlite_db(current_db, homepage_title="Old Home")
        _write_sqlite_db(incoming_db, homepage_title="New Home")

        with override_settings(
            BASE_DIR=self.base_dir,
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(current_db)}},
        ):
            with patch("vdw_server.admin_views.connections.close_all"):
                _swap_in_backup(incoming_db, "db_backups/manual_backups/backup.sqlite3")

            summary = validate_sqlite_database(current_db)

            self.assertEqual(summary.homepage_title, "New Home")
            self.assertTrue(maintenance_lock_path().exists())
            self.assertTrue(pending_restore_marker_path().exists())

    def test_swap_in_backup_rolls_back_invalid_database(self):
        current_db = self.base_dir / "db.sqlite3"
        incoming_db = self.base_dir / "incoming.sqlite3"
        _write_sqlite_db(current_db, homepage_title="Original Home")
        _write_sqlite_db(incoming_db, include_homepage=False)

        with override_settings(
            BASE_DIR=self.base_dir,
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(current_db)}},
        ):
            with patch("vdw_server.admin_views.connections.close_all"):
                with self.assertRaisesRegex(RuntimeError, "HOMEPAGE INVARIANT FAILED"):
                    _swap_in_backup(incoming_db, "db_backups/manual_backups/backup.sqlite3")

            summary = validate_sqlite_database(current_db)

            self.assertEqual(summary.homepage_title, "Original Home")
            self.assertFalse(maintenance_lock_path().exists())
            self.assertFalse(pending_restore_marker_path().exists())
