from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .search import search_posts


def search_page(request):
    """Render the search page with search interface"""
    return render(request, 'search/search.html')


@require_http_methods(["GET"])
def search_api(request):
    """API endpoint for search queries"""
    query = request.GET.get('q', '').strip()
    
    if not query:
        return JsonResponse({'hits': [], 'query': query, 'processingTime': 0})
    
    if len(query) < 2:
        return JsonResponse({'hits': [], 'query': query, 'processingTime': 0})
    
    results = search_posts(query)
    
    return JsonResponse(results)
