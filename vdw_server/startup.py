"""Server startup hooks for operational chores."""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from vdw_server.sitemap_utils import refresh_sitemap

logger = logging.getLogger(__name__)

_sitemap_refresh_attempted = False


def run_startup_tasks() -> None:
    """Execute one-off startup tasks in long-running server processes."""
    run_main = os.environ.get('RUN_MAIN')
    if run_main not in (None, 'true'):
        return
    _refresh_sitemap_if_configured()


def _refresh_sitemap_if_configured() -> None:
    global _sitemap_refresh_attempted
    if _sitemap_refresh_attempted:
        return
    _sitemap_refresh_attempted = True

    base_url = (getattr(settings, 'SITE_BASE_URL', '') or '').strip()
    if not base_url:
        logger.info('SITE_BASE_URL not configured; skipping automatic sitemap refresh')
        return

    try:
        refresh_sitemap(base_url)
    except (OperationalError, ProgrammingError) as exc:
        logger.warning('Skipping automatic sitemap refresh because the database is unavailable: %s', exc)
    except Exception:
        logger.exception('Automatic sitemap refresh failed')
    else:
        logger.info('Sitemap refreshed automatically on startup')
