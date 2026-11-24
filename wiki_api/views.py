"""Read-only JSON API that lets AI assistants search and read VitaminDWiki.

Used by the on-page AI chat (served from its own subdomain, hence CORS). The connector for
people's own ChatGPT/Claude apps (mcp.py) answers from the same functions (queries.py).
Every endpoint is public and GET-only. Results are deliberately compact (title, link, type,
date) because assistants pay per token; page text is read on demand through /api/pages/<slug>/.
"""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from . import queries
from .queries import ApiError

CACHE_SECONDS = 60


def api_view(view):
    """GET-only JSON endpoint: CORS for the chat's origin, short caching, JSON errors."""

    @require_GET
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            response = JsonResponse(view(request, *args, **kwargs))
        except ApiError as exc:
            response = JsonResponse({'error': str(exc)}, status=exc.status)
        else:
            response['Cache-Control'] = f'public, max-age={CACHE_SECONDS}'
        origin = request.headers.get('Origin')
        if origin and origin in settings.WIKI_API_CORS_ORIGINS:
            response['Access-Control-Allow-Origin'] = origin
        response['Vary'] = 'Origin'
        return response

    return wrapper


def _int_param(request, name: str, default: int) -> int:
    raw = request.GET.get(name)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except ValueError:
        raise ApiError(f'{name} must be a whole number')


@api_view
def search(request):
    return queries.search(
        request.GET.get('q', ''),
        limit=_int_param(request, 'limit', queries.DEFAULT_LIMIT),
        offset=_int_param(request, 'offset', 0),
    )


@api_view
def topics(request):
    return queries.topics(request.GET.get('q', ''), limit=_int_param(request, 'limit', 20))


@api_view
def topic_pages(request):
    return queries.topic_pages(
        request.GET.get('topics', '').split(','),
        sort=request.GET.get('sort', 'important'),
        limit=_int_param(request, 'limit', queries.DEFAULT_LIMIT),
        offset=_int_param(request, 'offset', 0),
    )


@api_view
def recent(request):
    return queries.recent(
        limit=_int_param(request, 'limit', queries.DEFAULT_LIMIT),
        offset=_int_param(request, 'offset', 0),
    )


@api_view
def page(request, slug):
    return queries.page(
        slug,
        start=_int_param(request, 'start', 0),
        max_chars=_int_param(request, 'max_chars', queries.DEFAULT_PAGE_CHARS),
    )
