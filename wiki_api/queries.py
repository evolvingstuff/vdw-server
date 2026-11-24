"""What the wiki API answers, shared by the JSON endpoints (views.py) and the MCP tools (mcp.py).

Each function returns a plain dict ready for JSON, or raises ApiError with a message that
tells an assistant what to do instead.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional, Sequence

from django.conf import settings
from django.urls import reverse
from meilisearch.errors import MeilisearchCommunicationError, MeilisearchTimeoutError

from pages.models import Page
from search.search import (
    SEARCH_PRIORITY_CATEGORY,
    SEARCH_PRIORITY_EXTENDED,
    SEARCH_PRIORITY_MANY_STUDIES,
    SEARCH_PRIORITY_META_ANALYSIS,
    SEARCH_PRIORITY_OVERVIEW,
    SEARCH_PRIORITY_RCT,
    SEARCH_PRIORITY_SEVERAL_STUDIES,
    SEARCH_PRIORITY_SUMMARY,
    compute_search_priority,
    search_pages,
)
from tags.models import Tag
from tags.services import TopicPage, get_topic_index

logger = logging.getLogger(__name__)

# Page types, most important first; the same buckets that order search and topic pages.
PAGE_TYPES = {
    SEARCH_PRIORITY_SUMMARY: 'summary',
    SEARCH_PRIORITY_OVERVIEW: 'overview',
    SEARCH_PRIORITY_EXTENDED: 'extended',
    SEARCH_PRIORITY_MANY_STUDIES: 'many studies',
    SEARCH_PRIORITY_SEVERAL_STUDIES: 'several studies',
    SEARCH_PRIORITY_META_ANALYSIS: 'meta-analysis',
    SEARCH_PRIORITY_RCT: 'RCT',
    SEARCH_PRIORITY_CATEGORY: 'category',
}
OTHER_PAGE_TYPE = 'page'

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_TOPICS_LIMIT = 100
MAX_SEARCH_DEPTH = 1000  # search can't page past its first 1,000 hits
DEFAULT_PAGE_CHARS = 15000  # about 4,000 tokens
MAX_PAGE_CHARS = 60000
MAX_COMBINED_TOPICS = 5
SORTS = ('important', 'recent')

# Site-relative links in page Markdown/HTML ("](/pages/x/", href="/...") become absolute,
# so an assistant can hand them to the reader. Protocol-relative "//host" links are left alone.
RELATIVE_LINK_RE = re.compile(r'(\]\(|href="|src=")/(?!/)')


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def check_range(name: str, value: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(f'{name} must be a whole number')
    if not minimum <= value <= maximum:
        raise ApiError(f'{name} must be between {minimum} and {maximum}')
    return value


def _absolute(path: str) -> str:
    return f"{settings.SITE_BASE_URL.rstrip('/')}{path}"


def page_url(slug: str) -> str:
    return _absolute(reverse('page_detail', args=[slug]))


def _date(value: Optional[datetime]) -> Optional[str]:
    return value.date().isoformat() if value else None


def page_type(priority: int) -> str:
    return PAGE_TYPES.get(priority, OTHER_PAGE_TYPE)


def _page_item(page: TopicPage) -> dict:
    return {
        'title': page.title,
        'url': _absolute(page.url),
        'slug': page.slug,
        'type': page_type(page.priority),
        'updated': _date(page.updated),
    }


def _search_hit_item(hit: dict) -> dict:
    modified = hit.get('modified_date')
    return {
        'title': hit['title'],
        'url': page_url(hit['slug']),
        'slug': hit['slug'],
        'type': page_type(hit.get('search_priority', 0)),
        'updated': datetime.fromtimestamp(modified, tz=timezone.utc).date().isoformat() if modified else None,
    }


def search(query: str, limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
    """Pages matching words in their title or text, most important first."""
    query = (query or '').strip()
    if not query:
        raise ApiError('q is required')
    check_range('limit', limit, 1, MAX_LIMIT)
    check_range('offset', offset, 0, MAX_SEARCH_DEPTH - limit)
    try:
        results = search_pages(query, limit=limit, offset=offset)
    except (MeilisearchCommunicationError, MeilisearchTimeoutError):
        # An assistant can still find pages through topics, so say so instead of failing.
        logger.exception('Wiki API search: search service unreachable')
        raise ApiError(
            'Search is temporarily unavailable. Find pages through topics instead '
            '(find topics by name, then list their pages).',
            status=503,
        )
    return {
        'query': query,
        'total': results.get('totalHits', 0),
        'offset': offset,
        'results': [_search_hit_item(hit) for hit in results['hits']],
    }


def topics(query: str = '', limit: int = 20) -> dict:
    """Topics whose name contains query (all topics when empty), with page counts."""
    check_range('limit', limit, 1, MAX_TOPICS_LIMIT)
    matches = get_topic_index().topics_matching(query or '')
    return {
        'query': query or '',
        'total': len(matches),
        'results': [{'name': t.name, 'slug': t.slug, 'pages': t.count} for t in matches[:limit]],
    }


def topic_pages(slugs: Sequence[str], sort: str = 'important', limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
    """Pages having every topic, most important or newest first."""
    slugs = [s.strip() for s in slugs if s and s.strip()]
    if not slugs:
        raise ApiError('topics is required: one or more topic slugs')
    if len(slugs) > MAX_COMBINED_TOPICS:
        raise ApiError(f'at most {MAX_COMBINED_TOPICS} topics can be combined')
    if sort not in SORTS:
        raise ApiError(f"sort must be one of: {', '.join(SORTS)}")
    check_range('limit', limit, 1, MAX_LIMIT)
    check_range('offset', offset, 0, 100000)

    index = get_topic_index()
    try:
        tags = index.tags_for_slugs(slugs)
    except Tag.DoesNotExist as exc:
        raise ApiError(f'{exc}. Find topic slugs by searching topics by name first.', status=404)
    ids = index.ordered_page_ids(tags, sort=sort)
    return {
        'topics': [{'name': t.name, 'slug': t.slug} for t in tags],
        'sort': sort,
        'total': len(ids),
        'offset': offset,
        'results': [_page_item(index.pages[pid]) for pid in ids[offset:offset + limit]],
    }


def recent(limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
    """The most recently added or updated pages, newest first."""
    check_range('limit', limit, 1, MAX_LIMIT)
    check_range('offset', offset, 0, 100000)
    index = get_topic_index()
    ids = index.recent_page_ids()
    return {
        'total': len(ids),
        'offset': offset,
        'results': [_page_item(index.pages[pid]) for pid in ids[offset:offset + limit]],
    }


def page(slug: str, start: int = 0, max_chars: int = DEFAULT_PAGE_CHARS) -> dict:
    """One page's Markdown, read in chunks of max_chars starting at start."""
    page = Page.objects.prefetch_related('derived_tags').filter(slug=slug, status='published').first()
    if page is None:
        raise ApiError(f'No published page {slug!r}. Search to find pages and their slugs.', status=404)
    check_range('max_chars', max_chars, 1000, MAX_PAGE_CHARS)
    base = settings.SITE_BASE_URL.rstrip('/')
    text = RELATIVE_LINK_RE.sub(lambda m: f'{m.group(1)}{base}/', page.content_md)
    check_range('start', start, 0, max(len(text) - 1, 0))
    end = min(start + max_chars, len(text))

    tags = list(page.derived_tags.all())
    priority = compute_search_priority([t.name for t in tags], [t.slug for t in tags], page.title)
    return {
        'title': page.title,
        'url': page_url(page.slug),
        'slug': page.slug,
        'type': page_type(priority),
        'created': _date(page.created_date),
        'updated': _date(page.public_modified_date),
        'topics': [{'name': t.name, 'slug': t.slug} for t in sorted(tags, key=lambda t: t.name.casefold())],
        'length': len(text),
        'start': start,
        'text': text[start:end],
        'next_start': end if end < len(text) else None,
    }
