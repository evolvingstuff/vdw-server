import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.template.loader import render_to_string
from meilisearch.errors import MeilisearchApiError
from requests import Response

from search.search import (
    SEARCH_PRIORITY_CATEGORY,
    SEARCH_PRIORITY_EXTENDED,
    SEARCH_PRIORITY_MANY_STUDIES,
    SEARCH_PRIORITY_META_ANALYSIS,
    SEARCH_PRIORITY_OVERVIEW,
    SEARCH_PRIORITY_RCT,
    SEARCH_PRIORITY_SEVERAL_STUDIES,
    SEARCH_PRIORITY_SUMMARY,
    compute_search_priority,
    has_overview_query_match,
    search_pages,
    sort_hits_by_priority,
)


def build_invalid_sort_error() -> MeilisearchApiError:
    response = Response()
    response.status_code = 400
    response._content = json.dumps(
        {
            "message": "Index `posts`: Attribute `search_priority` is not sortable.",
            "code": "invalid_search_sort",
            "type": "invalid_request",
        }
    ).encode("utf-8")
    return MeilisearchApiError("invalid_search_sort", response)


class SearchSortRecoveryTests(SimpleTestCase):
    def test_retries_when_sortable_attributes_missing(self):
        mock_client = Mock()
        mock_index = Mock()
        mock_client.index.return_value = mock_index
        mock_index.search.side_effect = [
            build_invalid_sort_error(),
            {"hits": [], "totalHits": 0},
        ]

        with patch("search.search.get_search_client", return_value=mock_client), patch(
            "search.search.initialize_search_index"
        ) as init_patch, patch(
            "search.search.fetch_overview_hits",
            return_value=[],
        ):
            results = search_pages("cancer")

        self.assertEqual(results.get("hits"), [])
        init_patch.assert_called_once()
        self.assertEqual(mock_index.search.call_count, 2)

    def test_hyphenated_query_uses_all_matching_strategy(self):
        mock_client = Mock()
        mock_index = Mock()
        mock_client.index.return_value = mock_index
        mock_index.search.return_value = {"hits": [], "totalHits": 0}

        with patch("search.search.get_search_client", return_value=mock_client), patch(
            "search.search.fetch_overview_hits",
            return_value=[],
        ):
            search_pages("non-alcohol")

        _, search_payload = mock_index.search.call_args.args
        self.assertEqual(search_payload.get("matchingStrategy"), "all")


class SearchPriorityTests(SimpleTestCase):
    def test_summary_tag_ranks_highest(self):
        priority = compute_search_priority(["Summary"], ["summary"], "Alcohol and Vitamin D")
        self.assertEqual(priority, SEARCH_PRIORITY_SUMMARY)

    def test_overview_tags_rank_highest(self):
        priority = compute_search_priority(["Overview"], ["overview"], "Alcohol and Vitamin D")
        self.assertEqual(priority, SEARCH_PRIORITY_OVERVIEW)

    def test_overview_title_rank_highest(self):
        priority = compute_search_priority([], [], "Overview Alcohol and Vitamin D")
        self.assertEqual(priority, SEARCH_PRIORITY_OVERVIEW)

    def test_overview_phrase_in_tag_ranks_highest(self):
        priority = compute_search_priority(
            ["Overview for doctors"],
            ["overview-for-doctors"],
            "Alcohol and Vitamin D",
        )
        self.assertEqual(priority, SEARCH_PRIORITY_OVERVIEW)

    def test_extended_tag_ranks_below_overview(self):
        priority = compute_search_priority(["Extended"], ["extended"], "Alcohol and Vitamin D")
        self.assertEqual(priority, SEARCH_PRIORITY_EXTENDED)

    def test_category_tag_ranks_third(self):
        priority = compute_search_priority(["category"], ["category"], "Alcohol and Vitamin D")
        self.assertEqual(priority, SEARCH_PRIORITY_CATEGORY)

    def test_many_studies_title_ranks_second(self):
        priority = compute_search_priority([], [], "Alcohol and Vitamin D - many studies")
        self.assertEqual(priority, SEARCH_PRIORITY_MANY_STUDIES)

    def test_many_studies_slug_ranks_second(self):
        priority = compute_search_priority(["Many Studies"], ["many-studies"], "Alcohol and Vitamin D")
        self.assertEqual(priority, SEARCH_PRIORITY_MANY_STUDIES)

    def test_several_studies_title_ranks_below_many_studies(self):
        priority = compute_search_priority([], [], "Alcohol and Vitamin D - several studies")
        self.assertEqual(priority, SEARCH_PRIORITY_SEVERAL_STUDIES)

    def test_meta_analysis_tag_ranks_above_rct(self):
        priority = compute_search_priority(
            ["Meta-analysis"],
            ["meta-analysis"],
            "Alcohol and Vitamin D",
        )
        self.assertEqual(priority, SEARCH_PRIORITY_META_ANALYSIS)

    def test_rct_title_ranks_above_category(self):
        priority = compute_search_priority([], [], "Alcohol and Vitamin D - 12 RCT")
        self.assertEqual(priority, SEARCH_PRIORITY_RCT)


