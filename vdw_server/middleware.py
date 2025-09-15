import re
from django.shortcuts import redirect
from django.urls import reverse
from posts.models import Post


class AdminPostRedirectMiddleware:
    """
    Middleware to transform post view URLs to edit URLs when accessing admin.
    Handles both logged-in users and post-login redirects.
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

            # Check if it's a post detail URL
            post_match = re.match(r'^/posts/([^/]+)/$', next_url)
            if post_match:
                slug = post_match.group(1)
                try:
                    # Find the post and redirect to edit page
                    post = Post.objects.get(slug=slug)
                    edit_url = reverse('admin:posts_post_change', args=[post.pk])
                    return redirect(edit_url)
                except Post.DoesNotExist:
                    # Post not found, redirect to admin home
                    return redirect(reverse('admin:index'))

        response = self.get_response(request)

        # Also handle post-login redirects
        if (request.path == '/admin/login/' and
            request.method == 'POST' and
            response.status_code == 302 and
            request.user.is_authenticated and
            request.user.is_staff):

            # Get the redirect URL from response
            redirect_url = response.url

            # Check if it's a post detail URL
            post_match = re.match(r'^/posts/([^/]+)/$', redirect_url)
            if post_match:
                slug = post_match.group(1)
                try:
                    # Find the post and redirect to edit page
                    post = Post.objects.get(slug=slug)
                    edit_url = reverse('admin:posts_post_change', args=[post.pk])
                    return redirect(edit_url)
                except Post.DoesNotExist:
                    # Post not found, redirect to admin home
                    return redirect(reverse('admin:index'))

        return response