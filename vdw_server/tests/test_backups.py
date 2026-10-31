import os
import sqlite3
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from vdw_server import backups
from vdw_server.backups import (
    AutoBackupScheduler,
    BackupInProgress,
    StoredBackup,
    auto_backup_reason,
    backup_lock,
    backup_name,
    backups_to_delete,
    parse_backup_name,
)

PACIFIC = ZoneInfo('America/Los_Angeles')
QUIET = timedelta(minutes=30)


def _pacific(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=PACIFIC)


def _backup(when, automatic=True):
    return StoredBackup(backup_name(when, automatic), when.astimezone(dt_timezone.utc))


class BackupNameTests(SimpleTestCase):
    def test_round_trip_and_unknown_names(self):
        when = datetime(2026, 10, 31, 7, 5, 9, tzinfo=dt_timezone.utc)
        self.assertEqual(backup_name(when, automatic=True), 'backup_20261031_070509_auto.sqlite3')
        self.assertEqual(backup_name(when, automatic=False), 'backup_20261031_070509.sqlite3')
        self.assertEqual(parse_backup_name('backup_20261031_070509_auto.sqlite3').taken_at, when)
        self.assertEqual(parse_backup_name('backup_20261031_070509.sqlite3').taken_at, when)
        self.assertIsNone(parse_backup_name('my-copy.sqlite3'))


class RetentionTests(SimpleTestCase):
    def _doomed(self, items, now):
        return {b.name for b in backups_to_delete(items, now, PACIFIC, keep_all_days=3, keep_daily_days=90)}

    def test_keeps_all_recent_then_last_of_each_day_then_nothing(self):
        now = _pacific(2026, 10, 31, 15)
        today = [_backup(_pacific(2026, 10, 31, h)) for h in (1, 9, 14)]
        two_days_ago = [_backup(_pacific(2026, 10, 29, h)) for h in (1, 23)]
        three_days_ago = [_backup(_pacific(2026, 10, 28, h)) for h in (0, 8, 22)]
        day_89 = [_backup(now - timedelta(days=89) + timedelta(hours=h)) for h in (-2, -1)]
        day_90 = [_backup(now - timedelta(days=90))]
        items = today + two_days_ago + three_days_ago + day_89 + day_90

        doomed = self._doomed(items, now)

        expected = {three_days_ago[0].name, three_days_ago[1].name, day_89[0].name, day_90[0].name}
        self.assertEqual(doomed, expected)

    def test_days_follow_the_backup_time_zone(self):
        now = _pacific(2026, 10, 31, 12)
        # 11pm and 11:30pm Pacific on Oct 20 are already Oct 21 in UTC; still one Pacific day.
        late = [_backup(_pacific(2026, 10, 20, 23)), _backup(_pacific(2026, 10, 20, 23, 30))]
        next_morning = _backup(_pacific(2026, 10, 21, 1))
        self.assertEqual(self._doomed([*late, next_morning], now), {late[0].name})

    def test_newest_backup_is_never_deleted(self):
        only = _backup(_pacific(2026, 1, 1))
        self.assertEqual(self._doomed([only, _backup(_pacific(2025, 12, 1))], _pacific(2026, 10, 31)),
                         {backup_name(_pacific(2025, 12, 1), True)})


class AutoBackupReasonTests(SimpleTestCase):
    now = _pacific(2026, 10, 31, 15)

    def reason(self, last_edit, newest_backup):
        return auto_backup_reason(self.now, last_edit, newest_backup, PACIFIC, QUIET)

    def test_backs_up_after_quiet_period_following_an_edit(self):
        self.assertEqual(self.reason(self.now - timedelta(minutes=31), self.now - timedelta(hours=2)), 'edited')

    def test_postpones_while_editing(self):
        self.assertEqual(self.reason(self.now - timedelta(minutes=10), self.now - timedelta(hours=2)), 'editing')

    def test_nothing_to_do_when_backed_up_since_last_edit_today(self):
        self.assertEqual(self.reason(self.now - timedelta(hours=3), self.now - timedelta(hours=2)), '')

    def test_daily_backup_even_without_edits(self):
        yesterday = self.now - timedelta(days=1)
        self.assertEqual(self.reason(yesterday - timedelta(hours=1), yesterday), 'daily')
        self.assertEqual(self.reason(None, None), 'daily')

    def test_daily_backup_still_waits_for_quiet(self):
        self.assertEqual(self.reason(self.now - timedelta(minutes=5), self.now - timedelta(days=1)), 'editing')


