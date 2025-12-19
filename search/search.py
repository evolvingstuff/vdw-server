import meilisearch
from django.conf import settings
from pages.models import Page


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
    index.update_searchable_attributes(
        [
            'title',
            'tags',
            'content',  # Plain text version for searching
        ]
    )

    # Configure filterable attributes
    index.update_filterable_attributes(
        [
            'status',
            'created_date',
            'tags',
        ]
    )

    # TODO: Disable typo tolerance to prevent "Metallica" matching "metallic"
    # The update_typo_tolerance() method is breaking search functionality
    # Need to find correct MeiliSearch Python client API for typo tolerance
    # index.update_typo_tolerance({
    #     'enabled': False
    # })

    # Configure ranking rules - prioritize attribute over proximity/exactness
    # Default Meilisearch order: words, typo, proximity, attribute, sort, exactness
    # We move 'attribute' before 'proximity' and 'exactness' to ensure that WHERE
    # matches occur (title > tags > content) takes priority over HOW close words
    # are to each other or how exact the matches are. This fixes issues where
    # pages with partial title matches ranked higher than pages with complete
    # title matches due to proximity/exactness factors.
    index.update_ranking_rules(
        [
            'words',  # Most important: number of matched terms
            'typo',  # Fewer typos = better
            'attribute',  # Where matches occur (title > tags > content) - MOVED UP
            'proximity',  # How close terms are to each other
            'sort',  # Custom sort criteria
            'exactness',  # Exact matches vs partial
        ]
    )

    return index


def clear_search_index():
    """Delete all documents from search index"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    index.delete_all_documents()


def format_page_for_search(page):
    """Convert Page object to search document format"""
    return {
        'id': page.pk,
        'title': page.title,
        'slug': page.slug,
        'content': page.content_text,  # Plain text for searching
        'content_html': page.content_html,  # HTML for display in results
        'tags': [tag.name for tag in page.tags.all()],
        'status': page.status,
        'created_date': page.created_date.isoformat(),
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

    results = index.search(
        query,
        {
            'limit': limit,
            'offset': offset,
            'filter': 'status = published',
            'attributesToHighlight': ['title', 'content'],
            'cropLength': 150,
            'attributesToCrop': ['content'],
        },
    )

    total_hits = extract_total_hits(results)
    if total_hits is not None:
        results['totalHits'] = total_hits

    return results
