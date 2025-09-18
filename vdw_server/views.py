from django.shortcuts import render


def custom_page_not_found(request, exception, template_name="404.html"):
    """Render a friendly 404 page with the correct status code."""
    return render(request, template_name, status=404)
