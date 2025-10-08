from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .search import search_pages


def search_page(request):
    """Render the search page with search interface"""
    query = request.GET.get('q', '').strip()
    results = []
    
    if query and len(query) >= 2:
        search_results = search_pages(query, limit=100)  # Get more results for full page
        results = search_results.get('hits', [])
    
    return render(request, 'search/search.html', {
        'query': query,
        'results': results,
        'result_count': len(results)
    })


@require_http_methods(["GET"])
def search_api(request):
    """API endpoint for search queries"""
    query = request.GET.get('q', '').strip()
    limit = request.GET.get('limit', '20')  # Default to 20, but allow override
    
    if not query:
        return JsonResponse({'hits': [], 'query': query, 'processingTime': 0})
    
    if len(query) < 2:
        return JsonResponse({'hits': [], 'query': query, 'processingTime': 0})
    
    try:
        limit = int(limit)
        limit = min(limit, 1000)  # Cap at 1000 for performance
    except ValueError:
        limit = 20
    
    results = search_pages(query, limit=limit)
    
    return JsonResponse(results)
