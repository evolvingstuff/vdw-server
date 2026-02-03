import logging

import meilisearch
from django.conf import settings
from meilisearch.errors import MeilisearchApiError
from pages.models import Page

logger = logging.getLogger(__name__)


def get_search_client():
    """Get MeiliSearch client instance"""
    return meilisearch.Client(
        settings.MEILISEARCH_URL,
        settings.MEILISEARCH_MASTER_KEY,
    )


def initialize_search_index():
    """Initialize MeiliSearch index with proper configuration"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)

    # Configure searchable attributes - positional ranking only
    searchable_task = index.update_searchable_attributes(
        [
            'title',
            'tags',
            'content',  # Plain text version for searching
        ]
    )

    # Configure filterable attributes
    filterable_task = index.update_filterable_attributes(
        [
            'status',
            'created_date',
            'modified_date',
            'tags',
        ]
    )

    sortable_task = index.update_sortable_attributes(
        [
            'search_priority',
            'modified_date',
        ]
    )

    # TODO: Disable typo tolerance to prevent "Metallica" matching "metallic"
    # The update_typo_tolerance() method is breaking search functionality
    # Need to find correct MeiliSearch Python client API for typo tolerance
    # index.update_typo_tolerance({
    #     'enabled': False
    # })

    # Configure ranking rules - prioritize sort (importance + recency) first
    # Default Meilisearch order: words, typo, proximity, attribute, sort, exactness
    # We put 'sort' first so priority buckets and recency dominate ordering.
    # Attribute still comes before proximity/exactness to keep title/tag matches
    # above content-only matches when sort ties.
    ranking_task = index.update_ranking_rules(
        [
            'sort',  # Enforce priority + recency ordering
            'words',  # Most important: number of matched terms
            'typo',  # Fewer typos = better
            'attribute',  # Where matches occur (title > tags > content) - MOVED UP
            'proximity',  # How close terms are to each other
            'exactness',  # Exact matches vs partial
        ]
    )

    wait_for_task(index, searchable_task, 'searchable attributes')
    wait_for_task(index, filterable_task, 'filterable attributes')
    wait_for_task(index, sortable_task, 'sortable attributes')
    wait_for_task(index, ranking_task, 'ranking rules')

    return index


def clear_search_index():
    """Delete all documents from search index"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    index.delete_all_documents()


def wait_for_task(index, task_info, task_name: str) -> None:
    assert index is not None, "index is required"
    assert task_info is not None, f"{task_name} task_info is required"
    assert hasattr(task_info, 'task_uid'), f"{task_name} task_info missing task_uid"
    index.wait_for_task(task_info.task_uid)


def compute_search_priority(tag_names: list[str], title: str) -> int:
    assert isinstance(tag_names, list), f"tag_names must be list, got {type(tag_names)}"
    assert isinstance(title, str), f"title must be str, got {type(title)}"
    assert all(isinstance(name, str) for name in tag_names), "tag_names must contain strings"

    tag_names_lower = {name.casefold() for name in tag_names}
    title_casefold = title.casefold()
    many_studies_token = 'many studies'

    if 'overviews' in tag_names_lower:
        return 3
    if 'category' in tag_names_lower:
        return 2
    if many_studies_token in title_casefold or many_studies_token in tag_names_lower:
        return 1
    return 0


def format_page_for_search(page):
    """Convert Page object to search document format"""
    tag_names = [tag.name for tag in page.tags.all()]
    search_priority = compute_search_priority(tag_names, page.title)
    modified_timestamp = int(page.modified_date.timestamp())

    return {
        'id': page.pk,
        'title': page.title,
        'slug': page.slug,
        'content': page.content_text,  # Plain text for searching
        'content_html': page.content_html,  # HTML for display in results
        'tags': tag_names,
        'status': page.status,
        'created_date': page.created_date.isoformat(),
        'modified_date': modified_timestamp,
        'search_priority': search_priority,
    }


def index_page(page):
    """Index a single page in MeiliSearch"""
    if page.status != 'published':
        return  # Only index published pages

    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)

    document = format_page_for_search(page)
    index.add_documents([document])


def remove_page_from_search(page_id):
    """Remove a page from search index"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    index.delete_document(page_id)


def bulk_index_pages(pages_queryset):
    """Index multiple pages in batches"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)

    batch_size = 100
    batch = []

    for page in pages_queryset:
        if page.status == 'published':
            batch.append(format_page_for_search(page))

        if len(batch) >= batch_size:
            index.add_documents(batch)
            batch = []

    # Add remaining pages
    if batch:
        index.add_documents(batch)


def extract_total_hits(search_response: dict) -> int | None:
    """Extract total hit count across MeiliSearch response versions.

    - MeiliSearch v1+: 'estimatedTotalHits'
    - Older versions: 'nbHits'
    """
    if not isinstance(search_response, dict):
        return None

    if 'estimatedTotalHits' in search_response:
        return search_response.get('estimatedTotalHits')

    if 'nbHits' in search_response:
        return search_response.get('nbHits')

    if 'totalHits' in search_response:
        return search_response.get('totalHits')

    return None


def search_pages(query: str, limit: int = 20, offset: int = 0):
    """Search pages and return MeiliSearch results."""
    if not query.strip():
        return {'hits': [], 'query': query, 'totalHits': 0, 'limit': limit, 'offset': offset}

    assert isinstance(limit, int), f"limit must be int, got {type(limit)}"
    assert isinstance(offset, int), f"offset must be int, got {type(offset)}"
    assert limit >= 0, 'limit must be non-negative'
    assert offset >= 0, 'offset must be non-negative'

    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)

    search_payload = {
        'limit': limit,
        'offset': offset,
        'filter': 'status = published',
        'sort': [
            'search_priority:desc',
            'modified_date:desc',
        ],
        'attributesToHighlight': ['title', 'content'],
        'cropLength': 150,
        'attributesToCrop': ['content'],
    }

    try:
        results = index.search(query, search_payload)
    except MeilisearchApiError as exc:
        if exc.code == 'invalid_search_sort':
            logger.warning(
                "Search index missing sortable attributes; reinitializing index settings."
            )
            initialize_search_index()
            results = index.search(query, search_payload)
        else:
            raise

    total_hits = extract_total_hits(results)
    if total_hits is not None:
        results['totalHits'] = total_hits

    return results
