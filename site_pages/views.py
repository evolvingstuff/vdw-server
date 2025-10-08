from django.shortcuts import render, get_object_or_404, redirect
from .models import SitePage
from pages.views import add_file_icons_to_html


def homepage(request):
    """Dynamic homepage view that looks for homepage page"""
    try:
        page = SitePage.objects.get(page_type='homepage', is_published=True)
    except SitePage.DoesNotExist:
        # Fallback to static template if no homepage exists
        return render(request, 'core/homepage.html')

    # Add file icons to the HTML content
    page.content_html = add_file_icons_to_html(page.content_html)

    return render(request, 'page_detail.html', {
        'page': page,
        'is_homepage': True
    })


def site_page_detail(request, slug):
    """View for individual pages"""
    page = get_object_or_404(SitePage, slug=slug, is_published=True)

    # Redirect if someone tries to access homepage via slug
    if page.page_type == 'homepage':
        return redirect('homepage')

    # Add file icons to the HTML content
    page.content_html = add_file_icons_to_html(page.content_html)

    return render(request, 'page_detail.html', {
        'page': page,
        'is_homepage': False
    })
