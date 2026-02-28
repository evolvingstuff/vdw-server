"""In-memory cache for the most recently updated published pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import List, Tuple

from django.db.models import QuerySet

from pages.models import Page

MAX_RECENT_PAGES = 150


@dataclass(frozen=True)
class RecentPageEntry:
    pk: int
    slug: str
    title: str
    status: str
    modified_date: datetime
    created_date: datetime


_recent_pages: List[RecentPageEntry] = []
_loaded = False
_lock = RLock()


def load_recent_pages(force: bool = False) -> None:
    """Load the in-memory cache from the database."""

    global _recent_pages, _loaded

    with _lock:
        if _loaded and not force:
            return

    pages: QuerySet[Page] = Page.objects.filter(status='published').only(
        'id',
        'slug',
        'title',
        'status',
        'modified_date',
        'created_date',
    ).order_by('-modified_date', '-created_date', '-id')[:MAX_RECENT_PAGES]
    entries = [_entry_from_page(page) for page in pages]

    with _lock:
        _recent_pages = entries
        _loaded = True


def reload_recent_pages() -> None:
    """Force a full cache reload."""

    load_recent_pages(force=True)


def clear_recent_pages_cache() -> None:
    """Clear all cached entries. Primarily used in tests."""

    global _recent_pages, _loaded
    with _lock:
        _recent_pages = []
        _loaded = False


def get_recent_pages() -> Tuple[RecentPageEntry, ...]:
    """Return cached pages sorted by most recently updated."""

    load_recent_pages()
    with _lock:
        return tuple(_recent_pages)


def upsert_recent_page(page: Page) -> None:
    """Add/update a page entry in cache, or remove it if unpublished."""

    assert page.pk, "Page must have a primary key before caching"

    load_recent_pages()
    with _lock:
        entries = [entry for entry in _recent_pages if entry.pk != page.pk]
        if page.status == 'published':
            entries.append(_entry_from_page(page))
            entries.sort(key=_sort_key, reverse=True)
            if len(entries) > MAX_RECENT_PAGES:
                entries = entries[:MAX_RECENT_PAGES]
        _replace_entries(entries)


def remove_recent_page(page_id: int) -> None:
    """Remove a page entry from cache by primary key."""

    assert isinstance(page_id, int), f"page_id must be int, got {type(page_id)}"

    load_recent_pages()
    with _lock:
        entries = [entry for entry in _recent_pages if entry.pk != page_id]
        _replace_entries(entries)


def get_cached_recent_count() -> int:
    """Return the current number of cached entries."""

    with _lock:
        return len(_recent_pages)


def _entry_from_page(page: Page) -> RecentPageEntry:
    assert page.pk, "Page must have a primary key before caching"
    assert page.modified_date, "Page modified_date is required for cache sorting"
    assert page.created_date, "Page created_date is required for cache sorting"
    return RecentPageEntry(
        pk=page.pk,
        slug=page.slug,
        title=page.title,
        status=page.status,
        modified_date=page.modified_date,
        created_date=page.created_date,
    )


def _sort_key(entry: RecentPageEntry) -> tuple[datetime, datetime, int]:
    return entry.modified_date, entry.created_date, entry.pk


def _replace_entries(entries: List[RecentPageEntry]) -> None:
    global _recent_pages, _loaded
    _recent_pages = entries
    _loaded = True
