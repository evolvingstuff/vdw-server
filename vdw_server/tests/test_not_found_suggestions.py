from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings

from pages.models import Page
from site_pages.models import SitePage
from vdw_server.not_found_suggestions import (
    clear_not_found_suggestions_cache,
    get_not_found_suggestions,
    reload_not_found_suggestions,
)


class NotFoundSuggestionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        clear_not_found_suggestions_cache()
        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()

    def tearDown(self):
        clear_not_found_suggestions_cache()
        self.index_patch.stop()
        self.remove_patch.stop()

    def test_lookup_uses_warm_cache_without_queries(self):
        page = Page.objects.create(
            title='Top 10 chronic health problems of children, women, pregnancies, seniors, and darker skins are fought by Vitamin D - VitaminDWiki',
            content_md='Body text',
            status='published',
        )
        reload_not_found_suggestions()
        request = self.factory.get(
            '/The+top+10+chronic+health+problems+of+children%2C+women%2C+pregnancies%2C+seniors%2C+and+darker+skins/'
        )

        with self.assertNumQueries(0):
            requested_phrase, suggestions = get_not_found_suggestions(request)

        self.assertEqual(
            requested_phrase,
            'The top 10 chronic health problems of children, women, pregnancies, seniors, and darker skins',
        )
        self.assertEqual(suggestions[0].title, page.title)
        self.assertEqual(suggestions[0].url, f'/pages/{page.slug}/')

    def test_page_signal_updates_loaded_cache_without_reload(self):
        reload_not_found_suggestions()
        page = Page.objects.create(
            title='Magnesium and Vitamin D Guide',
            content_md='Body text',
            status='published',
        )
        request = self.factory.get('/pages/magnesium-vitamin-d-guides/')

        with self.assertNumQueries(0):
            _, suggestions = get_not_found_suggestions(request)

        self.assertEqual(suggestions[0].title, page.title)

    def test_site_page_signal_updates_loaded_cache_without_reload(self):
        reload_not_found_suggestions()
        site_page = SitePage.objects.create(
            title='About Vitamin D Research',
            slug='about-vitamin-d-research',
            page_type='custom',
            is_published=True,
            content_md='Body text',
        )
        request = self.factory.get('/about-vitamin-d-research-library/')

        with self.assertNumQueries(0):
            _, suggestions = get_not_found_suggestions(request)

        self.assertEqual(suggestions[0].title, site_page.title)
        self.assertEqual(suggestions[0].url, '/about-vitamin-d-research/')

    @override_settings(DEBUG=False)
    def test_custom_404_renders_requested_phrase_and_matches(self):
        page = Page.objects.create(
            title='Vitamin D for Asthma Support',
            content_md='Body text',
            status='published',
        )
        reload_not_found_suggestions()

        response = self.client.get('/pages/Vitamin+D+for+Asthma+Supports/')

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Cannot find', status_code=404)
        self.assertContains(response, 'Vitamin D for Asthma Supports', status_code=404)
        self.assertContains(response, page.title, status_code=404)
        self.assertContains(response, f'/pages/{page.slug}/', status_code=404)
