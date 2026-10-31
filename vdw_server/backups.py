"""SQLite backups to S3: manual (admin button) and automatic (backup scheduler), plus cleanup.

Automatic backups (run_backup_scheduler checks every BACKUP_CHECK_SECONDS):
  - after an edit, once BACKUP_QUIET_MINUTES have passed with no further edits, and
  - at least once per day (BACKUP_TIME_ZONE) even when nothing changed, so a missing day
    in the backup list means something failed rather than "no edits".
  Both wait for BACKUP_QUIET_MINUTES without edits, so a backup never interrupts editing.
  "Edits" are admin saves/deletes (the admin log) and page or tag changes made anywhere
  (the topic index marker file).

Cleanup after every backup:
  - keep every backup from the last BACKUP_KEEP_ALL_DAYS days,
  - before that keep only the last backup of each day,
  - delete backups older than BACKUP_KEEP_DAILY_DAYS days.
  The newest backup is never deleted, and files with unrecognized names are left alone.

All backups go to BACKUP_PREFIX as backup_YYYYmmdd_HHMMSS[_auto].sqlite3 (UTC time the
snapshot started), so they sort by name and all appear on the admin Restore page.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Iterator, List, Optional, Sequence
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.utils import timezone

logger = logging.getLogger(__name__)

BACKUP_PREFIX = "db_backups/manual_backups"
SNAPSHOT_PREFIX = ".backup_snapshot_"
LOCK_FILENAME = ".backup.lock"
_NAME_RE = re.compile(r"^backup_(\d{8}_\d{6})(_auto)?\.sqlite3$")
_NAME_TIME_FORMAT = "%Y%m%d_%H%M%S"


class BackupInProgress(RuntimeError):
    """Another backup or restore holds the lock."""


@dataclass(frozen=True)
class StoredBackup:
    name: str
    taken_at: datetime  # UTC

    @property
    def path(self) -> str:
        return f"{BACKUP_PREFIX}/{self.name}"


def backup_time_zone() -> ZoneInfo:
    return ZoneInfo(settings.BACKUP_TIME_ZONE)


def _db_path() -> Path:
    db_settings = settings.DATABASES["default"]
    engine = db_settings.get("ENGINE")
    if engine != "django.db.backends.sqlite3":
        raise RuntimeError(f"BACKUPS ONLY SUPPORT SQLITE: configured engine is {engine}")
    return Path(db_settings.get("NAME"))


# --- Locking -------------------------------------------------------------------------------

@contextmanager
def backup_lock() -> Iterator[None]:
    """Only one backup or restore at a time, across processes and containers.

    The lock file sits next to the database, on the data volume both containers mount.
    Raises BackupInProgress instead of waiting.
    """
    lock_path = _db_path().parent / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupInProgress("A backup or restore is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# --- Creating a backup ---------------------------------------------------------------------

def backup_name(taken_at: datetime, automatic: bool) -> str:
    stamp = taken_at.astimezone(dt_timezone.utc).strftime(_NAME_TIME_FORMAT)
    return f"backup_{stamp}{'_auto' if automatic else ''}.sqlite3"


def create_backup(automatic: bool) -> StoredBackup:
    """Snapshot the live database and upload it to S3. Caller must hold backup_lock()."""
    db_path = _db_path()
    assert db_path.exists(), f"SQLITE DB MISSING: expected file at {db_path}"

    storage_class = default_storage.__class__.__name__
    if "S3" not in storage_class:
        raise RuntimeError(f"WRONG STORAGE BACKEND: Using {storage_class} - not an S3 storage backend")

    # Snapshots left behind by an interrupted backup (we hold the lock, so none is in use)
    for stale in db_path.parent.glob(f"{SNAPSHOT_PREFIX}*"):
        logger.warning("Removing leftover backup snapshot %s", stale)
        stale.unlink(missing_ok=True)

    # Ensure enough free space on the DB filesystem for the temporary snapshot.
    # SQLite backup needs about the DB size plus a little slack; it still raises on ENOSPC.
    usage = shutil.disk_usage(str(db_path.parent))
    required = db_path.stat().st_size + 16 * 1024 * 1024
    if usage.free < required:
        raise RuntimeError(f"INSUFFICIENT DISK: free={usage.free}B required={required}B on {db_path.parent}")

    taken_at = timezone.now()
    backup = StoredBackup(backup_name(taken_at, automatic), taken_at.replace(microsecond=0))
    tmp_snapshot = build_sqlite_snapshot(db_path)
    try:
        # Stream upload from file (avoid loading entire DB into memory)
        with tmp_snapshot.open("rb") as fp:
            saved_path = default_storage.save(backup.path, File(fp))
        if saved_path != backup.path:
            raise RuntimeError(f"S3 PATH MISMATCH: Requested '{backup.path}' but got '{saved_path}'")
        if not default_storage.exists(saved_path):
            raise RuntimeError(f"UPLOAD FAILED: File does not exist in S3 after save: {saved_path}")
    finally:
        tmp_snapshot.unlink(missing_ok=True)

    logger.info("%s SQLite backup uploaded to S3 at %s", "Automatic" if automatic else "Manual", backup.path)
    return backup


def build_sqlite_snapshot(db_path: Path) -> Path:
    """Create a consistent snapshot of the live SQLite DB next to it and return its path.

    Rationale: Using the default system temp (often the container rootfs) can
    run out of space. Writing the snapshot next to the live DB keeps I/O on the
    data volume, which typically has more space. We also disable journaling and
    use in-memory temp storage for the destination connection to reduce extra
    disk usage during the copy.
    """
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    fd, tmp_path_str = tempfile.mkstemp(prefix=SNAPSHOT_PREFIX, suffix=".sqlite3", dir=str(db_path.parent))
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
    finally:
        source.close()
    return tmp_path


# --- Listing and cleanup -------------------------------------------------------------------

def parse_backup_name(name: str) -> Optional[StoredBackup]:
    match = _NAME_RE.match(name)
    if not match:
        return None
    taken_at = datetime.strptime(match.group(1), _NAME_TIME_FORMAT).replace(tzinfo=dt_timezone.utc)
    return StoredBackup(name, taken_at)


def list_backups() -> List[StoredBackup]:
    """Backups in S3, oldest first (one LIST request). Unrecognized names are skipped."""
    try:
        _, filenames = default_storage.listdir(BACKUP_PREFIX)
    except FileNotFoundError:
        return []
    backups = []
    for name in filenames:
        backup = parse_backup_name(name)
        if backup is None:
            logger.warning("Ignoring unrecognized file in backups folder: %s", name)
            continue
        backups.append(backup)
    return sorted(backups, key=lambda b: b.taken_at)


def backups_to_delete(
    backups: Sequence[StoredBackup],
    now: datetime,
    tz: ZoneInfo,
    keep_all_days: int,
    keep_daily_days: int,
) -> List[StoredBackup]:
    """Which backups the retention rules remove (see module docstring)."""
    assert 0 < keep_all_days <= keep_daily_days, "need 0 < keep_all_days <= keep_daily_days"
    if not backups:
        return []
    today = now.astimezone(tz).date()
    newest = max(backups, key=lambda b: b.taken_at)
    last_of_day = {}
    for backup in sorted(backups, key=lambda b: b.taken_at):
        last_of_day[backup.taken_at.astimezone(tz).date()] = backup

    doomed = []
    for backup in backups:
        if backup is newest:
            continue
        day = backup.taken_at.astimezone(tz).date()
        age_days = (today - day).days
        if age_days < keep_all_days:
            continue
        if age_days < keep_daily_days and last_of_day[day] is backup:
            continue
        doomed.append(backup)
    return sorted(doomed, key=lambda b: b.taken_at)


def prune_backups(now: Optional[datetime] = None) -> List[StoredBackup]:
    """Delete backups the retention rules no longer keep; returns what was deleted."""
    doomed = backups_to_delete(
        list_backups(),
        now or timezone.now(),
        backup_time_zone(),
        keep_all_days=settings.BACKUP_KEEP_ALL_DAYS,
        keep_daily_days=settings.BACKUP_KEEP_DAILY_DAYS,
    )
    for backup in doomed:
        default_storage.delete(backup.path)
    if doomed:
        logger.info("Backup cleanup deleted %d old backup(s): %s ... %s", len(doomed), doomed[0].name, doomed[-1].name)
    return doomed


def create_backup_and_prune(automatic: bool) -> tuple[StoredBackup, Optional[Exception]]:
    """Back up under the lock, then clean up. A cleanup failure doesn't fail the backup."""
    with backup_lock():
        backup = create_backup(automatic)
        try:
            prune_backups()
        except Exception as exc:
            logger.exception("Backup cleanup failed after %s", backup.name)
            return backup, exc
    return backup, None


