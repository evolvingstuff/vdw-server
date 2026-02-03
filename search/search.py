import logging

import meilisearch
from django.conf import settings
from django.utils.text import slugify
from meilisearch.errors import MeilisearchApiError
from django.db.models import Q
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


def compute_search_priority(tag_names: list[str], tag_slugs: list[str], title: str) -> int:
    assert isinstance(tag_names, list), f"tag_names must be list, got {type(tag_names)}"
    assert isinstance(tag_slugs, list), f"tag_slugs must be list, got {type(tag_slugs)}"
    assert isinstance(title, str), f"title must be str, got {type(title)}"
    assert all(isinstance(name, str) for name in tag_names), "tag_names must contain strings"
    assert all(isinstance(slug, str) for slug in tag_slugs), "tag_slugs must contain strings"

    tag_names_lower = [name.casefold() for name in tag_names]
    tag_slugs_lower = [slug.casefold() for slug in tag_slugs]
    tag_keys = tag_names_lower + tag_slugs_lower
    normalized_tag_keys = [key.replace('-', ' ') for key in tag_keys]

    title_casefold = title.casefold()
    many_studies_token = 'many studies'

    has_overview = any('overview' in key for key in normalized_tag_keys) or ('overview' in title_casefold)
    has_category = any(key.startswith('category') for key in normalized_tag_keys)

    if has_overview:
        return 3
    if many_studies_token in title_casefold or any(many_studies_token in key for key in normalized_tag_keys):
        return 2
    if has_category:
        return 1
    return 0


def derive_tag_slugs(tag_names: list[str]) -> list[str]:
    assert isinstance(tag_names, list), f"tag_names must be list, got {type(tag_names)}"
    assert all(isinstance(name, str) for name in tag_names), "tag_names must contain strings"

    return [slugify(name) for name in tag_names]


def is_overview_hit(title_casefold: str, tags_casefold: list[str]) -> bool:
    assert isinstance(title_casefold, str), f"title_casefold must be str, got {type(title_casefold)}"
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    return 'overview' in title_casefold or any('overview' in tag for tag in tags_casefold)


def is_many_studies_hit(title_casefold: str) -> bool:
    assert isinstance(title_casefold, str), f"title_casefold must be str, got {type(title_casefold)}"
    return 'many studies' in title_casefold


def is_category_hit(tags_casefold: list[str]) -> bool:
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    return any(tag.startswith('category') for tag in tags_casefold)


def sort_hits_by_priority(hits: list[dict], query: str) -> list[dict]:
    assert isinstance(hits, list), f"hits must be list, got {type(hits)}"
    assert all(isinstance(hit, dict) for hit in hits), "hits must contain dicts"
    assert isinstance(query, str), f"query must be str, got {type(query)}"

    sortable_hits = []
    query_casefold = query.casefold()
    for hit in hits:
        title = hit['title']
        tags = hit['tags']
        modified_date = hit['modified_date']
        assert isinstance(title, str), f"title must be str, got {type(title)}"
        assert isinstance(tags, list), f"tags must be list, got {type(tags)}"
        assert isinstance(modified_date, int), f"modified_date must be int, got {type(modified_date)}"

        title_casefold = title.casefold()
        tags_casefold = [tag.casefold() for tag in tags]
        title_match = 1 if query_casefold and query_casefold in title_casefold else 0
        tag_match = 1 if query_casefold and any(query_casefold == tag for tag in tags_casefold) else 0
        strong_match = 1 if title_match or tag_match else 0

        has_overview = is_overview_hit(title_casefold, tags_casefold)
        has_many_studies = is_many_studies_hit(title_casefold)
        has_category = is_category_hit(tags_casefold)

        if has_overview and strong_match:
            effective_priority = 3
        elif has_many_studies:
            effective_priority = 2
        elif has_category and strong_match:
            effective_priority = 1
        else:
            effective_priority = 0

        hit['_sort_modified_date'] = modified_date
        hit['_sort_strong_match'] = strong_match
        hit['_sort_effective_priority'] = effective_priority
        sortable_hits.append(hit)

    sortable_hits.sort(
        key=lambda hit: (
            hit['_sort_effective_priority'],
            hit['_sort_strong_match'],
            hit['_sort_modified_date'],
        ),
        reverse=True,
    )

    for hit in sortable_hits:
        del hit['_sort_modified_date']
        del hit['_sort_strong_match']
        del hit['_sort_effective_priority']

    return sortable_hits


def has_overview_query_match(hit: dict, query_casefold: str) -> bool:
    assert isinstance(hit, dict), f"hit must be dict, got {type(hit)}"
    assert isinstance(query_casefold, str), f"query_casefold must be str, got {type(query_casefold)}"

    title = hit.get('title')
    tags = hit.get('tags')
    if not isinstance(title, str) or not isinstance(tags, list):
        return False

    title_casefold = title.casefold()
    tags_casefold = [tag.casefold() for tag in tags if isinstance(tag, str)]
    if not is_overview_hit(title_casefold, tags_casefold):
        return False

    if not query_casefold:
        return False

    if query_casefold in title_casefold:
        return True

    return any(query_casefold == tag for tag in tags_casefold)


def fetch_overview_hits(query: str, limit: int = 10) -> list[dict]:
    assert isinstance(query, str), f"query must be str, got {type(query)}"
    assert isinstance(limit, int), f"limit must be int, got {type(limit)}"
    assert limit > 0, "limit must be positive"

    trimmed = query.strip()
    if not trimmed:
        return []

    overview_pages = (
        Page.objects.filter(status='published')
        .filter(Q(title__icontains='overview') | Q(tags__name__icontains='overview'))
        .filter(Q(title__icontains=trimmed) | Q(tags__name__iexact=trimmed))
        .distinct()
        .order_by('-modified_date')[:limit]
    )

    return [format_page_for_search(page) for page in overview_pages]


def format_page_for_search(page):
    """Convert Page object to search document format"""
    tags = list(page.tags.all())
    tag_names = [tag.name for tag in tags]
    tag_slugs = [tag.slug for tag in tags]
    search_priority = compute_search_priority(tag_names, tag_slugs, page.title)
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

    max_hits = 1000
    fetch_limit = max_hits
    search_payload = {
        'limit': fetch_limit,
        'offset': 0,
        'filter': 'status = published',
        'sort': [
            'search_priority:desc',
            'modified_date:desc',
        ],
        'attributesToRetrieve': [
            'id',
            'title',
            'slug',
            'content',
            'content_html',
            'tags',
            'modified_date',
            'search_priority',
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

    hits = results.get('hits')
    assert isinstance(hits, list), f"hits must be list, got {type(hits)}"
    sorted_hits = sort_hits_by_priority(hits, query)
    query_casefold = query.casefold()
    has_overview_match = any(
        has_overview_query_match(hit, query_casefold) for hit in sorted_hits
    )

    if not has_overview_match:
        overview_hits = fetch_overview_hits(query)
        if overview_hits:
            overview_ids = {hit['id'] for hit in overview_hits}
            sorted_hits = overview_hits + [
                hit for hit in sorted_hits if hit.get('id') not in overview_ids
            ]

    results['hits'] = sorted_hits[offset : offset + limit]

    total_hits = extract_total_hits(results)
    if total_hits is not None:
        results['totalHits'] = total_hits

    return results
