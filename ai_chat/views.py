"""The chat app, served only on the chat subdomain (settings.CHAT_ORIGIN).

The app runs in an iframe on wiki pages. The reader's API key lives in this subdomain's
browser storage, out of reach of scripts on wiki pages, and goes from the browser straight
to the AI provider. This server never sees keys or conversations: it serves the page, the
assistant's instructions and tool list (config.json), and runs the wiki tools (/tool).
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from wiki_api.mcp import TOOLS, ProtocolError, call_tool

from .models import get_chat_settings
from .prompt import system_prompt

PROVIDER_API_ORIGINS = (
    'https://api.anthropic.com',
    'https://api.openai.com',
    'https://generativelanguage.googleapis.com',
)
MAX_TOOL_REQUEST_BYTES = 10000


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'


def content_security_policy() -> str:
    """Scripts and styles only from this subdomain; network only to it and the AI providers;
    embedding only by the wiki."""
    return '; '.join([
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        f"connect-src 'self' {' '.join(PROVIDER_API_ORIGINS)}",
        f"frame-ancestors {' '.join(settings.CHAT_EMBED_ORIGINS)}",
        "base-uri 'none'",
        "form-action 'none'",
    ])


@require_GET
def app(request):
    response = render(request, 'ai_chat/app.html')
    response['Content-Security-Policy'] = content_security_policy()
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-cache'
    return response


@require_GET
def config(request):
    chat_settings = get_chat_settings()
    response = JsonResponse({
        'enabled': chat_settings.enabled,
        'instructions': system_prompt(chat_settings),
        'tools': [
            {'name': tool['name'], 'description': tool['description'], 'parameters': tool['inputSchema']}
            for tool in TOOLS
        ],
        'wiki_origin': _origin(settings.SITE_BASE_URL),
        'embed_origins': settings.CHAT_EMBED_ORIGINS,
    })
    response['Cache-Control'] = 'no-cache'
    return response


@require_POST
def tool(request):
    """Run one wiki tool: {"name": ..., "arguments": {...}} -> {"text": ..., "is_error": bool}."""
    if len(request.body) > MAX_TOOL_REQUEST_BYTES:
        return JsonResponse({'text': 'Request too large', 'is_error': True}, status=413)
    try:
        params = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'text': 'Request must be JSON', 'is_error': True}, status=400)
    if not isinstance(params, dict):
        return JsonResponse({'text': 'Request must be a JSON object', 'is_error': True}, status=400)
    try:
        result = call_tool(params)
    except ProtocolError as exc:
        return JsonResponse({'text': str(exc), 'is_error': True})
    return JsonResponse({'text': result['content'][0]['text'], 'is_error': result['isError']})


def not_found(request):
    return HttpResponse('Not found', status=404, content_type='text/plain')