# --- Automatic backups ---------------------------------------------------------------------

def last_edit_time() -> Optional[datetime]:
    """Most recent edit: admin log entries, or page/tag changes made anywhere."""
    from django.contrib.admin.models import LogEntry

    from tags.services import _marker_file

    times = []
    admin_time = LogEntry.objects.order_by("-action_time").values_list("action_time", flat=True).first()
    if admin_time:
        times.append(admin_time)
    try:
        times.append(datetime.fromtimestamp(_marker_file().stat().st_mtime, tz=dt_timezone.utc))
    except FileNotFoundError:
        pass
    return max(times) if times else None


def auto_backup_reason(
    now: datetime,
    last_edit: Optional[datetime],
    newest_backup: Optional[datetime],
    tz: ZoneInfo,
    quiet: timedelta,
) -> str:
    """Why an automatic backup should run now ('edited', 'daily'), or why not ('', 'editing')."""
    edited_since_backup = last_edit is not None and (newest_backup is None or last_edit > newest_backup)
    no_backup_today = newest_backup is None or newest_backup.astimezone(tz).date() != now.astimezone(tz).date()
    if not edited_since_backup and not no_backup_today:
        return ""
    if last_edit is not None and now - last_edit < quiet:
        return "editing"
    return "edited" if edited_since_backup else "daily"


