import logging
import re
import time

import meilisearch
from django.conf import settings
from django.db.models import Q
from django.utils.text import slugify
from meilisearch.errors import MeilisearchApiError

from pages.models import Page

logger = logging.getLogger(__name__)

SEARCH_PRIORITY_CATEGORY = 1
SEARCH_PRIORITY_RCT = 2
SEARCH_PRIORITY_META_ANALYSIS = 3
SEARCH_PRIORITY_SEVERAL_STUDIES = 4
SEARCH_PRIORITY_MANY_STUDIES = 5
SEARCH_PRIORITY_EXTENDED = 6
SEARCH_PRIORITY_OVERVIEW = 7
SEARCH_PRIORITY_SUMMARY = 8

SUMMARY_PATTERN = re.compile(r'(?<![a-z0-9])summary(?![a-z0-9])')
OVERVIEW_PATTERN = re.compile(r'(?<![a-z0-9])overview(?![a-z0-9])')
EXTENDED_PATTERN = re.compile(r'(?<![a-z0-9])extended(?![a-z0-9])')
MANY_STUDIES_PATTERN = re.compile(r'(?<![a-z0-9])many\s+studies(?![a-z0-9])')
SEVERAL_STUDIES_PATTERN = re.compile(r'(?<![a-z0-9])several\s+studies(?![a-z0-9])')
META_ANALYSIS_PATTERN = re.compile(r'(?<![a-z0-9])meta\s+analysis(?![a-z0-9])')
RCT_PATTERN = re.compile(r'(?<![a-z0-9])(?:\d+\s+)?rct(?![a-z0-9])')
QUERY_TOKEN_PATTERN = re.compile(r'[a-z0-9]+')
MAX_SEARCH_HITS = 1000
MIN_RERANK_CANDIDATES = 100
RERANK_CANDIDATE_BUFFER = 40
SLOW_SEARCH_THRESHOLD_MS = 250.0


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


