from unittest.mock import patch

from django.test import RequestFactory, TestCase, override_settings

from pages.models import Page
from vdw_server.not_found_suggestions import _is_very_close, resolve_not_found

LONG_TITLE = 'Top 10 chronic health problems of children, women, pregnancies, seniors, and darker skins are fought by Vitamin D'


def _hits(*titles_and_slugs):
    return {'hits': [{'id': i, 'title': t, 'slug': s} for i, (t, s) in enumerate(titles_and_slugs)], 'totalHits': 0}


class VeryCloseTests(TestCase):
    def test_rules(self):
        self.assertTrue(_is_very_close('Vitamin D and bones', 'Vitamin D and bones – June 2011', None))  # date only
        self.assertTrue(_is_very_close(  # leading "The" + title a few words longer
            'The top 10 chronic health problems of children, women, pregnancies, seniors, and darker skins',
            LONG_TITLE, None))
        self.assertTrue(_is_very_close('vitamin d is only of only 2 ways to prevent nafld',
                                       'Vitamin D is one of only 2 ways to prevent NAFLD', None))  # typo
        self.assertFalse(_is_very_close('Breast cancer stuff', 'Breast Cancer survival 2X more likely', None))
        self.assertFalse(_is_very_close('magnesium', 'Magnesium and vitamin D', None))  # prefix too short
        # Very similar to two pages: not clearly the intended one
        self.assertFalse(_is_very_close('vitamin d and bone health in women',
                                        'Vitamin D and bone health in men', 'Vitamin D and bone health in men 2'))


@override_settings(ALLOWED_HOSTS=['*'], DEBUG=False, ENABLE_404_SUGGESTIONS=True)
class NotFoundViewTests(TestCase):
    def setUp(self):
        for target in ('pages.signals.index_page', 'pages.signals.remove_page_from_search'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        Page.objects.create(title='Overview Asthma and Vitamin D', status='published', content_md='x')
        self.rf = RequestFactory()

    def test_exact_old_address_redirects_permanently(self):
        response = self.client.get('/tiki-index.php?page=Overview+Asthma+and+Vitamin+D')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/pages/overview-asthma-and-vitamin-d/')

    @patch('search.search.search_pages')
    def test_very_close_title_redirects_temporarily(self, search):
        search.return_value = _hits((LONG_TITLE, 'top-10-chronic'), ('All of the top 10 chronic health problems of blacks', 'blacks'))
        response = self.client.get(
            '/The+top+10+chronic+health+problems+of+children%2C+women%2C+pregnancies%2C+seniors%2C+and+darker+skins')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/pages/top-10-chronic/')
        self.assertTrue(search.call_args.args[0].startswith('top 10 chronic'))  # leading "The" dropped
        self.assertEqual(search.call_args.kwargs['matching_strategy'], 'last')

    @patch('search.search.search_pages')
    def test_not_close_shows_suggestions(self, search):
        search.return_value = _hits(('Breast Cancer survival 2X more likely if vitamin D', 'bc'), ('Breast cancer and D', 'bc2'))
        response = self.client.get('/Breast+cancer+stuff')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Possible matches', status_code=404)
        self.assertContains(response, 'href="/pages/bc/"', status_code=404)

    @patch('search.search.search_pages')
    def test_junk_probe_skips_search(self, search):
        response = self.client.get('/wp-login.php')
        self.assertEqual(response.status_code, 404)
        search.assert_not_called()

    @patch('search.search.search_pages', side_effect=RuntimeError('meilisearch down'))
    def test_search_failure_shows_plain_404(self, search):
        response = self.client.get('/Some+missing+page+title')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'Some missing page title', status_code=404)

    @override_settings(ENABLE_404_SUGGESTIONS=False)
    @patch('search.search.search_pages')
    def test_disabled_mode_does_no_lookups(self, search):
        response = self.client.get('/Some+missing+page+title')
        self.assertEqual(response.status_code, 404)
        search.assert_not_called()