class AutoBackupScheduler:
    """One check per call to check(). Remembers the newest backup time between checks and
    re-reads the S3 list only when a backup looks due (a manual backup may have run)."""

    def __init__(self):
        self.newest_backup: Optional[datetime] = None

    def _reason(self, now: datetime, last_edit: Optional[datetime]) -> str:
        return auto_backup_reason(
            now,
            last_edit,
            self.newest_backup,
            backup_time_zone(),
            timedelta(minutes=settings.BACKUP_QUIET_MINUTES),
        )

    def _refresh_newest_backup(self) -> None:
        backups = list_backups()
        self.newest_backup = backups[-1].taken_at if backups else None

    def check(self, now: Optional[datetime] = None) -> str:
        """Run a backup if one is due. Returns what happened, for logging."""
        now = now or timezone.now()
        last_edit = last_edit_time()
        reason = self._reason(now, last_edit)
        if reason in ("edited", "daily"):
            self._refresh_newest_backup()
            reason = self._reason(now, last_edit)
        if not reason:
            return "up to date"
        if reason == "editing":
            return f"postponed: last edit at {last_edit:%Y-%m-%d %H:%M:%S} UTC"

        try:
            backup, cleanup_error = create_backup_and_prune(automatic=True)
        except BackupInProgress:
            return "skipped: another backup or restore is running"
        self.newest_backup = backup.taken_at
        result = f"backed up ({reason}): {backup.name}"
        if cleanup_error:
            result += f" (cleanup failed: {cleanup_error})"
        return result
