from django.conf import settings

from .models import get_chat_settings


def ai_chat(_request):
    """Where wiki pages load the chat panel from; empty when the chat is off or not set up."""
    origin = settings.CHAT_ORIGIN
    if not origin or not get_chat_settings().enabled:
        origin = ''
    return {'ai_chat_origin': origin}
