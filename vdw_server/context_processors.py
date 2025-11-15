"""Custom template context processors for global UI components."""

from django.conf import settings


def search_preferences(_request):
    """Expose search-related configuration to templates."""
    return {
        'search_results_display_mode': settings.SEARCH_RESULTS_DISPLAY_MODE,
    }
