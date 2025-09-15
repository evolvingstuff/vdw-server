# Feature: Admin Login Redirect to Previous Page

## Goal
When clicking the admin link from a post page and logging in, automatically redirect to the editing screen for that specific post instead of the default admin dashboard.

## Current System Analysis

### Existing Components
1. **Admin Link Location**: `/templates/components/global_search_bar.html` line 27
   - Currently: `<a href="/admin/" class="nav-link">Admin</a>`
   - Included in all pages via `base.html`

2. **URL Structure**:
   - Post detail view: `/posts/<slug>/` (handled by `post_detail` view)
   - Admin post edit: `/admin/posts/post/<id>/change/` (Django admin pattern)
   - Admin login: `/admin/login/` (Django's built-in)

3. **Django Admin Behavior**:
   - When not authenticated, visiting `/admin/` redirects to `/admin/login/?next=/admin/`
   - Django admin automatically handles the `next` parameter after successful login
   - The `next` parameter is preserved through the login form as a hidden field

## Implementation Strategy

### Approach: Client-Side + Middleware Solution

We'll use a combination of:
1. **Client-side JavaScript** to dynamically set the admin link with proper `next` parameter
2. **Custom middleware** to intercept and transform post URLs to edit URLs after login

### Why This Approach?
- **No admin override needed**: Works with Django's existing admin authentication
- **Clean separation**: JavaScript handles link generation, middleware handles redirect logic
- **Secure**: Middleware can validate permissions and URLs server-side
- **Flexible**: Easy to extend for other page types in the future

## Detailed Implementation

### 1. Update Admin Link (JavaScript in global_search_bar.html)
```javascript
// Dynamically set admin link based on current page
document.addEventListener('DOMContentLoaded', function() {
    const adminLink = document.querySelector('.nav-right a[href="/admin/"]');
    if (adminLink) {
        const currentPath = window.location.pathname;

        // Check if we're on a post detail page
        const postMatch = currentPath.match(/^\/posts\/([^\/]+)\/$/);
        if (postMatch) {
            // For post pages, set next to current URL
            // Middleware will handle transformation to edit URL
            adminLink.href = `/admin/login/?next=${encodeURIComponent(currentPath)}`;
        } else if (currentPath !== '/') {
            // For other pages, just preserve the current URL
            adminLink.href = `/admin/login/?next=${encodeURIComponent(currentPath)}`;
        }
        // Homepage keeps default /admin/ link
    }
});
```

### 2. Create Custom Middleware (vdw_server/middleware.py)
```python
import re
from django.shortcuts import redirect
from django.urls import reverse
from posts.models import Post

class AdminPostRedirectMiddleware:
    """
    Middleware to transform post view URLs to edit URLs after admin login.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only process successful admin login redirects
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
```

### 3. Register Middleware (settings.py)
Add to MIDDLEWARE list after AuthenticationMiddleware:
```python
MIDDLEWARE = [
    ...
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'vdw_server.middleware.AdminPostRedirectMiddleware',  # Add this line
    ...
]
```

## Alternative Approaches Considered

### 1. Override AdminSite.login() - REJECTED
- **Why rejected**: Too invasive, replaces core Django functionality
- **Risk**: May break with Django updates

### 2. Custom Login View - REJECTED
- **Why rejected**: Requires replacing admin login URL pattern
- **Risk**: Loses Django admin's built-in security features

### 3. Server-side Template Logic Only - REJECTED
- **Why rejected**: Can't easily access request.path in included template
- **Risk**: Would need context processor, more complex

## Security Considerations

1. **URL Validation**: Middleware only processes specific patterns
2. **Permission Check**: User must be authenticated and staff
3. **Safe Redirects**: Only redirects within the same domain
4. **Fallback**: If post doesn't exist, redirect to admin home
5. **No Open Redirects**: Pattern matching prevents arbitrary URL redirects

## Edge Cases Handled

1. **Post doesn't exist**: Redirect to admin dashboard
2. **User lacks permission**: Django admin handles this automatically
3. **Direct admin access**: Works normally (no `next` parameter)
4. **Homepage admin link**: Stays as default `/admin/`
5. **Non-post pages**: Preserves original URL for future use

## Testing Checklist
- [ ] Homepage: Admin link goes to `/admin/` (no next parameter)
- [ ] Post page: Admin link includes `?next=/posts/slug/`
- [ ] After login from post: Redirects to `/admin/posts/post/<id>/change/`
- [ ] After login from homepage: Redirects to `/admin/`
- [ ] Invalid post slug: Redirects to admin dashboard
- [ ] Non-staff user: Can't access admin at all
- [ ] Direct admin URL access: Works normally
- [ ] Logout and re-login: Preserves redirect behavior