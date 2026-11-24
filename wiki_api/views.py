"""Read-only JSON API that lets AI assistants search and read VitaminDWiki.

Used by the on-page AI chat (served from its own subdomain, hence CORS) and by the
connector for people's own ChatGPT/Claude apps. Every endpoint is public and GET-only.
Results are deliberately compact (title, link, type, date) because assistants pay per
token; page text is read on demand through /api/pages/<slug>/.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

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
MAX_OFFSET = 1000  # search can't page past its first 1,000 hits
DEFAULT_PAGE_CHARS = 15000  # about 4,000 tokens
MAX_PAGE_CHARS = 60000
MAX_COMBINED_TOPICS = 5
SORTS = ('important', 'recent')
CACHE_SECONDS = 60

# Site-relative links in page Markdown/HTML ("](/pages/x/", href="/...") become absolute,
# so an assistant can hand them to the reader. Protocol-relative "//host" links are left alone.
RELATIVE_LINK_RE = re.compile(r'(\]\(|href="|src=")/(?!/)')


class BadRequest(Exception):
    pass


def api_view(view):
    """GET-only JSON endpoint: CORS for the chat's origin, short caching, 400s for bad input."""

    @require_GET
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except BadRequest as exc:
            response = JsonResponse({'error': str(exc)}, status=400)
        else:
            response['Cache-Control'] = f'public, max-age={CACHE_SECONDS}'
        origin = request.headers.get('Origin')
        if origin and origin in settings.WIKI_API_CORS_ORIGINS:
            response['Access-Control-Allow-Origin'] = origin
        response['Vary'] = 'Origin'
        return response

    return wrapper


def _int_param(request, name: str, default: int, minimum: int, maximum: int) -> int:
    raw = request.GET.get(name)
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f'{name} must be a whole number')
    if not minimum <= value <= maximum:
        raise BadRequest(f'{name} must be between {minimum} and {maximum}')
    return value


def _absolute(path: str) -> str:
    return f"{settings.SITE_BASE_URL.rstrip('/')}{path}"


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
        'url': _absolute(reverse('page_detail', args=[hit['slug']])),
        'slug': hit['slug'],
        'type': page_type(hit.get('search_priority', 0)),
        'updated': datetime.fromtimestamp(modified, tz=timezone.utc).date().isoformat() if modified else None,
    }


@api_view
def search(request):
    """Pages matching words in their title or text, most important first."""
    query = request.GET.get('q', '').strip()
    if not query:
        raise BadRequest('q is required')
    limit = _int_param(request, 'limit', DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _int_param(request, 'offset', 0, 0, MAX_OFFSET - limit)
    results = search_pages(query, limit=limit, offset=offset)
    return JsonResponse({
        'query': query,
        'total': results.get('totalHits', 0),
        'offset': offset,
        'results': [_search_hit_item(hit) for hit in results['hits']],
    })


@api_view
def topics(request):
    """Topics whose name contains q (all topics when q is empty), with page counts."""
    query = request.GET.get('q', '')
    limit = _int_param(request, 'limit', 20, 1, MAX_TOPICS_LIMIT)
    matches = get_topic_index().topics_matching(query)
    return JsonResponse({
        'query': query,
        'total': len(matches),
        'results': [{'name': t.name, 'slug': t.slug, 'pages': t.count} for t in matches[:limit]],
    })


@api_view
def topic_pages(request):
    """Pages having every topic in ?topics=slug,slug, most important or newest first."""
    slugs = [s.strip() for s in request.GET.get('topics', '').split(',') if s.strip()]
    if not slugs:
        raise BadRequest('topics is required: one or more topic slugs separated by commas')
    if len(slugs) > MAX_COMBINED_TOPICS:
        raise BadRequest(f'at most {MAX_COMBINED_TOPICS} topics can be combined')
    sort = request.GET.get('sort', 'important')
    if sort not in SORTS:
        raise BadRequest(f"sort must be one of: {', '.join(SORTS)}")
    limit = _int_param(request, 'limit', DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _int_param(request, 'offset', 0, 0, 100000)

    index = get_topic_index()
    try:
        tags = index.tags_for_slugs(slugs)
    except Tag.DoesNotExist as exc:
        return JsonResponse({'error': f'{exc}. Use /api/topics/?q=... to find topic slugs.'}, status=404)
    ids = index.ordered_page_ids(tags, sort=sort)
    return JsonResponse({
        'topics': [{'name': t.name, 'slug': t.slug} for t in tags],
        'sort': sort,
        'total': len(ids),
        'offset': offset,
        'results': [_page_item(index.pages[pid]) for pid in ids[offset:offset + limit]],
    })


@api_view
def recent(request):
    """The most recently added or updated pages, newest first."""
    limit = _int_param(request, 'limit', DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _int_param(request, 'offset', 0, 0, 100000)
    index = get_topic_index()
    ids = index.recent_page_ids()
    return JsonResponse({
        'total': len(ids),
        'offset': offset,
        'results': [_page_item(index.pages[pid]) for pid in ids[offset:offset + limit]],
    })


@api_view
def page(request, slug):
    """One page's Markdown, read in chunks of max_chars starting at start."""
    # Not get_object_or_404: the site's 404 handler answers with an HTML page or a redirect.
    page = Page.objects.prefetch_related('derived_tags').filter(slug=slug, status='published').first()
    if page is None:
        return JsonResponse({'error': f'No published page {slug!r}. Use /api/search/?q=... to find pages.'}, status=404)
    max_chars = _int_param(request, 'max_chars', DEFAULT_PAGE_CHARS, 1000, MAX_PAGE_CHARS)
    text = RELATIVE_LINK_RE.sub(lambda m: f"{m.group(1)}{settings.SITE_BASE_URL.rstrip('/')}/", page.content_md)
    start = _int_param(request, 'start', 0, 0, max(len(text) - 1, 0))
    end = min(start + max_chars, len(text))

    tags = list(page.derived_tags.all())
    priority = compute_search_priority([t.name for t in tags], [t.slug for t in tags], page.title)
    return JsonResponse({
        'title': page.title,
        'url': _absolute(reverse('page_detail', args=[page.slug])),
        'slug': page.slug,
        'type': page_type(priority),
        'created': _date(page.created_date),
        'updated': _date(page.public_modified_date),
        'topics': [{'name': t.name, 'slug': t.slug} for t in sorted(tags, key=lambda t: t.name.casefold())],
        'length': len(text),
        'start': start,
        'text': text[start:end],
        'next_start': end if end < len(text) else None,
    })
