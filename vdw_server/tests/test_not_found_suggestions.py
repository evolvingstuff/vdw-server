from unittest.mock import patch

from django.http import HttpResponsePermanentRedirect
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

from pages.models import Page
from site_pages.models import SitePage
from vdw_server.not_found_suggestions import (
    clear_not_found_suggestions_cache,
    get_not_found_requested_phrase,
    get_not_found_redirect_url,
    get_not_found_suggestions,
    reload_not_found_suggestions,
)
from vdw_server.views import custom_page_not_found


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

    def test_get_not_found_requested_phrase_skips_scoring_work(self):
        request = self.factory.get(
            '/tiki-index.php/styles/vitamindwiki/magiczoom-trial/magiczoom/Stronger+rowers+have+higher+levels+of+Vitamin+D+-+Jan+2024'
        )

        requested_phrase = get_not_found_requested_phrase(request)

        self.assertEqual(
            requested_phrase,
            'tiki index.php styles vitamindwiki magiczoom trial magiczoom Stronger rowers have higher levels of Vitamin D Jan 2024',
        )

    def test_get_not_found_redirect_url_returns_exact_normalized_match(self):
        page = Page.objects.create(
            title='17 Autism risk factors: low Vitamin D, virus, vaccine, mercury etc. - many studies',
            content_md='Body text',
            status='published',
        )
        reload_not_found_suggestions()
        request = self.factory.get('/pages/17+Autism+risk+factors:+low+Vitamin+D,+virus,+vaccine,+mercury+etc.+many+studies/')

        redirect_url = get_not_found_redirect_url(request)

        self.assertEqual(redirect_url, f'/pages/{page.slug}/')

    @override_settings(DEBUG=False, ENABLE_404_SUGGESTIONS=True)
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

    @override_settings(DEBUG=False, ENABLE_404_SUGGESTIONS=True)
    def test_custom_404_redirects_exact_normalized_match(self):
        page = Page.objects.create(
            title='17 Autism risk factors: low Vitamin D, virus, vaccine, mercury etc. - many studies',
            content_md='Body text',
            status='published',
        )
        reload_not_found_suggestions()

        response = self.client.get(
            '/pages/17+Autism+risk+factors:+low+Vitamin+D,+virus,+vaccine,+mercury+etc.+many+studies/'
        )

        self.assertEqual(response.status_code, HttpResponsePermanentRedirect.status_code)
        self.assertEqual(response['Location'], f'/pages/{page.slug}/')

    @override_settings(DEBUG=True, ENABLE_404_SUGGESTIONS=True)
    def test_page_detail_fallback_redirects_exact_normalized_match_in_debug(self):
        page = Page.objects.create(
            title='17 Autism risk factors: low Vitamin D, virus, vaccine, mercury etc. - many studies',
            content_md='Body text',
            status='published',
        )
        reload_not_found_suggestions()

        response = self.client.get(
            '/pages/17+Autism+risk+factors:+low+Vitamin+D,+virus,+vaccine,+mercury+etc.+many+studies/'
        )

        self.assertEqual(response.status_code, HttpResponsePermanentRedirect.status_code)
        self.assertEqual(response['Location'], f'/pages/{page.slug}/')

    @override_settings(DEBUG=False, ENABLE_404_SUGGESTIONS=False)
    def test_custom_404_cheap_mode_skips_suggestion_scoring(self):
        request = self.factory.get('/pages/Vitamin+D+for+Asthma+Supports/')

        with patch('vdw_server.views.get_not_found_suggestions') as suggestions_mock:
            response = custom_page_not_found(request, Http404('missing'))

        suggestions_mock.assert_not_called()
        self.assertEqual(response.status_code, 404)
        response_html = response.content.decode()
        self.assertIn('Cannot find "Vitamin D for Asthma Supports"', response_html)
        self.assertNotIn('Possible matches', response_html)
