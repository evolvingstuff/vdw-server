from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import render


def custom_page_not_found(request, exception, template_name="404.html"):
    """Render a friendly 404 page with the correct status code."""
    return render(request, template_name, status=404)


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
