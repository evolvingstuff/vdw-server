"""VitaminDWiki as an MCP server: the connector people add to their own ChatGPT or Claude.

Public, read-only and stateless, at POST /mcp (Streamable HTTP with plain JSON replies, no
event streams, no sessions). Its tools answer from the same functions as the JSON API
(queries.py).

Two protocol eras are served, because clients move to new versions at their own pace:
- 2026-07-28 ("modern"): no handshake. Every request carries its protocol version in
  params._meta and in the MCP-Protocol-Version header; the client learns about the server
  with server/discover.
- 2025-03-26 to 2025-11-25 ("legacy"): the client starts with initialize, then sends
  notifications/initialized; ping exists. No session IDs are issued (they were optional).

`search` and `fetch` use the input shapes ChatGPT expects for deep research ({query}, {id}).
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable, Dict, Optional

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from tags.services import get_topic_index

from . import queries
from .queries import ApiError

MODERN_VERSION = '2026-07-28'
LEGACY_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')  # newest first
SUPPORTED_VERSIONS = (MODERN_VERSION, *LEGACY_VERSIONS)

META_PROTOCOL_VERSION = 'io.modelcontextprotocol/protocolVersion'
META_CLIENT_CAPABILITIES = 'io.modelcontextprotocol/clientCapabilities'
META_SERVER_INFO = 'io.modelcontextprotocol/serverInfo'

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

RESULT_TTL_MS = 3600000  # tool list and server description change only with a deploy
FETCH_CHARS = 40000  # under Claude's ~150,000-character limit even with the JSON copy
SEARCH_RESULTS = 15
BASE64_SENTINEL_RE = re.compile(r'^=\?base64\?(?P<data>.*)\?=$')

SERVER_INFO = {
    'name': 'vitamindwiki',
    'title': 'VitaminDWiki',
    'version': '1.0.0',
    'description': 'Search and read VitaminDWiki, a curated collection of vitamin D research.',
}

READ_ONLY = {
    'readOnlyHint': True,
    'destructiveHint': False,
    'idempotentHint': True,
    'openWorldHint': False,
}

PAGE_TYPES_TEXT = (
    'Page types, most important first: summary, overview, extended, many studies, '
    'several studies, meta-analysis, RCT, category, page (most single-study pages).'
)


def instructions() -> str:
    """How to use the wiki well; the first 512 characters carry the essentials."""
    pages = len(get_topic_index().pages) // 100 * 100
    return (
        f"VitaminDWiki (vitamindwiki.com) is a curated collection of vitamin D research: {pages:,}+ pages, "
        "most summarizing one published study, plus overviews of health topics. Answer from these pages and "
        "give the page URL for every claim. Start with overview pages, then meta-analyses, then RCTs. Page "
        "titles state each study's headline finding. Pages summarize research; they are not medical advice.\n\n"
        "Searching well:\n"
        "- search matches words, not meanings: use short key words and try synonyms (e.g. COVID, coronavirus).\n"
        "- Every page has topics. find_topics gives topic slugs; topic_pages lists a topic's pages, and with "
        "several slugs only pages on all of them (e.g. pregnancy + asthma). sort=recent shows the latest research.\n"
        "- fetch a page before quoting its numbers. Long pages continue with continue_page.\n"
        f"- {PAGE_TYPES_TEXT}\n\n"
        "Dosing questions: report what the wiki's pages say, with links, and do not give anyone a personal dose. "
        "If the person mentions kidney disease, high blood calcium, sarcoidosis or medications, say clearly that "
        "high vitamin D doses can be risky for them and that their doctor should guide the dose."
    )


# --- tools -----------------------------------------------------------------------------

def _string(description: str, **extra) -> dict:
    return {'type': 'string', 'description': description, **extra}


def _integer(description: str, minimum: int, maximum: int, default: int) -> dict:
    return {'type': 'integer', 'description': description, 'minimum': minimum, 'maximum': maximum, 'default': default}


TOOLS = [
    {
        'name': 'search',
        'title': 'Search VitaminDWiki',
        'description': (
            f'Search VitaminDWiki pages by words in their title or text. Returns up to {SEARCH_RESULTS} pages, '
            'most important first (overviews and summaries, then meta-analyses, RCTs, then other pages, newest '
            'first within each). Each result has id (pass it to fetch), title, url, type and updated date. '
            'Titles state each study\'s headline finding.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {'query': _string('Key words, e.g. "vitamin D asthma children".')},
            'required': ['query'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'fetch',
        'title': 'Read a VitaminDWiki page',
        'description': (
            f'Read one page by its id (from search or the other tools): title, url, and its text as Markdown, '
            f'up to {FETCH_CHARS:,} characters, with links to the original studies. metadata has the page '
            'type, dates and topics; if metadata.next_start is set the page is longer, so call continue_page.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {'id': _string('Page id (its slug), e.g. "overview-asthma-and-vitamin-d".')},
            'required': ['id'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'continue_page',
        'title': 'Continue reading a long page',
        'description': 'Read more of a long page, from the position fetch or a previous call gave as next_start.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'id': _string('Page id (its slug).'),
                'start': {'type': 'integer', 'minimum': 0, 'description': 'next_start from the previous read.'},
            },
            'required': ['id', 'start'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'find_topics',
        'title': 'Find VitaminDWiki topics',
        'description': (
            'Find topics by name, closest match first, with how many pages each has. Use the slugs with '
            'topic_pages. With no query, lists the biggest topics.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': _string('Part of a topic name, e.g. "pregnan" or "autism".'),
                'limit': _integer('How many topics to return.', 1, queries.MAX_TOPICS_LIMIT, 20),
            },
            'additionalProperties': False,
        },
    },
    {
        'name': 'topic_pages',
        'title': 'List pages on topics',
        'description': (
            'List the pages that have every given topic: one topic for everything on it, several for pages on '
            'all of them (e.g. ["pregnancy", "asthma"]). sort "important" puts overviews, meta-analyses and RCTs '
            'first; "recent" puts the newest first.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'topics': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'minItems': 1,
                    'maxItems': queries.MAX_COMBINED_TOPICS,
                    'description': 'Topic slugs from find_topics.',
                },
                'sort': {'type': 'string', 'enum': list(queries.SORTS), 'default': 'important'},
                'limit': _integer('How many pages to return.', 1, queries.MAX_LIMIT, SEARCH_RESULTS),
                'offset': _integer('How many pages to skip, to see more.', 0, 100000, 0),
            },
            'required': ['topics'],
            'additionalProperties': False,
        },
    },
    {
        'name': 'recent_pages',
        'title': 'Newest VitaminDWiki pages',
        'description': 'The most recently added or updated pages, newest first.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'limit': _integer('How many pages to return.', 1, queries.MAX_LIMIT, SEARCH_RESULTS),
                'offset': _integer('How many pages to skip, to see more.', 0, 100000, 0),
            },
            'additionalProperties': False,
        },
    },
]
for _tool in TOOLS:
    _tool['annotations'] = {'title': _tool['title'], **READ_ONLY}
TOOLS_BY_NAME = {tool['name']: tool for tool in TOOLS}


def _check_arguments(tool: dict, arguments: Any) -> dict:
    """Check arguments against the tool's (simple) schema; raise ApiError on mistakes."""
    if not isinstance(arguments, dict):
        raise ApiError('arguments must be an object')
    schema = tool['inputSchema']
    properties = schema['properties']
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise ApiError(f"unknown argument(s): {', '.join(unknown)}")
    missing = [name for name in schema.get('required', []) if name not in arguments]
    if missing:
        raise ApiError(f"missing argument(s): {', '.join(missing)}")
    checked = {}
    for name, value in arguments.items():
        expected = properties[name]['type']
        if expected == 'string' and not isinstance(value, str):
            raise ApiError(f'{name} must be a string')
        if expected == 'integer' and (isinstance(value, bool) or not isinstance(value, int)):
            raise ApiError(f'{name} must be a whole number')
        if expected == 'array' and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise ApiError(f'{name} must be a list of strings')
        if 'enum' in properties[name] and value not in properties[name]['enum']:
            raise ApiError(f"{name} must be one of: {', '.join(properties[name]['enum'])}")
        checked[name] = value
    return checked


