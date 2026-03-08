"""Server startup hooks for operational chores."""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from pages.recent_cache import load_recent_pages
from vdw_server.restore_state import finalize_pending_restore
from vdw_server.sitemap_utils import refresh_sitemap

logger = logging.getLogger(__name__)

_sitemap_refresh_attempted = False
_recent_pages_cache_attempted = False


def run_startup_tasks() -> None:
    """Execute one-off startup tasks in long-running server processes."""
    run_main = os.environ.get('RUN_MAIN')
    if run_main not in (None, 'true'):
        return
    _finalize_pending_restore_if_present()
    _refresh_sitemap_if_configured()
    _load_recent_pages_cache()


def _finalize_pending_restore_if_present() -> None:
    try:
        summary = finalize_pending_restore()
    except (OperationalError, ProgrammingError) as exc:
        logger.warning('Pending restore validation deferred because the database is unavailable: %s', exc)
    except Exception:
        logger.exception('Pending restore validation failed; maintenance mode remains enabled')
    else:
        if summary is not None:
            logger.info(
                'Pending restore validated successfully on startup; homepage=%s (%s); maintenance cleared',
                summary.homepage_id,
                summary.homepage_title,
            )


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


def _load_recent_pages_cache() -> None:
    global _recent_pages_cache_attempted
    if _recent_pages_cache_attempted:
        return
    _recent_pages_cache_attempted = True

    try:
        load_recent_pages(force=True)
    except (OperationalError, ProgrammingError) as exc:
        logger.warning('Skipping recent pages cache warmup because the database is unavailable: %s', exc)
    except Exception:
        logger.exception('Recent pages cache warmup failed')
    else:
        logger.info('Recent pages cache warmed successfully on startup')
