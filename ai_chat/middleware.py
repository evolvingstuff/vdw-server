from urllib.parse import urlsplit

from django.conf import settings
from django.urls import Resolver404, resolve

from . import views


class ChatHostMiddleware:
    """Serve the chat app on its own subdomain (settings.CHAT_ORIGIN) and nothing else there.

    Requests to that host skip the rest of the site: no sessions, cookies, logins, redirects
    or wiki pages exist on the chat subdomain.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.chat_host = urlsplit(settings.CHAT_ORIGIN).netloc.lower() if settings.CHAT_ORIGIN else ''

    def __call__(self, request):
        if not self.chat_host or request.get_host().lower() != self.chat_host:
            return self.get_response(request)
        try:
            match = resolve(request.path_info, urlconf='ai_chat.chat_urls')
        except Resolver404:
            return views.not_found(request)
        return match.func(request, *match.args, **match.kwargs)
