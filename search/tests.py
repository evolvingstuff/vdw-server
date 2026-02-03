import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from meilisearch.errors import MeilisearchApiError
from requests import Response

from search.search import search_pages


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
        ) as init_patch:
            results = search_pages("cancer")

        self.assertEqual(results.get("hits"), [])
        init_patch.assert_called_once()
        self.assertEqual(mock_index.search.call_count, 2)
