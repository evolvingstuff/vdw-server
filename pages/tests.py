from datetime import datetime
from types import SimpleNamespace
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
from pages.admin import BULK_TAG_EXCLUDED_IDS_FIELD, PageAdmin
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
        self.prefix_hit = Page.objects.create(
            title="Thyroid Support",
            content_md="Body text",
            status='published',
        )
        self.mid_word_hit = Page.objects.create(
            title="Hypothyroidism Overview",
            content_md="Body text",
            status='published',
        )
        self.phrase_prefix_hit = Page.objects.create(
            title="Vitamin D Basics",
            content_md="Body text",
            status='published',
        )
        self.phrase_mid_word_hit = Page.objects.create(
            title="Hypovitamin D Basics",
            content_md="Body text",
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

    def test_search_matches_phrase_at_start_of_word(self):
        request = self.factory.get('/admin/posts/page/', {'q': 'thyroid'})
        queryset = Page.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, 'thyroid')

        self.assertIn(self.prefix_hit, results)

    def test_search_ignores_phrase_inside_word(self):
        request = self.factory.get('/admin/posts/page/', {'q': 'thyroid'})
        queryset = Page.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, 'thyroid')

        self.assertNotIn(self.mid_word_hit, results)

    def test_search_matches_multi_word_phrase_at_word_start(self):
        request = self.factory.get('/admin/posts/page/', {'q': 'vitamin d'})
        queryset = Page.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, 'vitamin d')

        self.assertIn(self.phrase_prefix_hit, results)
        self.assertNotIn(self.phrase_mid_word_hit, results)

    def test_search_matches_page_url_last_segment(self):
        page = Page.objects.create(
            title="17 Autism risk factors: low Vitamin D, virus, vaccine, mercury etc. - many studies",
            content_md="Body text",
            status='published',
        )
        search_term = "https://www.vitamindwiki.com/pages/17-autism-risk-factors-low-vitamin-d-virus-vaccine-mercury-etc-many-studies/"
        request = self.factory.get('/admin/posts/page/', {'q': search_term})
        queryset = Page.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, search_term)

        self.assertIn(page, results)

    def test_search_matches_page_url_slug_when_slug_differs_from_title(self):
        page = Page.objects.create(
            title="Some hospitals record which supplements are taken, but rarely dosage, frequency, or form",
            slug="some-hospitals-record-which-supplements-are-taken-but-not-dosage-frequency-or-form",
            content_md="Body text",
            status='published',
        )
        search_term = "https://www.vitamindwiki.com/pages/some-hospitals-record-which-supplements-are-taken-but-not-dosage-frequency-or-form/"
        request = self.factory.get('/admin/posts/page/', {'q': search_term})
        queryset = Page.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, search_term)

        self.assertIn(page, results)


class PageAdminQueryOptimizationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = PageAdmin(Page, self.site)

    def test_changelist_queryset_defers_large_text_fields(self):
        request = self.factory.get('/admin/posts/page/')
        request.resolver_match = SimpleNamespace(url_name='pages_page_changelist')

        queryset = self.admin.get_queryset(request)
        sql = str(queryset.query)

        self.assertNotIn('"posts_post"."content_md"', sql)
        self.assertNotIn('"posts_post"."content_html"', sql)
        self.assertNotIn('"posts_post"."content_text"', sql)
        self.assertNotIn('"posts_post"."original_tiki"', sql)
        self.assertNotIn('LEFT OUTER JOIN "posts_post_tags"', sql)
        self.assertEqual(queryset._prefetch_related_lookups, ())

    def test_change_queryset_keeps_large_text_fields_available(self):
        request = self.factory.get('/admin/posts/page/1/change/')
        request.resolver_match = SimpleNamespace(url_name='pages_page_change')

        queryset = self.admin.get_queryset(request)
        sql = str(queryset.query)

        self.assertIn('"posts_post"."content_md"', sql)
        self.assertIn('"posts_post"."content_html"', sql)
        self.assertIn('"posts_post"."content_text"', sql)
        self.assertIn('"posts_post"."original_tiki"', sql)


