from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from search.query import extract_search_term, split_or_query


class SearchTermExtractionTests(SimpleTestCase):
    def test_link_text_variants(self):
        cases = {
            'Search VitaminDWiki for ICU OR “critical care” OR “intensive care”':
                'ICU OR "critical care" OR "intensive care"',
            'Search for Fatty Liver in VitaminDWiki': 'Fatty Liver',
            'Search PTH in VitaminDWiki': 'PTH',
            'Search VitraminDWiki for COIMBRA': 'COIMBRA',
            'Seach VitaminDWiki for ("mental health" OR "mental illness")': '"mental health" OR "mental illness"',
            'All items with the word Heaney in VitaminDWiki': 'Heaney',
            'All items for Garland  and "Breast Cancer" in VitaminDWiki': 'Garland "Breast Cancer"',
            '((Stunting OR “low birth weight” OR LBW)': 'Stunting OR "low birth weight" OR LBW',
            'Telomere': 'Telomere',
            'Searching for answers': 'Searching for answers',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_search_term(text), expected)

    def test_split_or_query(self):
        self.assertEqual(split_or_query('ICU OR "critical care" OR icu'), ['ICU', '"critical care"'])
        self.assertEqual(split_or_query('vitamin D or calcium'), ['vitamin D or calcium'])  # lower-case "or" is a word


def _fake_search(query, limit=20, offset=0):
    data = {
        'ICU': [{'id': 1, 'title': 'ICU overview', 'slug': 'icu-overview'}, {'id': 2, 'title': 'ICU and D', 'slug': 'icu-d'}],
        '"critical care"': [{'id': 3, 'title': 'Critical care study', 'slug': 'cc'}, {'id': 1, 'title': 'ICU overview', 'slug': 'icu-overview'}],
    }
    hits = data.get(query, [])
    return {'hits': hits, 'totalHits': len(hits)}


@override_settings(ALLOWED_HOSTS=['*'])
class SearchPageTests(TestCase):
    @patch('search.views.search_pages', side_effect=_fake_search)
    def test_or_query_interleaves_alternatives_without_duplicates(self, mock_search):
        response = self.client.get('/search/', {'q': 'ICU OR “critical care”'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.context['results']], [1, 3, 2])
        self.assertEqual(response.context['alternatives'], ['ICU', '"critical care"'])
        self.assertContains(response, '<meta name="robots" content="noindex, follow">')
        self.assertNotContains(response, 'rel="canonical"')
        self.assertEqual(mock_search.call_count, 2)

    @patch('search.views.search_pages', side_effect=_fake_search)
    def test_single_term(self, mock_search):
        response = self.client.get('/search/', {'q': 'ICU'})
        self.assertEqual([r['id'] for r in response.context['results']], [1, 2])
        self.assertEqual(response.context['alternatives'], [])

    def test_robots_blocks_search(self):
        self.assertContains(self.client.get('/robots.txt'), 'Disallow: /search/')
