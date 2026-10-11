from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from pages.models import Page
from tags.models import Tag
from tags.services import pages_in_search_order, published_pages_with_all_tags, tag_counts


@override_settings(ALLOWED_HOSTS=['*'])
class TopicBrowserTests(TestCase):
    def setUp(self):
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
        counts = {t.slug: t.count for t in tag_counts()}
        self.assertEqual(counts['bone'], 4)
        self.assertEqual(counts['infant-child'], 2)

    def test_combined_counts(self):
        counts = {t.slug: t.count for t in tag_counts([self.bone, self.infant])}
        # 'RCT' is in one title, so that page also counts under RCT.
        self.assertEqual(counts, {'bone': 2, 'infant-child': 2, 'rct': 1})

    def test_search_order_overview_then_rct_then_newest(self):
        rows = pages_in_search_order(published_pages_with_all_tags([self.bone]))
        self.assertEqual(
            [r['title'] for r in rows],
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
