"""In-memory topic index for the topic browser and tag pages.

Each worker process builds the index once (a few seconds on the server) and then
answers every topic request from memory in milliseconds. Membership uses
Page.derived_tags: tags assigned in admin plus tags implied by words in the title.

Keeping it fresh:
- Page and tag saves/deletes write a marker file (see tags/signals.py); bulk tag changes
  show up in the tag-link table's count and max id. Together these form a signature that
  costs well under a millisecond to read (no scan of the large pages table).
- The signature is checked at most every SIGNATURE_CHECK_SECONDS. When it changes, the
  index is rebuilt in a background thread while the previous index keeps answering, so
  only the very first request in a worker ever waits for a build.
- MAX_INDEX_AGE_SECONDS forces a periodic rebuild as a safety net.
- gunicorn.conf.py warms the index as soon as each worker starts.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from django.conf import settings
from django.db import close_old_connections, connection
from django.urls import reverse

from pages.models import Page
from search.search import compute_search_priority
from tags.models import Tag

logger = logging.getLogger(__name__)

TAG_PAGE_LIST_LIMIT = 1000
SIGNATURE_CHECK_SECONDS = 10
MAX_INDEX_AGE_SECONDS = 3600


@dataclass(frozen=True)
class TopicTag:
    id: int
    name: str
    slug: str


@dataclass(frozen=True)
class TopicPage:
    id: int
    title: str
    url: str


@dataclass(frozen=True)
class TopicCount:
    name: str
    slug: str
    count: int


class TopicIndex:
    """Immutable snapshot; a rebuild replaces the whole object."""

    def __init__(self, signature: tuple):
        self.signature = signature
        self.built_at = time.monotonic()

        tag_rows = Tag.objects.values_list('id', 'name', 'slug')
        self.tags: Dict[int, TopicTag] = {tid: TopicTag(tid, name, slug) for tid, name, slug in tag_rows}
        self.tag_id_by_slug: Dict[str, int] = {t.slug: t.id for t in self.tags.values()}

        page_rows = list(
            Page.objects.filter(status='published').values_list('id', 'title', 'slug', 'public_modified_date')
        )
        published_ids = {row[0] for row in page_rows}

        page_tags: Dict[int, List[int]] = defaultdict(list)
        links = Page.derived_tags.through.objects.values_list('page_id', 'tag_id')
        for page_id, tag_id in links:
            if page_id in published_ids and tag_id in self.tags:
                page_tags[page_id].append(tag_id)
        self.page_tags: Dict[int, Tuple[int, ...]] = {pid: tuple(tids) for pid, tids in page_tags.items()}

        tag_pages: Dict[int, set] = defaultdict(set)
        for page_id, tids in self.page_tags.items():
            for tag_id in tids:
                tag_pages[tag_id].add(page_id)
        self.tag_pages: Dict[int, FrozenSet[int]] = {tid: frozenset(pids) for tid, pids in tag_pages.items()}

        # Search order: priority bucket (Summary, Overview, ... RCT), then newest.
        page_url = reverse('page_detail', args=['SLUG'])
        sort_keys = {}
        self.pages: Dict[int, TopicPage] = {}
        for page_id, title, slug, modified in page_rows:
            tids = self.page_tags.get(page_id, ())
            priority = compute_search_priority(
                [self.tags[t].name for t in tids], [self.tags[t].slug for t in tids], title
            )
            sort_keys[page_id] = (priority, modified.timestamp() if modified else 0)
            self.pages[page_id] = TopicPage(page_id, title, page_url.replace('SLUG', slug))
        ordered = sorted(sort_keys, key=sort_keys.__getitem__, reverse=True)
        self.rank: Dict[int, int] = {page_id: i for i, page_id in enumerate(ordered)}

        self.topic_counts: List[TopicCount] = sorted(
            (TopicCount(self.tags[tid].name, self.tags[tid].slug, len(pids)) for tid, pids in self.tag_pages.items()),
            key=lambda c: (-c.count, c.name),
        )
        self._ordered_cache: Dict[Tuple[int, ...], List[int]] = {}
        self._cache_lock = threading.Lock()

    def tags_for_slugs(self, slugs: Sequence[str]) -> List[TopicTag]:
        """Tags in the given order; raises Tag.DoesNotExist for unknown slugs."""
        unique = list(dict.fromkeys(slugs))
        missing = [s for s in unique if s not in self.tag_id_by_slug]
        if missing:
            raise Tag.DoesNotExist(f"Unknown tag(s): {', '.join(missing)}")
        return [self.tags[self.tag_id_by_slug[s]] for s in unique]

    def _page_ids_with_all(self, tags: Sequence[TopicTag]) -> FrozenSet[int]:
        assert tags, "At least one tag is required"
        sets = sorted((self.tag_pages.get(t.id, frozenset()) for t in tags), key=len)
        result = sets[0]
        for other in sets[1:]:
            result = result & other
        return result

    def ordered_page_ids(self, tags: Sequence[TopicTag]) -> List[int]:
        key = tuple(sorted(t.id for t in tags))
        cached = self._ordered_cache.get(key)
        if cached is None:
            cached = sorted(self._page_ids_with_all(tags), key=self.rank.__getitem__)
            with self._cache_lock:
                if len(self._ordered_cache) > 2000:  # combinations are endless; keep memory bounded
                    self._ordered_cache.clear()
                self._ordered_cache[key] = cached
        return cached

    def pages_for(self, tags: Sequence[TopicTag], limit: int = TAG_PAGE_LIST_LIMIT) -> Tuple[List[TopicPage], int]:
        """(pages in search order, up to limit), total count)."""
        ids = self.ordered_page_ids(tags)
        return [self.pages[pid] for pid in ids[:limit]], len(ids)

    def combined_counts(self, tags: Sequence[TopicTag]) -> Tuple[int, Dict[str, int]]:
        """(pages having every tag, {slug: count among those pages})."""
        page_ids = self._page_ids_with_all(tags)
        counts: Counter = Counter()
        for page_id in page_ids:
            counts.update(self.page_tags.get(page_id, ()))
        return len(page_ids), {self.tags[tid].slug: n for tid, n in counts.most_common()}


_index: Optional[TopicIndex] = None
_last_signature_check = 0.0
_build_lock = threading.Lock()
_refresh_thread: Optional[threading.Thread] = None


def _marker_file() -> Path:
    return Path(getattr(settings, 'TOPIC_INDEX_MARKER_FILE', Path(settings.BASE_DIR) / 'data' / 'topic_index_version'))


def mark_topics_changed() -> None:
    """Tell every worker its topic index is out of date (called from model signals)."""
    marker = _marker_file()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{time.time_ns()}")
    except OSError as exc:  # never let this break a page save; the max-age rebuild covers it
        logger.error("Could not write topic index marker %s: %s", marker, exc)


def _database_signature() -> tuple:
    try:
        marker = _marker_file().read_text()
    except OSError:
        marker = ''
    links = Page.derived_tags.through._meta.db_table
    tags = Tag._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT (SELECT COUNT(*) FROM {links}), (SELECT MAX(id) FROM {links}), "
            f"(SELECT COUNT(*) FROM {tags}), (SELECT MAX(id) FROM {tags})"
        )
        return (marker, *cursor.fetchone())


def _is_current(index: TopicIndex, signature: tuple) -> bool:
    return index.signature == signature and time.monotonic() - index.built_at < MAX_INDEX_AGE_SECONDS


def _build(signature: tuple) -> TopicIndex:
    global _index
    with _build_lock:  # one build at a time; a waiting caller reuses the result
        index = _index
        if index is None or not _is_current(index, signature):
            started = time.monotonic()
            index = TopicIndex(signature)
            _index = index
            logger.info("Built topic index (%d pages, %d topics) in %.2fs",
                        len(index.pages), len(index.topic_counts), time.monotonic() - started)
        return index


def _refresh_in_background(signature: tuple) -> None:
    global _refresh_thread
    with _build_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return

        def run():
            try:
                _build(signature)
            except Exception:
                logger.exception("Background topic index rebuild failed")
            finally:
                close_old_connections()  # this thread's DB connection

        _refresh_thread = threading.Thread(target=run, name='topic-index-refresh', daemon=True)
        _refresh_thread.start()


def get_topic_index() -> TopicIndex:
    """The current index. Waits only if none exists yet; otherwise refreshes in the background."""
    global _last_signature_check
    index = _index
    now = time.monotonic()
    if index is not None and now - _last_signature_check < SIGNATURE_CHECK_SECONDS:
        return index

    signature = _database_signature()
    _last_signature_check = now
    if index is None:
        return _build(signature)
    if not _is_current(index, signature):
        if getattr(settings, 'TOPIC_INDEX_BACKGROUND_REFRESH', True):
            _refresh_in_background(signature)
        else:
            return _build(signature)
    return index


def warm_topic_index() -> None:
    """Build the index ahead of the first visitor (called when a gunicorn worker starts)."""
    try:
        get_topic_index()
    except Exception:
        logger.exception("Topic index warm-up failed")
    finally:
        close_old_connections()


def reset_topic_index() -> None:
    """Drop the cached index (used by tests)."""
    global _index, _last_signature_check
    _index = None
    _last_signature_check = 0.0
