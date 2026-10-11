from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from pages.models import Page
from tags.models import Tag
from tags.services import get_topic_index, reset_topic_index


@override_settings(ALLOWED_HOSTS=['*'], TOPIC_INDEX_BACKGROUND_REFRESH=False)
class TopicBrowserTests(TestCase):
    def setUp(self):
        reset_topic_index()
        self.addCleanup(reset_topic_index)
        for target in ('pages.signals.index_page', 'pages.signals.remove_page_from_search'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.bone = Tag.objects.create(name='Bone', slug='bone')
        self.infant = Tag.objects.create(name='Infant-Child', slug='infant-child')
        self.rct = Tag.objects.create(name='RCT', slug='rct')

        self.old_rct = self._page('Infant bone density – RCT', [self.bone, self.infant], 2020)
        self.new_plain = self._page('Infant bones and vitamin D', [self.bone, self.infant], 2024)
        self.overview = self._page('Overview Bone and vitamin D', [self.bone], 2010)
        # Not assigned the tag, but "Bone" is in the title, so it counts (derived tag).
        self.title_only = self._page('Bone fractures in seniors', [], 2022)
        self.draft = self._page('Draft about bone', [self.bone, self.infant], 2025, status='draft')

    def _page(self, title, tags, year, status='published'):
        page = Page.objects.create(title=title, status=status, content_md='Text.')
        page.tags.set(tags)
        Page.objects.filter(pk=page.pk).update(
            public_modified_date=timezone.make_aware(datetime(year, 1, 1), datetime_timezone.utc)
        )
        return page

    def test_counts_include_title_matches_and_skip_drafts(self):
        counts = {t.slug: t.count for t in get_topic_index().topic_counts}
        self.assertEqual(counts['bone'], 4)
        self.assertEqual(counts['infant-child'], 2)

    def test_combined_counts(self):
        index = get_topic_index()
        total, counts = index.combined_counts(index.tags_for_slugs(['bone', 'infant-child']))
        self.assertEqual(total, 2)
        # 'RCT' is in one title, so that page also counts under RCT.
        self.assertEqual(counts, {'bone': 2, 'infant-child': 2, 'rct': 1})

    def test_search_order_overview_then_rct_then_newest(self):
        index = get_topic_index()
        pages, total = index.pages_for(index.tags_for_slugs(['bone']))
        self.assertEqual(total, 4)
        self.assertEqual(
            [p.title for p in pages],
            [self.overview.title, self.old_rct.title, self.new_plain.title, self.title_only.title],
        )

    def test_single_topic_page_is_indexable_list(self):
        response = self.client.get('/tags/bone/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<link rel="canonical"')
        self.assertNotContains(response, 'noindex')
        self.assertContains(response, self.title_only.title)
        self.assertNotContains(response, self.draft.title)

    def test_combination_page_is_noindex(self):
        response = self.client.get('/tags/bone/?and=infant-child')
        self.assertContains(response, '<meta name="robots" content="noindex, follow">')
        self.assertNotContains(response, '<link rel="canonical"')
        self.assertContains(response, 'Bone + Infant-Child')
        self.assertContains(response, self.old_rct.title)
        self.assertNotContains(response, self.overview.title)

    def test_old_paginated_links_redirect_to_single_list(self):
        response = self.client.get('/tags/bone/?page=3&and=infant-child')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/tags/bone/?and=infant-child')

    def test_unknown_topic_404s(self):
        self.assertEqual(self.client.get('/tags/nope/').status_code, 404)
        self.assertEqual(self.client.get('/tags/bone/?and=nope').status_code, 404)

    def test_counts_api(self):
        response = self.client.get('/tags/api/counts/?tags=bone,infant-child')
        self.assertEqual(response.json(), {'total': 2, 'counts': {'bone': 2, 'infant-child': 2, 'rct': 1}})
        self.assertEqual(self.client.get('/tags/api/counts/').status_code, 400)

    def test_browser_page_embeds_topics(self):
        response = self.client.get('/tags/')
        self.assertContains(response, '<h1>Topics/Subjects</h1>')
        self.assertContains(response, 'id="topics-data"')
        self.assertContains(response, '"slug": "bone"')

    def test_index_rebuilds_after_a_page_is_saved(self):
        index = get_topic_index()
        page = self._page('Bone news', [self.bone], 2026)
        with patch('tags.services.SIGNATURE_CHECK_SECONDS', 0):
            new_index = get_topic_index()
        self.assertIsNot(new_index, index)
        self.assertIn(page.id, new_index.pages)

    def test_article_footer_shows_tags_with_counts_once_index_is_ready(self):
        url = f'/pages/{self.old_rct.slug}/'
        before = self.client.get(url)  # index not built yet: no waiting, no counts
        self.assertContains(before, 'Click on a tag to see studies:')
        self.assertContains(before, '>Bone</a>')

        get_topic_index()
        after = self.client.get(url)
        self.assertContains(after, '>Bone (4)</a>')
        self.assertContains(after, '>Infant-Child (2)</a>')
        self.assertNotContains(after, 'Tags:</strong>')
