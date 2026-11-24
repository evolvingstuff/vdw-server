from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from pages.models import Page
from tags.models import Tag
from tags.services import reset_topic_index

CHAT_ORIGIN = 'https://chat.example.com'


@override_settings(
    ALLOWED_HOSTS=['*'],
    TOPIC_INDEX_BACKGROUND_REFRESH=False,
    SITE_BASE_URL='https://wiki.example.com',
    WIKI_API_CORS_ORIGINS=[CHAT_ORIGIN],
)
class WikiApiTests(TestCase):
    def setUp(self):
        reset_topic_index()
        self.addCleanup(reset_topic_index)
        for target in ('pages.signals.index_page', 'pages.signals.remove_page_from_search'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.bone = Tag.objects.create(name='Bone', slug='bone')
        self.infant = Tag.objects.create(name='Infant-Child', slug='infant-child')
        self.bone_density = Tag.objects.create(name='Low bone density', slug='low-bone-density')
        self.rct = Tag.objects.create(name='RCT', slug='rct')
        self.overview_tag = Tag.objects.create(name='Overview', slug='overview')

        self.old_rct = self._page('Infant bone density – RCT', [self.bone, self.infant], 2020)
        self.new_plain = self._page('Infant bones and vitamin D', [self.bone, self.infant], 2024)
        self.overview = self._page('Overview Bone and vitamin D', [self.bone, self.overview_tag], 2010)
        self.draft = self._page('Draft about bone', [self.bone, self.infant], 2025, status='draft')

    def _page(self, title, tags, year, status='published', content_md='Text.'):
        page = Page.objects.create(title=title, status=status, content_md=content_md)
        page.tags.set(tags)
        Page.objects.filter(pk=page.pk).update(
            public_modified_date=timezone.make_aware(datetime(year, 1, 1), datetime_timezone.utc)
        )
        return page

    def _get(self, path, **headers):
        response = self.client.get(path, **headers)
        return response, response.json()

    # --- topic pages -------------------------------------------------------------

    def test_topic_pages_most_important_first(self):
        response, data = self._get('/api/topic-pages/?topics=bone')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['sort'], 'important')
        self.assertEqual(
            [(r['title'], r['type']) for r in data['results']],
            [
                ('Overview Bone and vitamin D', 'overview'),
                ('Infant bone density – RCT', 'RCT'),
                ('Infant bones and vitamin D', 'page'),
            ],
        )
        first = data['results'][0]
        self.assertEqual(first['url'], f'https://wiki.example.com/pages/{self.overview.slug}/')
        self.assertEqual(first['slug'], self.overview.slug)
        self.assertEqual(first['updated'], '2010-01-01')

    def test_topic_pages_newest_first(self):
        _, data = self._get('/api/topic-pages/?topics=bone&sort=recent')
        self.assertEqual(
            [r['updated'] for r in data['results']],
            ['2024-01-01', '2020-01-01', '2010-01-01'],
        )

    def test_topic_pages_combined_topics_skip_drafts(self):
        _, data = self._get('/api/topic-pages/?topics=bone,infant-child')
        self.assertEqual(data['total'], 2)
        self.assertEqual([t['slug'] for t in data['topics']], ['bone', 'infant-child'])
        self.assertNotIn('Draft about bone', [r['title'] for r in data['results']])

    def test_topic_pages_limit_and_offset(self):
        _, data = self._get('/api/topic-pages/?topics=bone&limit=1&offset=1')
        self.assertEqual(data['total'], 3)
        self.assertEqual([r['title'] for r in data['results']], ['Infant bone density – RCT'])

    def test_topic_pages_unknown_topic_is_json_404(self):
        response, data = self._get('/api/topic-pages/?topics=bone,nope')
        self.assertEqual(response.status_code, 404)
        self.assertIn('nope', data['error'])

    def test_topic_pages_bad_input(self):
        for query in ('', '?topics=bone&sort=oldest', '?topics=bone&limit=0', '?topics=bone&limit=x',
                      '?topics=a,b,c,d,e,f'):
            response, data = self._get(f'/api/topic-pages/{query}')
            self.assertEqual(response.status_code, 400, query)
            self.assertIn('error', data)

    # --- topics ------------------------------------------------------------------

    def test_topics_closest_names_first(self):
        self._page('Osteopenia in seniors', [self.bone_density], 2022)
        _, data = self._get('/api/topics/?q=bone')
        # The exact name first (despite fewer pages it would still lead), then a word starting with it.
        self.assertEqual([t['slug'] for t in data['results']], ['bone', 'low-bone-density'])
        self.assertEqual([t['pages'] for t in data['results']], [3, 1])

    def test_topics_without_pages_are_left_out(self):
        _, data = self._get('/api/topics/?q=density')
        self.assertEqual(data['results'], [])

    def test_topics_without_query_lists_biggest_first(self):
        _, data = self._get('/api/topics/?limit=2')
        self.assertEqual(data['results'][0]['slug'], 'bone')
        self.assertEqual(len(data['results']), 2)

    # --- recent ------------------------------------------------------------------

    def test_recent_newest_first_without_drafts(self):
        _, data = self._get('/api/recent/')
        self.assertEqual(
            [r['title'] for r in data['results']],
            ['Infant bones and vitamin D', 'Infant bone density – RCT', 'Overview Bone and vitamin D'],
        )

    # --- page text ---------------------------------------------------------------

    def test_page_text_with_absolute_links_and_topics(self):
        page = self._page(
            'Bone study',
            [self.bone],
            2023,
            content_md='See [overview](/pages/overview-bone/) and <a href="/tags/bone/">Bone</a> '
                       'and [paper](https://doi.org/10.1/x) and [cdn](//cdn.example.com/a.png).',
        )
        response, data = self._get(f'/api/pages/{page.slug}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['title'], 'Bone study')
        self.assertEqual(data['url'], f'https://wiki.example.com/pages/{page.slug}/')
        self.assertIn('[overview](https://wiki.example.com/pages/overview-bone/)', data['text'])
        self.assertIn('href="https://wiki.example.com/tags/bone/"', data['text'])
        self.assertIn('(https://doi.org/10.1/x)', data['text'])
        self.assertIn('(//cdn.example.com/a.png)', data['text'])
        self.assertEqual(data['updated'], '2023-01-01')
        self.assertEqual([t['slug'] for t in data['topics']], ['bone'])
        self.assertIsNone(data['next_start'])

    def test_page_text_in_chunks(self):
        page = self._page('Long bone page', [self.bone], 2023, content_md='a' * 2500)
        _, first = self._get(f'/api/pages/{page.slug}/?max_chars=1000')
        self.assertEqual((len(first['text']), first['length'], first['next_start']), (1000, 2500, 1000))
        _, last = self._get(f"/api/pages/{page.slug}/?max_chars=1000&start=2000")
        self.assertEqual((len(last['text']), last['next_start']), (500, None))

    def test_page_draft_or_missing_is_json_404(self):
        for slug in (self.draft.slug, 'no-such-page'):
            response, data = self._get(f'/api/pages/{slug}/')
            self.assertEqual(response.status_code, 404, slug)
            self.assertIn('error', data)

    # --- search ------------------------------------------------------------------

    def test_search_shapes_hits(self):
        hits = {
            'hits': [{'id': 1, 'title': 'Overview Bone', 'slug': 'overview-bone',
                      'search_priority': 7, 'modified_date': 1704067200}],
            'totalHits': 41,
        }
        with patch('wiki_api.queries.search_pages', return_value=hits) as search_pages:
            response, data = self._get('/api/search/?q=bone&limit=5&offset=10')
        search_pages.assert_called_once_with('bone', limit=5, offset=10)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total'], 41)
        self.assertEqual(data['results'], [{
            'title': 'Overview Bone',
            'url': 'https://wiki.example.com/pages/overview-bone/',
            'slug': 'overview-bone',
            'type': 'overview',
            'updated': '2024-01-01',
        }])

    def test_search_requires_query(self):
        response, data = self._get('/api/search/?q=%20')
        self.assertEqual(response.status_code, 400)
        self.assertIn('q', data['error'])

    # --- CORS, methods, caching ------------------------------------------------------

    def test_cors_only_for_the_chat_origin(self):
        response = self.client.get('/api/recent/', HTTP_ORIGIN=CHAT_ORIGIN)
        self.assertEqual(response['Access-Control-Allow-Origin'], CHAT_ORIGIN)
        self.assertEqual(response['Vary'], 'Origin')
        other = self.client.get('/api/recent/', HTTP_ORIGIN='https://evil.example.com')
        self.assertNotIn('Access-Control-Allow-Origin', other)

    def test_cors_also_on_errors(self):
        response = self.client.get('/api/topic-pages/', HTTP_ORIGIN=CHAT_ORIGIN)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Access-Control-Allow-Origin'], CHAT_ORIGIN)

    def test_get_only_and_cached_briefly(self):
        self.assertEqual(self.client.post('/api/recent/').status_code, 405)
        self.assertEqual(self.client.get('/api/recent/')['Cache-Control'], 'public, max-age=60')
