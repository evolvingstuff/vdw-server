"""In-memory cache for legacy aliases → canonical page slugs."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from django.db.models import QuerySet

from pages.models import Page

logger = logging.getLogger(__name__)

_alias_path_map: Dict[str, str] = {}
_alias_plain_map: Dict[str, str] = {}
_loaded = False


def load_alias_redirects(force: bool = False) -> None:
    """Populate the alias cache by reading every published page once."""

    global _alias_path_map, _alias_plain_map, _loaded

    if _loaded and not force:
        return

    path_map: Dict[str, str] = {}
    plain_map: Dict[str, str] = {}

    pages: QuerySet[Page] = Page.objects.filter(status='published').only('slug', 'aliases', 'original_page_id')
    for page in pages.iterator():
        _register_aliases_for_page(page, path_map, plain_map)

    _alias_path_map = path_map
    _alias_plain_map = plain_map
    _loaded = True


def reload_alias_redirects() -> None:
    """Force a reload. Useful for tests or admin scripts."""

    load_alias_redirects(force=True)


def lookup_path(path: str) -> Optional[str]:
    """Return the slug for a normalized alias path (with or without leading `/`)."""

    if not path:
        return None
    normalized = _normalize_path(path)
    return _alias_path_map.get(normalized)


def lookup_plain(value: Optional[str]) -> Optional[str]:
    """Return the slug for a plain alias value (no leading slash)."""

    if not value:
        return None
    normalized = _normalize_plain(value)
    if not normalized:
        return None
    return _alias_plain_map.get(normalized)


def _register_aliases_for_page(page: Page, path_map: Dict[str, str], plain_map: Dict[str, str]) -> None:
    slug = page.slug

    alias_lines = (page.aliases or '').splitlines()
    for raw_alias in alias_lines:
        normalized_path = _normalize_path(raw_alias)
        normalized_plain = normalized_path.lstrip('/')

        _register_alias(path_map, normalized_path, slug, f"alias '{raw_alias}'")
        if normalized_plain:
            _register_alias(plain_map, normalized_plain, slug, f"alias '{raw_alias}'")

    if page.original_page_id:
        key = str(page.original_page_id).strip()
        if key:
            _register_alias(path_map, f'/{key}', slug, f'page_id {key}')
            _register_alias(plain_map, key, slug, f'page_id {key}')


def _register_alias(mapping: Dict[str, str], key: str, slug: str, source: str) -> None:
    if not key:
        return

    existing = mapping.get(key)
    if existing and existing != slug:
        logger.warning(
            "Alias %s already registered for %s, ignoring duplicate for %s from %s",
            key,
            existing,
            slug,
            source,
        )
        return

    mapping.setdefault(key, slug)


def _normalize_path(path: str) -> str:
    trimmed = (path or '').strip()
    if not trimmed:
        return ''
    if not trimmed.startswith('/'):
        trimmed = '/' + trimmed
    return trimmed


def _normalize_plain(value: str) -> str:
    trimmed = (value or '').strip().lstrip('/')
    return trimmed


def get_cached_alias_count() -> int:
    """Return the number of cached path variants (for debugging/tests)."""

    return len(_alias_path_map)
