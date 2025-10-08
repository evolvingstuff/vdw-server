import re
from django.shortcuts import redirect
from django.urls import reverse
from pages.models import Page
from site_pages.models import SitePage


class AdminPageRedirectMiddleware:
    """
    Middleware to transform published page and site page URLs into their admin edit
    equivalents. Handles both logged-in users and post-login redirects.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if user is already authenticated and accessing admin with a 'next' parameter
        if (request.path == '/admin/' and
            request.method == 'GET' and
            request.user.is_authenticated and
            request.user.is_staff and
            'next' in request.GET):

            next_url = request.GET.get('next')

            # Check if it's a page detail URL (new or legacy path)
            page_slug = None
            for pattern in (r'^/pages/([^/]+)/$', r'^/posts/([^/]+)/$'):
                match = re.match(pattern, next_url)
                if match:
                    page_slug = match.group(1)
                    break

            if page_slug:
                try:
                    page = Page.objects.get(slug=page_slug)
                    edit_url = reverse('admin:posts_page_change', args=[page.pk])
                    return redirect(edit_url)
                except Page.DoesNotExist:
                    return redirect(reverse('admin:index'))

            # Check if it's the homepage
            if next_url == '/':
                try:
                    # Find the homepage and redirect to edit page
                    homepage = SitePage.objects.get(page_type='homepage')
                    edit_url = reverse('admin:pages_sitepage_change', args=[homepage.pk])
                    return redirect(edit_url)
                except SitePage.DoesNotExist:
                    # Homepage not found, redirect to admin home
                    return redirect(reverse('admin:index'))

            # Check if it's a page detail URL
            else:
                page_match = re.match(r'^/([^/]+)/$', next_url)
                if page_match:
                    slug = page_match.group(1)
                    try:
                        # Find the page and redirect to edit page
                        page = SitePage.objects.get(slug=slug)
                        edit_url = reverse('admin:pages_sitepage_change', args=[page.pk])
                        return redirect(edit_url)
                    except SitePage.DoesNotExist:
                        # Page not found, continue to default admin behavior
                        pass

        response = self.get_response(request)

        # Also handle post-login redirects
        if (request.path == '/admin/login/' and
            request.method == 'POST' and
            response.status_code == 302 and
            request.user.is_authenticated and
            request.user.is_staff):

            # Get the redirect URL from response
            redirect_url = response.url

            page_slug = None
            for pattern in (r'^/pages/([^/]+)/$', r'^/posts/([^/]+)/$'):
                match = re.match(pattern, redirect_url)
                if match:
                    page_slug = match.group(1)
                    break

            if page_slug:
                try:
                    page = Page.objects.get(slug=page_slug)
                    edit_url = reverse('admin:posts_page_change', args=[page.pk])
                    return redirect(edit_url)
                except Page.DoesNotExist:
                    return redirect(reverse('admin:index'))

            # Check if it's the homepage
            if redirect_url == '/':
                try:
                    # Find the homepage and redirect to edit page
                    homepage = SitePage.objects.get(page_type='homepage')
                    edit_url = reverse('admin:pages_sitepage_change', args=[homepage.pk])
                    return redirect(edit_url)
                except SitePage.DoesNotExist:
                    # Homepage not found, redirect to admin home
                    return redirect(reverse('admin:index'))

            # Check if it's a page detail URL
            else:
                page_match = re.match(r'^/([^/]+)/$', redirect_url)
                if page_match:
                    slug = page_match.group(1)
                    try:
                        # Find the page and redirect to edit page
                        page = SitePage.objects.get(slug=slug)
                        edit_url = reverse('admin:pages_sitepage_change', args=[page.pk])
                        return redirect(edit_url)
                    except SitePage.DoesNotExist:
                        # Page not found, continue to default behavior
                        pass

        return response
