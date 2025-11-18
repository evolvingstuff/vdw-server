from datetime import datetime
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone
from django.utils.text import slugify

from _retired.conversion_md_to_db import get_created_and_modified_dates, process_tags
from pages.alias_cache import lookup_path, lookup_plain, reload_alias_redirects
from pages.admin import PageAdmin
from pages.models import Page
from tags.models import Tag
from vdw_server.middleware import LegacyAliasRedirectMiddleware


class PageAdminSearchTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = PageAdmin(Page, self.site)

        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()

        self.title_hit = Page.objects.create(
            title="Needle in Title",
            content_md="Body text",
            status='published',
        )
        self.content_hit = Page.objects.create(
            title="Completely Different",
            content_md="Contains the needle keyword",
            status='published',
        )

    def tearDown(self):
        self.index_patch.stop()
        self.remove_patch.stop()

    def test_search_matches_title(self):
        request = self.factory.get('/admin/posts/page/', {'q': 'needle'})
        queryset = Page.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, 'needle')

        self.assertIn(self.title_hit, results)

    def test_search_ignores_content_only_matches(self):
        request = self.factory.get('/admin/posts/page/', {'q': 'needle'})
        queryset = Page.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, 'needle')

        self.assertNotIn(self.content_hit, results)


class ConversionDateParsingTests(SimpleTestCase):
    def test_lastmod_used_when_present(self):
        frontmatter = {
            'date': '2024-01-01',
            'lastmod': '2024-02-03',
        }

        created, modified = get_created_and_modified_dates(frontmatter, timezone)

        expected_created = timezone.make_aware(datetime(2024, 1, 1))
        expected_modified = timezone.make_aware(datetime(2024, 2, 3))

        self.assertEqual(created, expected_created)
        self.assertEqual(modified, expected_modified)

    def test_missing_lastmod_defaults_to_created(self):
        frontmatter = {
            'date': '2024-01-01',
        }

        created, modified = get_created_and_modified_dates(frontmatter, timezone)

        expected_created = timezone.make_aware(datetime(2024, 1, 1))

        self.assertEqual(created, expected_created)
        self.assertEqual(modified, expected_created)

    def test_iso_datetime_with_z_suffix(self):
        frontmatter = {
            'date': '2024-01-01T00:00:00Z',
            'lastmod': '2024-01-01T05:30:00Z',
        }

        created, modified = get_created_and_modified_dates(frontmatter, timezone)

        expected_created = timezone.make_aware(datetime(2024, 1, 1))
        expected_modified = timezone.make_aware(datetime(2024, 1, 1, 5, 30))

        self.assertEqual(created, expected_created)
        self.assertEqual(modified, expected_modified)


class ConversionTagFilteringTests(TestCase):
    def test_disallowed_tags_are_removed(self):
        used_slugs = set()
        raw_tags = [
            'Normal Tag',
            ' AI ',
            'Another Tag',
            'Top news',
            'Video Page Names',
            'Z',
            'Z-section',
            'Old Name',
        ]

        tags = process_tags(raw_tags, Tag, slugify, used_slugs)

        names = {tag.name for tag in tags}

        self.assertEqual(names, {'Normal Tag', 'Another Tag'})
        self.assertIn(slugify('Normal Tag'), used_slugs)
        self.assertNotIn('ai', used_slugs)

        disallowed_names = {'AI', 'Top news', 'Video Page Names', 'Z', 'Z-section', 'Old Name'}
        self.assertEqual(Tag.objects.filter(name__in=disallowed_names).count(), 0)


class AliasCacheTests(TestCase):
    def setUp(self):
        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()
        reload_alias_redirects()

    def tearDown(self):
        self.index_patch.stop()
        self.remove_patch.stop()
        reload_alias_redirects()

    def test_alias_variants_and_ids_are_loaded(self):
        page = Page.objects.create(
            title='Vitamin D and Magnesium',
            slug='vitamin-d-and-magnesium',
            content_md='Body text',
            status='published',
            aliases='/Vitamin+D+and+Magnesium\n/123456',
            original_page_id=16217,
        )

        reload_alias_redirects()

        self.assertEqual(lookup_path('/Vitamin+D+and+Magnesium'), page.slug)
        self.assertEqual(lookup_path('Vitamin+D+and+Magnesium'), page.slug)
        self.assertEqual(lookup_path('/123456'), page.slug)
        self.assertEqual(lookup_plain('Vitamin+D+and+Magnesium'), page.slug)
        self.assertEqual(lookup_plain('123456'), page.slug)
        self.assertEqual(lookup_plain('16217'), page.slug)

    def test_original_page_id_adds_alias_even_without_entry(self):
        page = Page.objects.create(
            title='Standalone',
            slug='standalone',
            content_md='Body text',
            status='published',
            aliases='',
            original_page_id=99999,
        )

        reload_alias_redirects()

        self.assertEqual(lookup_plain('99999'), page.slug)
        self.assertEqual(lookup_path('/99999'), page.slug)

    def test_tiki_alias_retains_query_string(self):
        page = Page.objects.create(
            title='Vitamin D and Magnesium',
            slug='vitamin-d-and-magnesium',
            content_md='Body text',
            status='published',
            aliases='/tiki-index.php?page=Vitamin+D+and+Magnesium',
        )

        reload_alias_redirects()

        self.assertIsNone(lookup_path('/tiki-index.php'))
        self.assertEqual(
            lookup_path('/tiki-index.php?page=Vitamin+D+and+Magnesium'),
            page.slug,
        )


class LegacyAliasRedirectMiddlewareTests(TestCase):
    def setUp(self):
        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()

        self.factory = RequestFactory()
        self.middleware = LegacyAliasRedirectMiddleware(lambda request: HttpResponse('ok'))

        self.page = Page.objects.create(
            title='Vitamin D and Magnesium',
            slug='vitamin-d-and-magnesium',
            content_md='Body text',
            status='published',
            aliases='/Vitamin+D+and+Magnesium\n/123456',
            original_page_id=16217,
        )

        reload_alias_redirects()

    def tearDown(self):
        self.index_patch.stop()
        self.remove_patch.stop()
        reload_alias_redirects()

    def test_plain_alias_redirects_to_page(self):
        request = self.factory.get('/Vitamin+D+and+Magnesium')

        response = self.middleware(request)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/pages/vitamin-d-and-magnesium/')

    def test_numeric_alias_redirects(self):
        request = self.factory.get('/123456')

        response = self.middleware(request)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/pages/vitamin-d-and-magnesium/')

    def test_tiki_page_param_redirects(self):
        request = self.factory.get('/tiki-index.php?page=Vitamin+D+and+Magnesium')

        response = self.middleware(request)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/pages/vitamin-d-and-magnesium/')

    def test_tiki_page_id_param_redirects(self):
        request = self.factory.get('/tiki-index.php?page_id=16217')

        response = self.middleware(request)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/pages/vitamin-d-and-magnesium/')

    def test_non_alias_path_passthrough(self):
        request = self.factory.get('/no-match/')

        response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')