class SearchHitSortingTests(SimpleTestCase):
    def test_overview_hits_sort_ahead_of_many_studies(self):
        hits = [
            {
                "id": 1,
                "title": "Microplastics - many studies",
                "slug": "microplastics",
                "content": "mentions alcohol",
                "content_html": "<p>mentions alcohol</p>",
                "tags": ["Interactions"],
                "modified_date": 200,
            },
            {
                "id": 2,
                "title": "Overview Alcohol and Vitamin D",
                "slug": "overview-alcohol",
                "content": "alcohol overview content",
                "content_html": "<p>alcohol overview content</p>",
                "tags": ["Overview"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_query_match_sorts_within_priority_bucket(self):
        hits = [
            {
                "id": 1,
                "title": "Overview Fibromyalgia and vitamin D",
                "slug": "overview-fibromyalgia",
                "content": "no gut mention",
                "content_html": "<p>no gut mention</p>",
                "tags": ["Overview"],
                "modified_date": 200,
            },
            {
                "id": 2,
                "title": "Overview Gut and vitamin D",
                "slug": "overview-gut",
                "content": "gut mention",
                "content_html": "<p>gut mention</p>",
                "tags": ["Overview", "Gut"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "gut")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_matching_non_overview_beats_unrelated_overview(self):
        hits = [
            {
                "id": 1,
                "title": "Overview Gut and vitamin D",
                "slug": "overview-gut",
                "content": "overview content",
                "content_html": "<p>overview content</p>",
                "tags": ["Overview"],
                "modified_date": 300,
            },
            {
                "id": 2,
                "title": "Alcohol and Vitamin D - many studies",
                "slug": "alcohol-many-studies",
                "content": "alcohol content",
                "content_html": "<p>alcohol content</p>",
                "tags": ["Many Studies"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_title_match_overview_beats_other_title_matches(self):
        hits = [
            {
                "id": 1,
                "title": "Addictions to smoking, alcohol, opiates, etc.",
                "slug": "addictions-alcohol",
                "content": "alcohol content",
                "content_html": "<p>alcohol content</p>",
                "tags": [],
                "modified_date": 200,
            },
            {
                "id": 2,
                "title": "Overview Alcohol and Vitamin D",
                "slug": "overview-alcohol",
                "content": "overview content",
                "content_html": "<p>overview content</p>",
                "tags": ["Overview"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_summary_strong_match_beats_overview(self):
        hits = [
            {
                "id": 1,
                "title": "Alcohol and Vitamin D overview",
                "slug": "overview-alcohol",
                "content": "overview content",
                "content_html": "<p>overview content</p>",
                "tags": ["Overview"],
                "modified_date": 200,
            },
            {
                "id": 2,
                "title": "Alcohol and Vitamin D quick guide",
                "slug": "summary-alcohol",
                "content": "summary content",
                "content_html": "<p>summary content</p>",
                "tags": ["Summary"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_unrelated_summary_does_not_beat_relevant_many_studies(self):
        hits = [
            {
                "id": 1,
                "title": "Gut and vitamin D quick guide",
                "slug": "summary-gut",
                "content": "alcohol is mentioned in content only",
                "content_html": "<p>alcohol is mentioned in content only</p>",
                "tags": ["Summary"],
                "modified_date": 300,
            },
            {
                "id": 2,
                "title": "Alcohol and Vitamin D - many studies",
                "slug": "many-studies-alcohol",
                "content": "alcohol content",
                "content_html": "<p>alcohol content</p>",
                "tags": ["Many Studies"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_extended_strong_match_beats_many_studies(self):
        hits = [
            {
                "id": 1,
                "title": "Alcohol and Vitamin D - many studies",
                "slug": "many-studies-alcohol",
                "content": "many studies content",
                "content_html": "<p>many studies content</p>",
                "tags": ["Many Studies"],
                "modified_date": 200,
            },
            {
                "id": 2,
                "title": "Alcohol and Vitamin D extended guide",
                "slug": "extended-alcohol",
                "content": "extended content",
                "content_html": "<p>extended content</p>",
                "tags": ["Extended"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_meta_analysis_beats_rct(self):
        hits = [
            {
                "id": 1,
                "title": "Alcohol and Vitamin D trial roundup",
                "slug": "rct-alcohol",
                "content": "trial content",
                "content_html": "<p>trial content</p>",
                "tags": ["RCT"],
                "modified_date": 200,
            },
            {
                "id": 2,
                "title": "Alcohol and Vitamin D research synthesis",
                "slug": "meta-analysis-alcohol",
                "content": "synthesis content",
                "content_html": "<p>synthesis content</p>",
                "tags": ["Meta-analysis"],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_non_alcoholic_meta_analysis_does_not_get_alcohol_priority_boost(self):
        hits = [
            {
                "id": 1,
                "title": "Non-Alcoholic Fatty Liver Disease treated by Omega-3 - meta-analysis",
                "slug": "nafld-meta-analysis",
                "content": "non-alcoholic content",
                "content_html": "<p>non-alcoholic content</p>",
                "tags": ["Meta-analysis"],
                "modified_date": 300,
            },
            {
                "id": 2,
                "title": "Alcohol and Vitamin D basics",
                "slug": "alcohol-basics",
                "content": "alcohol content",
                "content_html": "<p>alcohol content</p>",
                "tags": [],
                "modified_date": 100,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "alcohol")

        self.assertEqual(sorted_hits[0]["id"], 2)

    def test_space_separated_query_matches_hyphenated_meta_analysis_tag(self):
        hits = [
            {
                "id": 1,
                "title": "Research synthesis page",
                "slug": "research-synthesis",
                "content": "synthesis content",
                "content_html": "<p>synthesis content</p>",
                "tags": ["Meta-analysis"],
                "modified_date": 100,
            },
            {
                "id": 2,
                "title": "General research page",
                "slug": "general-research",
                "content": "general content",
                "content_html": "<p>general content</p>",
                "tags": [],
                "modified_date": 300,
            },
        ]

        sorted_hits = sort_hits_by_priority(hits, "meta analysis")

        self.assertEqual(sorted_hits[0]["id"], 1)

    def test_overview_query_match_detection(self):
        hit = {
            "title": "Overview Alcohol and Vitamin D",
            "tags": [],
        }

        self.assertTrue(has_overview_query_match(hit, "alcohol"))

    def test_overview_query_match_ignores_non_alcoholic(self):
        hit = {
            "title": "Overview Non-Alcoholic Fatty Liver Disease",
            "tags": [],
        }

        self.assertFalse(has_overview_query_match(hit, "alcohol"))


class SearchPageTemplateTests(SimpleTestCase):
    def test_search_help_link_is_present_in_global_header(self):
        html = render_to_string("components/global_search_bar.html")

        self.assertIn(
            'href="/pages/search-vitamindwiki/"',
            html,
        )
        self.assertIn(
            'aria-label="How search works"',
            html,
        )