def _fetch_result(page: dict) -> dict:
    return {
        'id': page['slug'],
        'title': page['title'],
        'text': page['text'],
        'url': page['url'],
        'metadata': {
            'type': page['type'],
            'created': page['created'],
            'updated': page['updated'],
            'topics': [t['slug'] for t in page['topics']],
            'length': page['length'],
            'start': page['start'],
            'next_start': page['next_start'],
        },
    }


def _search_result(found: dict) -> dict:
    return {
        'results': [
            {'id': r['slug'], 'title': r['title'], 'url': r['url'], 'type': r['type'], 'updated': r['updated']}
            for r in found['results']
        ],
        'total': found['total'],
    }


TOOL_HANDLERS: Dict[str, Callable[[dict], dict]] = {
    'search': lambda a: _search_result(queries.search(a['query'], limit=SEARCH_RESULTS)),
    'fetch': lambda a: _fetch_result(queries.page(a['id'], max_chars=FETCH_CHARS)),
    'continue_page': lambda a: _fetch_result(queries.page(a['id'], start=a['start'], max_chars=FETCH_CHARS)),
    'find_topics': lambda a: queries.topics(a.get('query', ''), limit=a.get('limit', 20)),
    'topic_pages': lambda a: queries.topic_pages(
        a['topics'], sort=a.get('sort', 'important'),
        limit=a.get('limit', SEARCH_RESULTS), offset=a.get('offset', 0),
    ),
    'recent_pages': lambda a: queries.recent(limit=a.get('limit', SEARCH_RESULTS), offset=a.get('offset', 0)),
}


