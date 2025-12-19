from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .search import search_pages


def _format_total_hits_display(total_hits: int, max_hits: int = 1000) -> str:
    if total_hits >= max_hits:
        return f"{max_hits}+"
    return str(total_hits)


def search_page(request):
    """Render the search page with search interface"""
    query = request.GET.get('q', '').strip()
    results = []
    total_hits = 0

    if query and len(query) >= 2:
        search_results = search_pages(query, limit=100, offset=0)
        results = search_results.get('hits', [])
        total_hits = search_results.get('totalHits') or 0

    return render(
        request,
        'search/search.html',
        {
            'query': query,
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
