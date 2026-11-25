import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from pages.models import Page
from tags.services import reset_topic_index

from .models import ChatSettings, clear_chat_settings_cache, get_chat_settings

CHAT_ORIGIN = 'https://chat.example.com'
CHAT_HOST = 'chat.example.com'


@override_settings(
    ALLOWED_HOSTS=['*'],
    TOPIC_INDEX_BACKGROUND_REFRESH=False,
    SITE_BASE_URL='https://wiki.example.com',
    CHAT_ORIGIN=CHAT_ORIGIN,
    CHAT_EMBED_ORIGINS=['https://wiki.example.com'],
)
class ChatTestCase(TestCase):
    def setUp(self):
        reset_topic_index()
        self.addCleanup(reset_topic_index)
        clear_chat_settings_cache()
        self.addCleanup(clear_chat_settings_cache)
        for target in ('pages.signals.index_page', 'pages.signals.remove_page_from_search'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def chat_get(self, path):
        return self.client.get(path, HTTP_HOST=CHAT_HOST)

    def call_tool(self, payload):
        return self.client.post('/tool', data=json.dumps(payload), content_type='application/json', HTTP_HOST=CHAT_HOST)


class ChatSubdomainTests(ChatTestCase):
    def test_app_page_is_locked_down_and_frameable_only_by_the_wiki(self):
        response = self.chat_get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ai_chat/chat.js')
        policy = response['Content-Security-Policy']
        self.assertIn("script-src 'self'", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertIn('frame-ancestors https://wiki.example.com', policy)
        self.assertIn('https://api.anthropic.com', policy)
        self.assertNotIn('X-Frame-Options', response)
        self.assertNotIn('<script>', response.content.decode())  # no inline scripts: the policy would block them

    def test_chat_subdomain_sets_no_cookies_and_serves_no_wiki_pages(self):
        self.assertEqual(self.chat_get('/').cookies, {})
        for path in ('/pages/', '/admin/', '/api/search/?q=x', '/mcp', '/use-with-ai/'):
            self.assertEqual(self.chat_get(path).status_code, 404, path)

    def test_wiki_host_does_not_serve_the_chat_app(self):
        self.assertEqual(self.client.get('/config.json').status_code, 404)
        # There it is an ordinary unknown address (redirected to /tool/, which is no page).
        tool = self.client.post('/tool', data='{}', content_type='application/json', follow=True)
        self.assertEqual(tool.status_code, 404)

    def test_config_has_instructions_tools_and_origins(self):
        config = self.chat_get('/config.json').json()

        self.assertTrue(config['enabled'])
        self.assertEqual(config['wiki_origin'], 'https://wiki.example.com')
        self.assertEqual(config['embed_origins'], ['https://wiki.example.com'])
        self.assertEqual(
            [tool['name'] for tool in config['tools']],
            ['search', 'fetch', 'continue_page', 'find_topics', 'topic_pages', 'recent_pages'],
        )
        self.assertEqual(config['tools'][0]['parameters']['required'], ['query'])
        self.assertIn('Answer from VitaminDWiki pages first', config['instructions'])  # the default strictness
        self.assertIn('do not give anyone a personal dose', config['instructions'])

    def test_tool_reads_a_page(self):
        Page.objects.create(title='Asthma overview', slug='asthma-overview', content_md='Vitamin D and asthma.', status='published')

        response = self.call_tool({'name': 'fetch', 'arguments': {'id': 'asthma-overview'}})

        result = response.json()
        self.assertFalse(result['is_error'])
        page = json.loads(result['text'])
        self.assertEqual(page['title'], 'Asthma overview')
        self.assertEqual(page['url'], 'https://wiki.example.com/pages/asthma-overview/')
        self.assertIn('Vitamin D and asthma.', page['text'])

    def test_tool_mistakes_come_back_as_errors_the_model_can_read(self):
        missing = self.call_tool({'name': 'fetch', 'arguments': {'id': 'no-such-page'}}).json()
        self.assertTrue(missing['is_error'])
        self.assertIn('No published page', missing['text'])

        unknown = self.call_tool({'name': 'delete_everything', 'arguments': {}}).json()
        self.assertTrue(unknown['is_error'])
        self.assertIn('Unknown tool', unknown['text'])

        bad_arguments = self.call_tool({'name': 'search', 'arguments': {}}).json()
        self.assertTrue(bad_arguments['is_error'])

    def test_tool_rejects_bad_requests(self):
        self.assertEqual(self.chat_get('/tool').status_code, 405)
        not_json = self.client.post('/tool', data='nope', content_type='application/json', HTTP_HOST=CHAT_HOST)
        self.assertEqual(not_json.status_code, 400)
        too_large = self.call_tool({'name': 'search', 'arguments': {'query': 'x' * 20000}})
        self.assertEqual(too_large.status_code, 413)


class ChatSettingsTests(ChatTestCase):
    def test_strictness_and_extra_instructions_reach_the_assistant(self):
        chat_settings = get_chat_settings()
        chat_settings.strictness = ChatSettings.WIKI_ONLY
        chat_settings.extra_instructions = 'Mention that official limits differ by country.'
        chat_settings.save()

        instructions = self.chat_get('/config.json').json()['instructions']

        self.assertIn('Answer only from VitaminDWiki pages', instructions)
        self.assertNotIn('Answer from VitaminDWiki pages first', instructions)
        self.assertIn('official limits differ by country', instructions)

    def test_there_is_only_ever_one_settings_record(self):
        get_chat_settings()
        ChatSettings(strictness=ChatSettings.OPEN).save()

        self.assertEqual(ChatSettings.objects.count(), 1)
        self.assertEqual(get_chat_settings().strictness, ChatSettings.OPEN)


class ChatButtonTests(ChatTestCase):
    def test_wiki_pages_show_the_chat_button(self):
        response = self.client.get('/tags/')

        self.assertContains(response, 'id="ai-chat-toggle"')
        self.assertContains(response, f'data-origin="{CHAT_ORIGIN}"')

    def test_button_is_hidden_when_switched_off_in_the_admin(self):
        chat_settings = get_chat_settings()
        chat_settings.enabled = False
        chat_settings.save()

        self.assertNotContains(self.client.get('/tags/'), 'ai-chat-toggle')
        self.assertFalse(self.chat_get('/config.json').json()['enabled'])

    @override_settings(CHAT_ORIGIN='')
    def test_button_is_hidden_when_no_chat_subdomain_is_set_up(self):
        self.assertNotContains(self.client.get('/tags/'), 'ai-chat-toggle')