@override_settings(BACKUP_TIME_ZONE='America/Los_Angeles', BACKUP_QUIET_MINUTES=30)
class SchedulerTests(SimpleTestCase):
    now = _pacific(2026, 10, 31, 15)

    def test_only_lists_s3_when_a_backup_looks_due(self):
        scheduler = AutoBackupScheduler()
        scheduler.newest_backup = self.now - timedelta(hours=1)
        with patch.object(backups, 'last_edit_time', return_value=self.now - timedelta(hours=2)), \
                patch.object(backups, 'list_backups') as list_backups:
            self.assertEqual(scheduler.check(self.now), 'up to date')
        list_backups.assert_not_called()

    def test_manual_backup_made_elsewhere_counts(self):
        scheduler = AutoBackupScheduler()
        scheduler.newest_backup = self.now - timedelta(hours=5)
        manual = _backup(self.now - timedelta(minutes=20), automatic=False)
        with patch.object(backups, 'last_edit_time', return_value=self.now - timedelta(hours=1)), \
                patch.object(backups, 'list_backups', return_value=[manual]), \
                patch.object(backups, 'create_backup_and_prune') as create:
            self.assertEqual(scheduler.check(self.now), 'up to date')
        create.assert_not_called()

    def test_runs_backup_and_remembers_it(self):
        scheduler = AutoBackupScheduler()
        made = _backup(self.now)
        with patch.object(backups, 'last_edit_time', return_value=self.now - timedelta(hours=1)), \
                patch.object(backups, 'list_backups', return_value=[_backup(self.now - timedelta(hours=5))]), \
                patch.object(backups, 'create_backup_and_prune', return_value=(made, None)) as create:
            self.assertEqual(scheduler.check(self.now), f'backed up (edited): {made.name}')
            self.assertEqual(scheduler.check(self.now + timedelta(minutes=5)), 'up to date')
        create.assert_called_once_with(automatic=True)

    def test_postpones_without_backing_up(self):
        scheduler = AutoBackupScheduler()
        with patch.object(backups, 'last_edit_time', return_value=self.now - timedelta(minutes=3)), \
                patch.object(backups, 'list_backups', return_value=[_backup(self.now - timedelta(hours=5))]), \
                patch.object(backups, 'create_backup_and_prune') as create:
            self.assertTrue(scheduler.check(self.now).startswith('postponed'))
        create.assert_not_called()


class FakeS3Storage:
    def __init__(self, files=()):
        self.files = {name: b'' for name in files}

    def save(self, path, content):
        self.files[path.rsplit('/', 1)[-1]] = content.read()
        return path

    def exists(self, path):
        return path.rsplit('/', 1)[-1] in self.files

    def listdir(self, prefix):
        return [], list(self.files)

    def delete(self, path):
        del self.files[path.rsplit('/', 1)[-1]]


@override_settings(BACKUP_TIME_ZONE='America/Los_Angeles', BACKUP_KEEP_ALL_DAYS=3, BACKUP_KEEP_DAILY_DAYS=90)
class CreateBackupTests(SimpleTestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db_path = self.dir / 'db.sqlite3'
        connection = sqlite3.connect(self.db_path)
        connection.execute('CREATE TABLE t (x)')
        connection.execute("INSERT INTO t VALUES ('hello')")
        connection.commit()
        connection.close()
        patcher = patch.object(backups, '_db_path', return_value=self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uploads_snapshot_cleans_up_and_prunes(self):
        old = backup_name(datetime(2020, 1, 1, tzinfo=dt_timezone.utc), automatic=False)
        storage = FakeS3Storage([old, 'keep-me.sqlite3'])
        (self.dir / f'{backups.SNAPSHOT_PREFIX}leftover.sqlite3').write_bytes(b'x')

        with patch.object(backups, 'default_storage', storage):
            backup, cleanup_error = backups.create_backup_and_prune(automatic=True)

        self.assertIsNone(cleanup_error)
        self.assertTrue(backup.name.endswith('_auto.sqlite3'))
        self.assertEqual(set(storage.files), {backup.name, 'keep-me.sqlite3'})
        self.assertEqual(list(self.dir.glob(f'{backups.SNAPSHOT_PREFIX}*')), [])
        copy = self.dir / 'copy.sqlite3'
        copy.write_bytes(storage.files[backup.name])
        self.assertEqual(sqlite3.connect(copy).execute('SELECT x FROM t').fetchone(), ('hello',))

    def test_lock_rejects_a_second_backup(self):
        with backup_lock():
            with self.assertRaises(BackupInProgress):
                with backup_lock():
                    pass
        with backup_lock():  # released again
            pass


class LastEditTimeTests(TestCase):
    def test_uses_newest_of_admin_log_and_page_marker(self):
        user = get_user_model().objects.create_user('editor')
        entry = LogEntry.objects.create(user=user, action_flag=CHANGE, object_repr='x')
        admin_time = datetime(2026, 10, 30, 12, tzinfo=dt_timezone.utc)
        LogEntry.objects.filter(pk=entry.pk).update(action_time=admin_time)

        with TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'topic_index_version'
            with patch('tags.services._marker_file', return_value=marker):
                self.assertEqual(backups.last_edit_time(), admin_time)
                marker.write_text('1')
                later = admin_time + timedelta(hours=3)
                os.utime(marker, (later.timestamp(), later.timestamp()))
                self.assertEqual(backups.last_edit_time(), later)


@override_settings(ALLOWED_HOSTS=['*'])
class ManualBackupViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser('admin', 'a@example.com', 'pw')
        self.client.force_login(user)

    def test_reports_success(self):
        made = _backup(_pacific(2026, 10, 31), automatic=False)
        with patch('vdw_server.admin_views.create_backup_and_prune', return_value=(made, None)) as create:
            response = self.client.post('/admin/manual-backup/', follow=True)
        create.assert_called_once_with(automatic=False)
        self.assertContains(response, f'Backup uploaded to S3: {made.path}')

    def test_reports_busy(self):
        with patch('vdw_server.admin_views.create_backup_and_prune', side_effect=BackupInProgress('busy')):
            response = self.client.post('/admin/manual-backup/', follow=True)
        self.assertContains(response, 'Another backup or restore is running')
