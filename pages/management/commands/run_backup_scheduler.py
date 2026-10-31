import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections

from vdw_server.backups import AutoBackupScheduler

logger = logging.getLogger('vdw_server.backups')


class Command(BaseCommand):
    help = 'Run automatic database backups to S3 (see vdw_server/backups.py for the rules)'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Check once and exit')

    def handle(self, *args, **options):
        interval = settings.BACKUP_CHECK_SECONDS
        scheduler = AutoBackupScheduler()
        logger.info('Backup scheduler started: checking every %s seconds', interval)
        while True:
            try:
                result = scheduler.check()
                # "up to date" every 5 minutes would flood the log; daily backups show it's alive
                logger.log(logging.DEBUG if result == 'up to date' else logging.INFO, 'Backup check: %s', result)
            except Exception:
                logger.exception('Backup check failed; will retry at the next check')
            finally:
                # Don't hold a connection between checks: a restore replaces the database file.
                connections.close_all()
            if options['once']:
                return
            time.sleep(interval)