def normalize_priority_text(value: str) -> str:
    assert isinstance(value, str), f"value must be str, got {type(value)}"

    normalized = value.casefold()
    normalized = re.sub(r'[-_]+', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def normalize_query_match_text(value: str) -> str:
    assert isinstance(value, str), f"value must be str, got {type(value)}"

    return value.casefold()


def extract_query_tokens(query: str) -> list[str]:
    assert isinstance(query, str), f"query must be str, got {type(query)}"

    return QUERY_TOKEN_PATTERN.findall(normalize_query_match_text(query))


def build_query_match_pattern(query: str) -> re.Pattern[str] | None:
    assert isinstance(query, str), f"query must be str, got {type(query)}"

    query_tokens = extract_query_tokens(query)
    if not query_tokens:
        return None

    pattern = r'(?<![a-z0-9])' + r'[\s_-]+'.join(re.escape(token) for token in query_tokens) + r'(?![a-z0-9])'
    return re.compile(pattern)


def has_clean_query_match(text: str, query_pattern: re.Pattern[str] | None) -> bool:
    assert isinstance(text, str), f"text must be str, got {type(text)}"

    if query_pattern is None:
        return False

    return bool(query_pattern.search(normalize_query_match_text(text)))


def compute_search_priority(tag_names: list[str], tag_slugs: list[str], title: str) -> int:
    assert isinstance(tag_names, list), f"tag_names must be list, got {type(tag_names)}"
    assert isinstance(tag_slugs, list), f"tag_slugs must be list, got {type(tag_slugs)}"
    assert isinstance(title, str), f"title must be str, got {type(title)}"
    assert all(isinstance(name, str) for name in tag_names), "tag_names must contain strings"
    assert all(isinstance(slug, str) for slug in tag_slugs), "tag_slugs must contain strings"

    normalized_tag_keys = [
        normalize_priority_text(key) for key in [*tag_names, *tag_slugs]
    ]
    normalized_title = normalize_priority_text(title)

    if is_summary_hit(normalized_tag_keys):
        return SEARCH_PRIORITY_SUMMARY
    if is_overview_hit(normalized_title, normalized_tag_keys):
        return SEARCH_PRIORITY_OVERVIEW
    if is_extended_hit(normalized_tag_keys):
        return SEARCH_PRIORITY_EXTENDED
    if is_many_studies_hit(normalized_title, normalized_tag_keys):
        return SEARCH_PRIORITY_MANY_STUDIES
    if is_several_studies_hit(normalized_title, normalized_tag_keys):
        return SEARCH_PRIORITY_SEVERAL_STUDIES
    if is_meta_analysis_hit(normalized_tag_keys):
        return SEARCH_PRIORITY_META_ANALYSIS
    if is_rct_hit(normalized_title, normalized_tag_keys):
        return SEARCH_PRIORITY_RCT
    if is_category_hit(normalized_tag_keys):
        return SEARCH_PRIORITY_CATEGORY
    return 0


def derive_tag_slugs(tag_names: list[str]) -> list[str]:
    assert isinstance(tag_names, list), f"tag_names must be list, got {type(tag_names)}"
    assert all(isinstance(name, str) for name in tag_names), "tag_names must contain strings"

    return [slugify(name) for name in tag_names]


def is_summary_hit(tags_casefold: list[str]) -> bool:
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    return any(SUMMARY_PATTERN.search(tag) for tag in tags_casefold)


def is_overview_hit(title_casefold: str, tags_casefold: list[str]) -> bool:
    assert isinstance(title_casefold, str), f"title_casefold must be str, got {type(title_casefold)}"
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    return bool(OVERVIEW_PATTERN.search(title_casefold)) or any(
        OVERVIEW_PATTERN.search(tag) for tag in tags_casefold
    )


def is_extended_hit(tags_casefold: list[str]) -> bool:
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    return any(EXTENDED_PATTERN.search(tag) for tag in tags_casefold)


def is_many_studies_hit(title_casefold: str, tags_casefold: list[str]) -> bool:
    assert isinstance(title_casefold, str), f"title_casefold must be str, got {type(title_casefold)}"
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    if MANY_STUDIES_PATTERN.search(title_casefold):
        return True
    return any(MANY_STUDIES_PATTERN.search(tag) for tag in tags_casefold)


def is_several_studies_hit(title_casefold: str, tags_casefold: list[str]) -> bool:
    assert isinstance(title_casefold, str), f"title_casefold must be str, got {type(title_casefold)}"
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    if SEVERAL_STUDIES_PATTERN.search(title_casefold):
        return True
    return any(SEVERAL_STUDIES_PATTERN.search(tag) for tag in tags_casefold)


def is_meta_analysis_hit(tags_casefold: list[str]) -> bool:
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    return any(META_ANALYSIS_PATTERN.search(tag) for tag in tags_casefold)


def is_rct_hit(title_casefold: str, tags_casefold: list[str]) -> bool:
    assert isinstance(title_casefold, str), f"title_casefold must be str, got {type(title_casefold)}"
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    if RCT_PATTERN.search(title_casefold):
        return True
    return any(RCT_PATTERN.search(tag) for tag in tags_casefold)


def is_category_hit(tags_casefold: list[str]) -> bool:
    assert isinstance(tags_casefold, list), f"tags_casefold must be list, got {type(tags_casefold)}"
    assert all(isinstance(tag, str) for tag in tags_casefold), "tags_casefold must contain strings"

    return any(tag.startswith('category') for tag in tags_casefold)


def sort_hits_by_priority(hits: list[dict], query: str) -> list[dict]:
    assert isinstance(hits, list), f"hits must be list, got {type(hits)}"
    assert all(isinstance(hit, dict) for hit in hits), "hits must contain dicts"
    assert isinstance(query, str), f"query must be str, got {type(query)}"

    sortable_hits = []
    query_pattern = build_query_match_pattern(query)
    for hit in hits:
        title = hit['title']
        tags = hit['tags']
        modified_date = hit['modified_date']
        assert isinstance(title, str), f"title must be str, got {type(title)}"
        assert isinstance(tags, list), f"tags must be list, got {type(tags)}"
        assert isinstance(modified_date, int), f"modified_date must be int, got {type(modified_date)}"

        title_casefold = normalize_priority_text(title)
        tags_casefold = [normalize_priority_text(tag) for tag in tags]
        title_match = 1 if has_clean_query_match(title, query_pattern) else 0
        tag_match = 1 if any(has_clean_query_match(tag, query_pattern) for tag in tags) else 0
        strong_match = 1 if title_match or tag_match else 0

        has_summary = is_summary_hit(tags_casefold)
        has_overview = is_overview_hit(title_casefold, tags_casefold)
        has_extended = is_extended_hit(tags_casefold)
        has_many_studies = is_many_studies_hit(title_casefold, tags_casefold)
        has_several_studies = is_several_studies_hit(title_casefold, tags_casefold)
        has_meta_analysis = is_meta_analysis_hit(tags_casefold)
        has_rct = is_rct_hit(title_casefold, tags_casefold)
        has_category = is_category_hit(tags_casefold)

        if has_summary and strong_match:
            effective_priority = SEARCH_PRIORITY_SUMMARY
        elif has_overview and strong_match:
            effective_priority = SEARCH_PRIORITY_OVERVIEW
        elif has_extended and strong_match:
            effective_priority = SEARCH_PRIORITY_EXTENDED
        elif has_many_studies and strong_match:
            effective_priority = SEARCH_PRIORITY_MANY_STUDIES
        elif has_several_studies and strong_match:
            effective_priority = SEARCH_PRIORITY_SEVERAL_STUDIES
        elif has_meta_analysis and strong_match:
            effective_priority = SEARCH_PRIORITY_META_ANALYSIS
        elif has_rct and strong_match:
            effective_priority = SEARCH_PRIORITY_RCT
        elif has_category and strong_match:
            effective_priority = SEARCH_PRIORITY_CATEGORY
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

    query_pattern = build_query_match_pattern(query_casefold)
    return _has_overview_query_match_pattern(hit, query_pattern)


def _has_overview_query_match_pattern(hit: dict, query_pattern: re.Pattern[str] | None) -> bool:
    assert isinstance(hit, dict), f"hit must be dict, got {type(hit)}"

    title = hit.get('title')
    tags = hit.get('tags')
    if not isinstance(title, str) or not isinstance(tags, list):
        return False

    title_casefold = normalize_priority_text(title)
    tags_casefold = [normalize_priority_text(tag) for tag in tags if isinstance(tag, str)]
    if not is_overview_hit(title_casefold, tags_casefold):
        return False

    if query_pattern is None:
        return False

    if has_clean_query_match(title, query_pattern):
        return True

    return any(has_clean_query_match(tag, query_pattern) for tag in tags if isinstance(tag, str))


def compute_fetch_limit(limit: int, offset: int) -> int:
    assert isinstance(limit, int), f"limit must be int, got {type(limit)}"
    assert isinstance(offset, int), f"offset must be int, got {type(offset)}"
    assert limit >= 0, 'limit must be non-negative'
    assert offset >= 0, 'offset must be non-negative'

    candidate_window = offset + limit + RERANK_CANDIDATE_BUFFER
    return min(
        MAX_SEARCH_HITS,
        max(candidate_window, MIN_RERANK_CANDIDATES),
    )


def log_slow_search_timing(
    query: str,
    limit: int,
    offset: int,
    fetch_limit: int,
    raw_hits_count: int,
    total_hits: int | None,
    meili_ms: float,
    rerank_ms: float,
    overview_ms: float,
    total_ms: float,
) -> None:
    assert isinstance(query, str), f"query must be str, got {type(query)}"

    if total_ms < SLOW_SEARCH_THRESHOLD_MS:
        return

    query_preview = query if len(query) <= 80 else f"{query[:77]}..."
    logger.warning(
        (
            "Slow search query=%r limit=%s offset=%s fetch_limit=%s raw_hits=%s total_hits=%s "
            "meili_ms=%.1f rerank_ms=%.1f overview_ms=%.1f total_ms=%.1f"
        ),
        query_preview,
        limit,
        offset,
        fetch_limit,
        raw_hits_count,
        total_hits,
        meili_ms,
        rerank_ms,
        overview_ms,
        total_ms,
    )


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
    tags = list(page.derived_tags.all())
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
        page.update_derived_tags()
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
    search_started_at = time.perf_counter()

    display_mode = settings.SEARCH_RESULTS_DISPLAY_MODE
    include_content = display_mode == 'full'

    attributes_to_retrieve = [
        'id',
        'title',
        'slug',
        'tags',
        'modified_date',
        'search_priority',
    ]
    attributes_to_highlight = ['title']
    attributes_to_crop = []

    if include_content:
        attributes_to_retrieve.append('content')
        attributes_to_highlight.append('content')
        attributes_to_crop.append('content')

    fetch_limit = compute_fetch_limit(limit, offset)
    search_payload = {
        'limit': fetch_limit,
        'offset': 0,
        'filter': 'status = published',
        'sort': [
            'search_priority:desc',
            'modified_date:desc',
        ],
        'attributesToRetrieve': attributes_to_retrieve,
        'attributesToHighlight': attributes_to_highlight,
    }
    if include_content:
        search_payload['cropLength'] = 150
        search_payload['attributesToCrop'] = attributes_to_crop

    query_terms = extract_query_tokens(query)
    if len(query_terms) >= 2:
        search_payload['matchingStrategy'] = 'all'

    meili_started_at = time.perf_counter()
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
    meili_ms = (time.perf_counter() - meili_started_at) * 1000

    hits = results.get('hits')
    assert isinstance(hits, list), f"hits must be list, got {type(hits)}"
    rerank_started_at = time.perf_counter()
    sorted_hits = sort_hits_by_priority(hits, query)
    overview_query_pattern = build_query_match_pattern(query)
    has_overview_match = any(
        _has_overview_query_match_pattern(hit, overview_query_pattern) for hit in sorted_hits
    )
    rerank_ms = (time.perf_counter() - rerank_started_at) * 1000

    overview_ms = 0.0
    if not has_overview_match:
        overview_started_at = time.perf_counter()
        overview_hits = fetch_overview_hits(query)
        overview_ms = (time.perf_counter() - overview_started_at) * 1000
        if overview_hits:
            overview_ids = {hit['id'] for hit in overview_hits}
            sorted_hits = overview_hits + [
                hit for hit in sorted_hits if hit.get('id') not in overview_ids
            ]

    results['hits'] = sorted_hits[offset : offset + limit]

    total_hits = extract_total_hits(results)
    if total_hits is not None:
        results['totalHits'] = total_hits

    total_ms = (time.perf_counter() - search_started_at) * 1000
    log_slow_search_timing(
        query=query,
        limit=limit,
        offset=offset,
        fetch_limit=fetch_limit,
        raw_hits_count=len(hits),
        total_hits=total_hits,
        meili_ms=meili_ms,
        rerank_ms=rerank_ms,
        overview_ms=overview_ms,
        total_ms=total_ms,
    )

    return results
