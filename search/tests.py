import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from meilisearch.errors import MeilisearchApiError
from requests import Response

from search.search import (
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


class SearchPriorityTests(SimpleTestCase):
    def test_overview_tags_rank_highest(self):
        priority = compute_search_priority(["Overview"], ["overview"], "Alcohol and Vitamin D")
        self.assertEqual(priority, 3)

    def test_overview_title_rank_highest(self):
        priority = compute_search_priority([], [], "Overview Alcohol and Vitamin D")
        self.assertEqual(priority, 3)

    def test_overview_phrase_in_tag_ranks_highest(self):
        priority = compute_search_priority(
            ["Overview for doctors"],
            ["overview-for-doctors"],
            "Alcohol and Vitamin D",
        )
        self.assertEqual(priority, 3)

    def test_category_tag_ranks_third(self):
        priority = compute_search_priority(["category"], ["category"], "Alcohol and Vitamin D")
        self.assertEqual(priority, 1)

    def test_many_studies_title_ranks_second(self):
        priority = compute_search_priority([], [], "Alcohol and Vitamin D - many studies")
        self.assertEqual(priority, 2)

    def test_many_studies_slug_ranks_second(self):
        priority = compute_search_priority(["Many Studies"], ["many-studies"], "Alcohol and Vitamin D")
        self.assertEqual(priority, 2)


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

    def test_overview_query_match_detection(self):
        hit = {
            "title": "Overview Alcohol and Vitamin D",
            "tags": [],
        }

        self.assertTrue(has_overview_query_match(hit, "alcohol"))
