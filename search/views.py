from functools import lru_cache

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from helper_functions.seo import meta_description_for
from pages.models import Page

from .query import split_or_query
from .search import search_pages


def _format_total_hits_display(total_hits: int, max_hits: int = 1000) -> str:
    if total_hits >= max_hits:
        return f"{max_hits}+"
    return str(total_hits)


SEARCH_PAGE_LIMIT = 100


@lru_cache(maxsize=20000)
def _cached_description(page_id: int, modified_key: str) -> str:
    """Meta description for a page; cached per page version (modified_key changes on save)."""
    page = Page.objects.filter(pk=page_id).only('id', 'title', 'meta_description', 'content_md').first()
    return meta_description_for(page) if page else ''


def _add_descriptions_and_dates(results):
    """Give each result a one-line description and its last-updated date."""
    ids = [r['id'] for r in results]
    rows = Page.objects.filter(id__in=ids).values_list('id', 'modified_date', 'public_modified_date')
    info = {pid: (modified, public) for pid, modified, public in rows}
    for result in results:
        modified, public = info.get(result['id'], (None, None))
        result['description'] = _cached_description(result['id'], str(modified)) if modified else ''
        result['updated'] = public
    return results


def _interleave_results(result_lists):
    """Merge ranked result lists: the best of each list first, then the second best, etc."""
    merged, seen = [], set()
    for position in range(max((len(r) for r in result_lists), default=0)):
        for results in result_lists:
            if position < len(results) and results[position]['id'] not in seen:
                seen.add(results[position]['id'])
                merged.append(results[position])
    return merged


def search_page(request):
    """Full-page search results (/search/?q=...).

    Supports Tiki-style OR queries, as used by old "Search VitaminDWiki for X OR Y" links:
    each alternative is searched separately and the results are combined.
    """
    query = request.GET.get('q', '').strip()
    alternatives = split_or_query(query) if query else []
    results = []
    total_hits = 0

    if query and len(query) >= 2:
        if len(alternatives) > 1:
            responses = [search_pages(alt, limit=SEARCH_PAGE_LIMIT, offset=0) for alt in alternatives]
            results = _interleave_results([r.get('hits', []) for r in responses])
            total_hits = len(results)
        else:
            search_results = search_pages(alternatives[0] if alternatives else query, limit=SEARCH_PAGE_LIMIT, offset=0)
            results = search_results.get('hits', [])
            total_hits = search_results.get('totalHits') or 0

    results = _add_descriptions_and_dates(results)

    return render(
        request,
        'search/search.html',
        {
            'query': query,
            'alternatives': alternatives if len(alternatives) > 1 else [],
            'results': results,
            'result_total': total_hits,
            'result_total_display': _format_total_hits_display(total_hits),
        },
    )


@require_http_methods(["GET"])
def search_api(request):
    """API endpoint for search queries"""
    query = request.GET.get('q', '').strip()
    limit_raw = request.GET.get('limit', '20')
    offset_raw = request.GET.get('offset', '0')

    if not query or len(query) < 2:
        return JsonResponse(
            {
                'hits': [],
                'query': query,
                'processingTime': 0,
                'totalHits': 0,
                'limit': 0,
                'offset': 0,
            }
        )

    try:
        limit = int(limit_raw)
        offset = int(offset_raw)
    except ValueError:
        return JsonResponse(
            {'error': 'limit and offset must be integers'},
            status=400,
        )

    if limit < 0 or offset < 0:
        return JsonResponse({'error': 'limit and offset must be non-negative'}, status=400)

    max_hits = 1000
    limit = min(limit, max_hits)
    if offset > max_hits:
        return JsonResponse({'error': f'offset must be <= {max_hits}'}, status=400)

    if offset + limit > max_hits:
        limit = max_hits - offset

    results = search_pages(query, limit=limit, offset=offset)
    results['limit'] = limit
    results['offset'] = offset

    if 'totalHits' not in results:
        results['totalHits'] = 0

    return JsonResponse(results)
