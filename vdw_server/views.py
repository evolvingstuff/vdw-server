from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponsePermanentRedirect
from django.shortcuts import render
from vdw_server.not_found_suggestions import (
    get_not_found_redirect_url,
    get_not_found_requested_phrase,
    get_not_found_suggestions,
)


def page_detail_fallback(request, raw_slug):
    """Handle page-like URLs that failed the `<slug:slug>` route."""

    assert isinstance(raw_slug, str), f"raw_slug must be str, got {type(raw_slug)}"

    redirect_url = get_not_found_redirect_url(request)
    if redirect_url:
        return HttpResponsePermanentRedirect(redirect_url)

    return custom_page_not_found(request, Http404(f"Page not found for raw slug {raw_slug!r}"))


def custom_page_not_found(request, exception, template_name="404.html"):
    """Render a friendly 404 page with the correct status code."""
    if settings.ENABLE_404_SUGGESTIONS:
        redirect_url = get_not_found_redirect_url(request)
        if redirect_url:
            return HttpResponsePermanentRedirect(redirect_url)
        requested_phrase, suggestions = get_not_found_suggestions(request)
    else:
        requested_phrase = get_not_found_requested_phrase(request)
        suggestions = tuple()
    return render(
        request,
        template_name,
        {
            'requested_phrase': requested_phrase,
            'suggestions': suggestions,
        },
        status=404,
    )


def custom_server_error(request, template_name="500.html"):
    """Render a stable 500 page with a request ID for log correlation."""
    request_id = getattr(request, 'request_id', None)
    response = render(
        request,
        template_name,
        {
            'request_id': request_id,
        },
        status=500,
    )
    if request_id:
        response['X-Request-ID'] = request_id
    return response


def sitemap_xml(request):
    """Serve the most recently generated sitemap file."""
    sitemap_path = Path(getattr(settings, 'SITEMAP_FILE_PATH', settings.BASE_DIR / 'sitemap.xml'))

    if not sitemap_path.exists():
        raise Http404("Sitemap has not been generated yet.")

    response = FileResponse(sitemap_path.open('rb'), content_type='application/xml')
    response['Content-Disposition'] = 'inline; filename="sitemap.xml"'
    return response


def google_site_verification(request, token):
    """Serve the google<token>.html verification file from the project root."""
    verification_dir = Path(getattr(settings, 'GOOGLE_VERIFICATION_DIR', settings.BASE_DIR))
    filename = f'google{token}.html'
    verification_path = verification_dir / filename

    if not verification_path.exists():
        raise Http404("Verification file not found.")

    response = FileResponse(verification_path.open('rb'), content_type='text/html')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
