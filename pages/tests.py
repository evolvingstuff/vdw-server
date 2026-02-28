from datetime import datetime
from unittest.mock import patch

from django.contrib.admin import helpers
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.urls import reverse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone
from django.utils.text import slugify

from _retired.conversion_md_to_db import get_created_and_modified_dates, process_tags
from pages.alias_cache import lookup_path, lookup_plain, reload_alias_redirects
from pages.admin import PageAdmin
from pages.models import Page
from pages.recent_cache import clear_recent_pages_cache, get_recent_pages, reload_recent_pages
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


class DerivedTagsFromTitleTests(TestCase):
    def test_title_implies_existing_tags(self):
        Tag.objects.create(name="Alcohol", slug="alcohol")
        Tag.objects.create(name="Vitamin D", slug="vitamin-d")

        page = Page.objects.create(
            title="Alcohol and Vitamin D",
            content_md="Body",
            status="draft",
        )

        derived_slugs = set(page.derived_tags.values_list("slug", flat=True))
        self.assertIn("alcohol", derived_slugs)
        self.assertIn("vitamin-d", derived_slugs)


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


class PageAdminBulkTagActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = PageAdmin(Page, self.site)

        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )

        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()

    def tearDown(self):
        self.index_patch.stop()
        self.remove_patch.stop()

    def _attach_messages(self, request):
        request.user = self.user
        request.session = self.client.session
        request._messages = FallbackStorage(request)

    def test_add_tags_action_renders_confirmation_form(self):
        page = Page.objects.create(title="P1", content_md="Body", status="draft")
        request = self.factory.post(
            "/admin/posts/page/",
            {
                "action": "add_tags_to_selected",
                helpers.ACTION_CHECKBOX_NAME: [str(page.pk)],
            },
        )
        self._attach_messages(request)

        response = self.admin.add_tags_to_selected(request, Page.objects.filter(pk=page.pk))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template_name, "admin/posts/page/add_tags.html")

    def test_add_tags_action_adds_existing_and_new_tags(self):
        existing = Tag.objects.create(name="Existing", slug="existing")
        Tag.objects.create(name="Slug Taken", slug="beta")

        page_1 = Page.objects.create(title="P1", content_md="Body", status="draft")
        page_2 = Page.objects.create(title="P2", content_md="Body", status="draft")
        queryset = Page.objects.filter(pk__in=[page_1.pk, page_2.pk])

        request = self.factory.post(
            "/admin/posts/page/",
            {
                "apply": "1",
                "tags": [str(existing.pk)],
                "new_tags": "Beta, Gamma",
            },
        )
        self._attach_messages(request)

        response = self.admin.add_tags_to_selected(request, queryset)

        self.assertIsNone(response)
        self.assertTrue(Tag.objects.filter(name="Beta").exists())
        self.assertEqual(Tag.objects.get(name="Beta").slug, "beta-2")

        for page in [page_1, page_2]:
            self.assertEqual(page.tags.count(), 3)
            self.assertEqual(page.derived_tags.count(), 3)
            self.assertTrue(page.tags.filter(name="Existing").exists())
            self.assertTrue(page.tags.filter(name="Beta").exists())
            self.assertTrue(page.tags.filter(name="Gamma").exists())


class MostRecentPageListTests(TestCase):
    def setUp(self):
        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()
        clear_recent_pages_cache()
        reload_recent_pages()

    def tearDown(self):
        clear_recent_pages_cache()
        self.index_patch.stop()
        self.remove_patch.stop()

    def test_recent_page_list_limits_to_150_published_pages(self):
        for index in range(170):
            Page.objects.create(
                title=f"Published {index}",
                content_md="Body",
                status="published",
            )

        Page.objects.create(title="Draft page", content_md="Body", status="draft")

        response = self.client.get(reverse('recent_page_list'))

        self.assertEqual(response.status_code, 200)
        pages = response.context['pages']
        self.assertEqual(len(pages), 150)
        self.assertTrue(all(page.status == 'published' for page in pages))

    def test_recent_page_list_orders_by_most_recent_update(self):
        older_page = Page.objects.create(
            title="Older update",
            content_md="Body",
            status="published",
        )
        newer_page = Page.objects.create(
            title="Newer update",
            content_md="Body",
            status="published",
        )

        older_ts = timezone.make_aware(datetime(2024, 1, 1))
        newer_ts = timezone.make_aware(datetime(2025, 1, 1))
        Page.objects.filter(pk=older_page.pk).update(modified_date=older_ts)
        Page.objects.filter(pk=newer_page.pk).update(modified_date=newer_ts)

        response = self.client.get(reverse('recent_page_list'))

        pages = list(response.context['pages'])
        self.assertEqual(pages[0].pk, newer_page.pk)
        self.assertEqual(pages[1].pk, older_page.pk)

    def test_recent_page_list_renders_month_year_date_format(self):
        page = Page.objects.create(
            title="Date format page",
            content_md="Body",
            status="published",
        )
        Page.objects.filter(pk=page.pk).update(
            modified_date=timezone.make_aware(datetime(2025, 2, 3))
        )
        reload_recent_pages()

        response = self.client.get(reverse('recent_page_list'))

        self.assertContains(response, "Date format page")
        self.assertContains(response, "02/2025")

    def test_recent_page_list_includes_most_recent_toolbar_link(self):
        response = self.client.get(reverse('recent_page_list'))

        self.assertContains(response, "Most Recent")
        self.assertContains(response, 'href="/pages/recent/"')


class RecentPageCacheTests(TestCase):
    def setUp(self):
        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()
        clear_recent_pages_cache()
        reload_recent_pages()

    def tearDown(self):
        clear_recent_pages_cache()
        self.index_patch.stop()
        self.remove_patch.stop()

    def test_cache_serves_without_queries_after_reload(self):
        Page.objects.create(title="Cached", content_md="Body", status="published")
        reload_recent_pages()

        with self.assertNumQueries(0):
            entries = get_recent_pages()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Cached")

    def test_cache_updates_on_save_and_delete(self):
        page = Page.objects.create(title="Lifecycle", content_md="Body", status="published")

        self.assertTrue(any(entry.pk == page.pk for entry in get_recent_pages()))

        page.status = "draft"
        page.save()
        self.assertFalse(any(entry.pk == page.pk for entry in get_recent_pages()))

        page.status = "published"
        page.save()
        self.assertTrue(any(entry.pk == page.pk for entry in get_recent_pages()))

        page_id = page.pk
        page.delete()
        self.assertFalse(any(entry.pk == page_id for entry in get_recent_pages()))
