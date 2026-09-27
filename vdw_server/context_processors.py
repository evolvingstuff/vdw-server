"""Custom template context processors for global UI components."""

from django.conf import settings


def search_preferences(_request):
    """Expose search-related configuration to templates."""
    return {
        'search_results_display_mode': settings.SEARCH_RESULTS_DISPLAY_MODE,
    }


def seo(request):
    """Canonical URL: always the primary domain, dropping query strings except pagination."""
    base_url = (settings.SITE_BASE_URL or '').rstrip('/')
    canonical_url = f"{base_url}{request.path}"
    page_number = request.GET.get('page', '')
    if page_number.isdigit() and page_number != '1':
        canonical_url += f"?page={page_number}"
    return {'canonical_url': canonical_url}