class PageAdminListFilterTests(TestCase):
    def setUp(self):
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

    def test_changelist_exposes_tag_filter(self):
        tag = Tag.objects.create(name="Vitamin D", slug="vitamin-d")
        page = Page.objects.create(title="Tagged page", content_md="Body", status="draft")
        page.tags.add(tag)

        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:posts_page_changelist"))

        self.assertEqual(response.status_code, 200)
        filter_titles = [filter_spec.title for filter_spec in response.context["cl"].filter_specs]
        self.assertIn("tags", filter_titles)


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

    def _bulk_create_pages(self, count: int):
        Page.objects.bulk_create(
            [
                Page(
                    title=f"P{index}",
                    slug=f"p{index}",
                    content_md="Body",
                    content_html="<p>Body</p>",
                    content_text="Body",
                    character_count=4,
                    status="draft",
                )
                for index in range(count)
            ]
        )

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

    def test_add_tags_action_select_across_omits_hidden_ids_and_limits_preview(self):
        total_pages = 1100
        self._bulk_create_pages(total_pages)

        request = self.factory.post(
            "/admin/posts/page/",
            {
                "action": "add_tags_to_selected",
                "select_across": "1",
            },
        )
        self._attach_messages(request)

        response = self.admin.add_tags_to_selected(request, Page.objects.order_by("pk"))
        response.render()

        self.assertEqual(response.context_data["selected_page_count"], total_pages)
        self.assertEqual(len(response.context_data["selected_page_ids"]), 1)
        self.assertEqual(
            len(response.context_data["selected_pages_preview"]),
            self.admin.BULK_TAG_PREVIEW_LIMIT,
        )
        self.assertIn(
            "All pages matching the current changelist filters will be updated.",
            response.content.decode(),
        )
        self.assertEqual(
            response.content.decode().count(f'name="{helpers.ACTION_CHECKBOX_NAME}"'),
            1,
        )

    def test_add_tags_action_select_across_excludes_unchecked_pages_from_confirmation(self):
        self._bulk_create_pages(5)
        excluded_pages = list(Page.objects.order_by("pk")[:2])

        request = self.factory.post(
            "/admin/posts/page/",
            {
                "action": "add_tags_to_selected",
                "select_across": "1",
                BULK_TAG_EXCLUDED_IDS_FIELD: ",".join(str(page.pk) for page in excluded_pages),
            },
        )
        self._attach_messages(request)

        response = self.admin.add_tags_to_selected(request, Page.objects.order_by("pk"))
        response.render()

        self.assertEqual(response.context_data["selected_page_count"], 3)
        self.assertEqual(response.context_data["excluded_page_ids"], [page.pk for page in excluded_pages])
        self.assertEqual(response.context_data["excluded_page_count"], 2)
        self.assertEqual(response.context_data["selected_pages_preview"], ["P2", "P3", "P4"])
        self.assertIn("2 unchecked page(s) will be skipped.", response.content.decode())

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

    def test_add_tags_action_select_across_applies_tags_to_entire_queryset(self):
        existing = Tag.objects.create(name="Existing", slug="existing")
        self._bulk_create_pages(60)

        request = self.factory.post(
            "/admin/posts/page/",
            {
                "apply": "1",
                "select_across": "1",
                "tags": [str(existing.pk)],
            },
        )
        self._attach_messages(request)

        response = self.admin.add_tags_to_selected(request, Page.objects.all())

        self.assertIsNone(response)
        self.assertEqual(Page.objects.filter(tags=existing).count(), 60)
        self.assertEqual(Page.objects.filter(derived_tags=existing).count(), 60)

    def test_add_tags_action_select_across_skips_excluded_pages_on_apply(self):
        existing = Tag.objects.create(name="Existing", slug="existing")
        self._bulk_create_pages(6)
        excluded_pages = list(Page.objects.order_by("pk")[:2])

        request = self.factory.post(
            "/admin/posts/page/",
            {
                "apply": "1",
                "select_across": "1",
                "tags": [str(existing.pk)],
                BULK_TAG_EXCLUDED_IDS_FIELD: ",".join(str(page.pk) for page in excluded_pages),
            },
        )
        self._attach_messages(request)

        response = self.admin.add_tags_to_selected(request, Page.objects.all())

        self.assertIsNone(response)
        self.assertEqual(Page.objects.filter(tags=existing).count(), 4)
        self.assertFalse(Page.objects.filter(pk=excluded_pages[0].pk, tags=existing).exists())
        self.assertFalse(Page.objects.filter(pk=excluded_pages[1].pk, tags=existing).exists())

    def test_add_tags_action_select_across_full_admin_flow_creates_new_tag(self):
        self._bulk_create_pages(60)
        self.client.force_login(self.user)

        confirmation_response = self.client.post(
            "/admin/posts/page/",
            {
                "action": "add_tags_to_selected",
                "select_across": "1",
                "index": "0",
                helpers.ACTION_CHECKBOX_NAME: [str(Page.objects.order_by("pk").first().pk)],
            },
        )

        self.assertEqual(confirmation_response.status_code, 200)
        confirmation_selected_ids = confirmation_response.context["selected_page_ids"]
        self.assertEqual(len(confirmation_selected_ids), 1)

        apply_response = self.client.post(
            "/admin/posts/page/",
            {
                "apply": "1",
                "action": "add_tags_to_selected",
                "select_across": "1",
                "new_tags": "Foobar",
                helpers.ACTION_CHECKBOX_NAME: [str(confirmation_selected_ids[0])],
            },
        )

        self.assertEqual(apply_response.status_code, 302)
        self.assertTrue(Tag.objects.filter(name="Foobar").exists())
        created_tag = Tag.objects.get(name="Foobar")
        self.assertEqual(Page.objects.filter(tags=created_tag).count(), 60)
        self.assertEqual(Page.objects.filter(derived_tags=created_tag).count(), 60)

    def test_add_tags_action_select_across_full_admin_flow_skips_excluded_pages(self):
        self._bulk_create_pages(6)
        excluded_pages = list(Page.objects.order_by("pk")[:2])
        self.client.force_login(self.user)

        confirmation_response = self.client.post(
            "/admin/posts/page/",
            {
                "action": "add_tags_to_selected",
                "select_across": "1",
                "index": "0",
                BULK_TAG_EXCLUDED_IDS_FIELD: ",".join(str(page.pk) for page in excluded_pages),
                helpers.ACTION_CHECKBOX_NAME: [str(Page.objects.order_by("pk").first().pk)],
            },
        )

        self.assertEqual(confirmation_response.status_code, 200)
        self.assertEqual(confirmation_response.context["selected_page_count"], 4)

        confirmation_selected_ids = confirmation_response.context["selected_page_ids"]
        apply_response = self.client.post(
            "/admin/posts/page/",
            {
                "apply": "1",
                "action": "add_tags_to_selected",
                "select_across": "1",
                "new_tags": "Foobar",
                BULK_TAG_EXCLUDED_IDS_FIELD: ",".join(str(page.pk) for page in excluded_pages),
                helpers.ACTION_CHECKBOX_NAME: [str(confirmation_selected_ids[0])],
            },
        )

        self.assertEqual(apply_response.status_code, 302)
        created_tag = Tag.objects.get(name="Foobar")
        self.assertEqual(Page.objects.filter(tags=created_tag).count(), 4)
        self.assertFalse(Page.objects.filter(pk=excluded_pages[0].pk, tags=created_tag).exists())
        self.assertFalse(Page.objects.filter(pk=excluded_pages[1].pk, tags=created_tag).exists())


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


class PageDetailPrintTemplateTests(TestCase):
    def setUp(self):
        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()

    def tearDown(self):
        self.index_patch.stop()
        self.remove_patch.stop()

    def test_page_detail_renders_print_metadata_and_css(self):
        page = Page.objects.create(
            title="Print Friendly Page",
            content_md="Body copy for printing",
            status="published",
        )

        response = self.client.get(reverse('page_detail', args=[page.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="print-page-metadata"')
        self.assertContains(response, f'URL: http://testserver/pages/{page.slug}/')
        self.assertContains(response, 'data-print-generated-at')
        self.assertContains(response, '@page')


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
