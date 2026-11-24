import base64
import json
from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from pages.models import Page
from tags.models import Tag
from tags.services import reset_topic_index
from wiki_api.mcp import FETCH_CHARS, MODERN_VERSION, TOOLS, instructions

MODERN_META = {
    'io.modelcontextprotocol/protocolVersion': MODERN_VERSION,
    'io.modelcontextprotocol/clientCapabilities': {},
    'io.modelcontextprotocol/clientInfo': {'name': 'test-client', 'version': '1.0'},
}


@override_settings(
    ALLOWED_HOSTS=['*'],
    TOPIC_INDEX_BACKGROUND_REFRESH=False,
    SITE_BASE_URL='https://wiki.example.com',
    MCP_ALLOWED_ORIGINS=['http://localhost:6274'],
)
class McpTests(TestCase):
    def setUp(self):
        reset_topic_index()
        self.addCleanup(reset_topic_index)
        for target in ('pages.signals.index_page', 'pages.signals.remove_page_from_search'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.bone = Tag.objects.create(name='Bone', slug='bone')
        self.infant = Tag.objects.create(name='Infant-Child', slug='infant-child')
        self.overview_tag = Tag.objects.create(name='Overview', slug='overview')
        self.overview = self._page('Overview Bone and vitamin D', [self.bone, self.overview_tag], 2010,
                                   content_md='See [study](/pages/infant-bones/).')
        self.infant_page = self._page('Infant bones and vitamin D', [self.bone, self.infant], 2024)
        self.long_page = self._page('Long bone review', [self.bone], 2022, content_md='x' * (FETCH_CHARS + 500))

    def _page(self, title, tags, year, content_md='Text.'):
        page = Page.objects.create(title=title, status='published', content_md=content_md)
        page.tags.set(tags)
        Page.objects.filter(pk=page.pk).update(
            public_modified_date=timezone.make_aware(datetime(year, 1, 1), datetime_timezone.utc)
        )
        return page

    def _post(self, message, path='/mcp', **headers):
        body = message if isinstance(message, (str, bytes)) else json.dumps(message)
        return self.client.post(path, data=body, content_type='application/json', **headers)

    # --- legacy (initialize-based) clients ------------------------------------------------

    def _legacy(self, method, params=None, request_id=1, **headers):
        message = {'jsonrpc': '2.0', 'id': request_id, 'method': method}
        if params is not None:
            message['params'] = params
        return self._post(message, **headers)

    def _legacy_call(self, name, arguments):
        response = self._legacy('tools/call', {'name': name, 'arguments': arguments},
                                HTTP_MCP_PROTOCOL_VERSION='2025-06-18')
        self.assertEqual(response.status_code, 200)
        return response.json()['result']

    def test_initialize_agrees_on_a_supported_version(self):
        response = self._legacy('initialize', {
            'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'c', 'version': '1'},
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['id'], 1)
        result = data['result']
        self.assertEqual(result['protocolVersion'], '2025-06-18')
        self.assertEqual(result['capabilities'], {'tools': {}})
        self.assertEqual(result['serverInfo']['name'], 'vitamindwiki')
        self.assertEqual(result['serverInfo']['websiteUrl'], 'https://wiki.example.com')
        self.assertIn('VitaminDWiki', result['instructions'])
        self.assertNotIn('Mcp-Session-Id', response)

    def test_initialize_with_an_unknown_version_offers_the_newest_legacy_one(self):
        result = self._legacy('initialize', {'protocolVersion': '2024-01-01'}).json()['result']
        self.assertEqual(result['protocolVersion'], '2025-11-25')

    def test_notifications_are_accepted_without_a_body(self):
        response = self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.content, b'')

    def test_ping(self):
        self.assertEqual(self._legacy('ping').json()['result'], {})

    def test_tools_list_all_read_only(self):
        tools = self._legacy('tools/list').json()['result']['tools']
        self.assertEqual(
            [t['name'] for t in tools],
            ['search', 'fetch', 'continue_page', 'find_topics', 'topic_pages', 'recent_pages'],
        )
        for tool in tools:
            self.assertTrue(tool['annotations']['readOnlyHint'], tool['name'])
            self.assertEqual(tool['inputSchema']['type'], 'object', tool['name'])
            self.assertFalse(tool['inputSchema']['additionalProperties'], tool['name'])

    def test_search_returns_ids_titles_urls(self):
        hits = {'hits': [{'id': 1, 'title': 'Overview Bone', 'slug': 'overview-bone',
                          'search_priority': 7, 'modified_date': 1704067200}], 'totalHits': 1}
        with patch('wiki_api.queries.search_pages', return_value=hits) as search_pages:
            result = self._legacy_call('search', {'query': 'bone'})
        search_pages.assert_called_once_with('bone', limit=15, offset=0)
        self.assertFalse(result['isError'])
        self.assertEqual(result['structuredContent']['results'], [{
            'id': 'overview-bone', 'title': 'Overview Bone', 'url': 'https://wiki.example.com/pages/overview-bone/',
            'type': 'overview', 'updated': '2024-01-01',
        }])
        self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])

    def test_search_down_says_to_use_topics(self):
        from meilisearch.errors import MeilisearchCommunicationError
        with patch('wiki_api.queries.search_pages', side_effect=MeilisearchCommunicationError('down')), \
                self.assertLogs('wiki_api.queries', level='ERROR'):
            result = self._legacy_call('search', {'query': 'bone'})
        self.assertTrue(result['isError'])
        self.assertIn('Search is temporarily unavailable', result['content'][0]['text'])

    def test_fetch_reads_a_page(self):
        result = self._legacy_call('fetch', {'id': self.overview.slug})
        page = result['structuredContent']
        self.assertEqual(page['id'], self.overview.slug)
        self.assertEqual(page['url'], f'https://wiki.example.com/pages/{self.overview.slug}/')
        self.assertIn('(https://wiki.example.com/pages/infant-bones/)', page['text'])
        self.assertEqual(page['metadata']['type'], 'overview')
        self.assertEqual(page['metadata']['topics'], ['bone', 'overview'])
        self.assertIsNone(page['metadata']['next_start'])

    def test_long_page_continues(self):
        first = self._legacy_call('fetch', {'id': self.long_page.slug})['structuredContent']
        self.assertEqual(len(first['text']), FETCH_CHARS)
        self.assertEqual(first['metadata']['next_start'], FETCH_CHARS)
        rest = self._legacy_call('continue_page', {'id': self.long_page.slug, 'start': FETCH_CHARS})
        self.assertEqual(len(rest['structuredContent']['text']), 500)
        self.assertIsNone(rest['structuredContent']['metadata']['next_start'])

    def test_topics_and_topic_pages_and_recent(self):
        found = self._legacy_call('find_topics', {'query': 'bon'})['structuredContent']
        self.assertEqual(found['results'][0]['slug'], 'bone')
        listed = self._legacy_call('topic_pages', {'topics': ['bone', 'infant-child']})['structuredContent']
        self.assertEqual([r['title'] for r in listed['results']], ['Infant bones and vitamin D'])
        newest = self._legacy_call('recent_pages', {'limit': 1})['structuredContent']
        self.assertEqual([r['title'] for r in newest['results']], ['Infant bones and vitamin D'])

    def test_tool_mistakes_are_tool_errors_the_model_can_read(self):
        cases = [
            ('fetch', {'id': 'no-such-page'}, 'No published page'),
            ('fetch', {}, 'missing argument(s): id'),
            ('fetch', {'id': 'x', 'extra': 1}, 'unknown argument(s): extra'),
            ('topic_pages', {'topics': 'bone'}, 'topics must be a list of strings'),
            ('topic_pages', {'topics': ['nope']}, 'Unknown tag'),
            ('topic_pages', {'topics': ['bone'], 'sort': 'oldest'}, 'sort must be one of'),
            ('recent_pages', {'limit': 0}, 'limit must be between'),
            ('continue_page', {'id': self.overview.slug, 'start': 10 ** 6}, 'start must be between'),
        ]
        for name, arguments, message in cases:
            result = self._legacy_call(name, arguments)
            self.assertTrue(result['isError'], (name, arguments))
            self.assertIn(message, result['content'][0]['text'], (name, arguments))

    def test_unknown_tool_is_a_protocol_error(self):
        data = self._legacy('tools/call', {'name': 'delete_everything', 'arguments': {}}).json()
        self.assertEqual(data['error']['code'], -32602)

    def test_unknown_method(self):
        data = self._legacy('resources/list').json()
        self.assertEqual(data['error']['code'], -32601)

    def test_legacy_request_with_an_unsupported_version_header(self):
        response = self._legacy('tools/list', HTTP_MCP_PROTOCOL_VERSION='2024-11-05')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], -32022)

    # --- modern (2026-07-28) clients --------------------------------------------------------

    def _modern(self, method, params=None, meta=MODERN_META, headers=None):
        message = {'jsonrpc': '2.0', 'id': 7, 'method': method, 'params': {**(params or {}), '_meta': meta}}
        sent = {'HTTP_MCP_PROTOCOL_VERSION': MODERN_VERSION, 'HTTP_MCP_METHOD': method}
        if method == 'tools/call':
            sent['HTTP_MCP_NAME'] = (params or {}).get('name')
        sent.update(headers or {})
        return self._post(message, **{k: v for k, v in sent.items() if v is not None})

    def test_server_discover(self):
        response = self._modern('server/discover')
        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertEqual(result['resultType'], 'complete')
        self.assertIn(MODERN_VERSION, result['supportedVersions'])
        self.assertEqual(result['capabilities'], {'tools': {}})
        self.assertEqual(result['cacheScope'], 'public')
        self.assertGreater(result['ttlMs'], 0)
        self.assertEqual(result['_meta']['io.modelcontextprotocol/serverInfo']['name'], 'vitamindwiki')
        self.assertIn('VitaminDWiki', result['instructions'])

    def test_modern_tools_list_and_call(self):
        listed = self._modern('tools/list').json()['result']
        self.assertEqual(listed['resultType'], 'complete')
        self.assertEqual(len(listed['tools']), len(TOOLS))
        self.assertIn('ttlMs', listed)

        called = self._modern('tools/call', {'name': 'topic_pages', 'arguments': {'topics': ['bone']}}).json()['result']
        self.assertEqual(called['resultType'], 'complete')
        self.assertFalse(called['isError'])
        self.assertEqual(called['structuredContent']['total'], 3)

    def test_base64_encoded_tool_name_header(self):
        encoded = '=?base64?' + base64.b64encode(b'recent_pages').decode() + '?='
        response = self._modern('tools/call', {'name': 'recent_pages', 'arguments': {}},
                                headers={'HTTP_MCP_NAME': encoded})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['result']['isError'])

    def test_missing_or_mismatched_headers(self):
        cases = [
            {'HTTP_MCP_METHOD': None},
            {'HTTP_MCP_PROTOCOL_VERSION': None},
            {'HTTP_MCP_METHOD': 'tools/call'},
            {'HTTP_MCP_NAME': 'fetch'},
        ]
        for headers in cases:
            message = {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                       'params': {'name': 'recent_pages', 'arguments': {}, '_meta': MODERN_META}}
            sent = {'HTTP_MCP_PROTOCOL_VERSION': MODERN_VERSION, 'HTTP_MCP_METHOD': 'tools/call',
                    'HTTP_MCP_NAME': 'recent_pages', **headers}
            if headers.get('HTTP_MCP_METHOD') == 'tools/call':
                message['method'] = 'tools/list'
            response = self._post(message, **{k: v for k, v in sent.items() if v is not None})
            self.assertEqual(response.status_code, 400, headers)
            self.assertEqual(response.json()['error']['code'], -32020, headers)

    def test_unsupported_modern_version(self):
        meta = {**MODERN_META, 'io.modelcontextprotocol/protocolVersion': '2099-01-01'}
        response = self._modern('tools/list', meta=meta, headers={'HTTP_MCP_PROTOCOL_VERSION': '2099-01-01'})
        self.assertEqual(response.status_code, 400)
        error = response.json()['error']
        self.assertEqual(error['code'], -32022)
        self.assertEqual(error['data']['requested'], '2099-01-01')
        self.assertIn(MODERN_VERSION, error['data']['supported'])

    def test_missing_client_capabilities(self):
        meta = {'io.modelcontextprotocol/protocolVersion': MODERN_VERSION}
        response = self._modern('tools/list', meta=meta)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], -32602)

    def test_modern_unknown_method_is_404(self):
        response = self._modern('ping')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], -32601)

    # --- HTTP edges ---------------------------------------------------------------------

    def test_get_and_delete_are_not_allowed(self):
        for response in (self.client.get('/mcp'), self.client.delete('/mcp')):
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response['Allow'], 'POST')

    def test_origin_checked(self):
        blocked = self._legacy('ping', HTTP_ORIGIN='https://evil.example.com')
        self.assertEqual(blocked.status_code, 403)
        allowed = self._legacy('ping', HTTP_ORIGIN='http://localhost:6274')
        self.assertEqual(allowed.status_code, 200)

    def test_bad_json_and_batches(self):
        response = self._post('{not json')
        self.assertEqual((response.status_code, response.json()['error']['code']), (400, -32700))
        response = self._post([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}])
        self.assertEqual((response.status_code, response.json()['error']['code']), (400, -32600))

    def test_trailing_slash_also_works(self):
        self.assertEqual(self._post({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, path='/mcp/').status_code, 200)

    def test_instructions_put_the_essentials_first(self):
        first = instructions()[:512]
        for phrase in ('VitaminDWiki', 'URL for every claim', 'overview', 'not medical advice'):
            self.assertIn(phrase, first)