def call_tool(params: dict) -> dict:
    """tools/call: a protocol error for an unknown tool, otherwise a result (isError on mistakes)."""
    name = params.get('name')
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ProtocolError(INVALID_PARAMS, f'Unknown tool: {name}')
    try:
        structured = TOOL_HANDLERS[name](_check_arguments(tool, params.get('arguments') or {}))
    except ApiError as exc:
        return {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
    return {
        'content': [{'type': 'text', 'text': json.dumps(structured, ensure_ascii=False)}],
        'structuredContent': structured,
        'isError': False,
    }


# --- JSON-RPC over HTTP --------------------------------------------------------------

class ProtocolError(Exception):
    def __init__(self, code: int, message: str, status: int = 200, data: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.data = data


def _error_response(request_id, error: ProtocolError) -> JsonResponse:
    body = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': error.code, 'message': str(error)}}
    if error.data is not None:
        body['error']['data'] = error.data
    return JsonResponse(body, status=error.status)


def _decode_header(value: str) -> str:
    match = BASE64_SENTINEL_RE.match(value)
    if not match:
        return value
    try:
        return base64.b64decode(match.group('data'), validate=True).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        raise ProtocolError(HEADER_MISMATCH, 'Mcp-Name is not valid base64', status=400)


def _handle_modern(request, method: str, params: dict, meta: dict) -> dict:
    version = meta[META_PROTOCOL_VERSION]
    if version != MODERN_VERSION:
        raise ProtocolError(
            UNSUPPORTED_PROTOCOL_VERSION, f'Unsupported protocol version: {version}', status=400,
            data={'supported': list(SUPPORTED_VERSIONS), 'requested': version},
        )
    if not isinstance(meta.get(META_CLIENT_CAPABILITIES), dict):
        raise ProtocolError(INVALID_PARAMS, f'params._meta must include {META_CLIENT_CAPABILITIES}', status=400)

    expected_headers = {'MCP-Protocol-Version': version, 'Mcp-Method': method}
    if method == 'tools/call':
        expected_headers['Mcp-Name'] = params.get('name')
    for header, expected in expected_headers.items():
        value = request.headers.get(header)
        if value is None:
            raise ProtocolError(HEADER_MISMATCH, f'Missing {header} header', status=400)
        if _decode_header(value) != expected:
            raise ProtocolError(HEADER_MISMATCH, f'{header} header does not match the request body', status=400)

    if method == 'server/discover':
        result = {
            'supportedVersions': list(SUPPORTED_VERSIONS),
            'capabilities': {'tools': {}},
            'instructions': instructions(),
            'ttlMs': RESULT_TTL_MS,
            'cacheScope': 'public',
        }
    elif method == 'tools/list':
        result = {'tools': TOOLS, 'ttlMs': RESULT_TTL_MS, 'cacheScope': 'public'}
    elif method == 'tools/call':
        result = call_tool(params)
    else:
        raise ProtocolError(METHOD_NOT_FOUND, f'Method not found: {method}', status=404)
    return {'resultType': 'complete', **result, '_meta': {META_SERVER_INFO: SERVER_INFO}}


def _handle_legacy(request, method: str, params: dict) -> dict:
    if method == 'initialize':
        requested = params.get('protocolVersion')
        return {
            'protocolVersion': requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0],
            'capabilities': {'tools': {}},
            'serverInfo': {**SERVER_INFO, 'websiteUrl': settings.SITE_BASE_URL},
            'instructions': instructions(),
        }
    header_version = request.headers.get('MCP-Protocol-Version')
    if header_version is not None and header_version not in LEGACY_VERSIONS:
        raise ProtocolError(
            UNSUPPORTED_PROTOCOL_VERSION, f'Unsupported protocol version: {header_version}', status=400,
            data={'supported': list(SUPPORTED_VERSIONS), 'requested': header_version},
        )
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        return call_tool(params)
    raise ProtocolError(METHOD_NOT_FOUND, f'Method not found: {method}')


@csrf_exempt
def mcp_endpoint(request):
    origin = request.headers.get('Origin')
    if origin and origin not in settings.MCP_ALLOWED_ORIGINS:
        return _error_response(None, ProtocolError(INVALID_REQUEST, 'Origin not allowed', status=403))
    if request.method != 'POST':
        response = HttpResponse(status=405)
        response['Allow'] = 'POST'
        return response

    try:
        message = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return _error_response(None, ProtocolError(PARSE_ERROR, 'Parse error', status=400))
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return _error_response(None, ProtocolError(INVALID_REQUEST, 'Expected one JSON-RPC 2.0 request', status=400))

    if 'id' not in message:
        return HttpResponse(status=202)  # a notification, e.g. notifications/initialized

    request_id = message['id']
    method = message['method']
    params = message.get('params') or {}
    if not isinstance(params, dict):
        return _error_response(request_id, ProtocolError(INVALID_PARAMS, 'params must be an object', status=400))
    meta = params.get('_meta')
    try:
        if isinstance(meta, dict) and META_PROTOCOL_VERSION in meta:
            result = _handle_modern(request, method, params, meta)
        else:
            result = _handle_legacy(request, method, params)
    except ProtocolError as exc:
        return _error_response(request_id, exc)
    return JsonResponse({'jsonrpc': '2.0', 'id': request_id, 'result': result})
