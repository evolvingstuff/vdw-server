"""Tag queries shared by the topic browser, tag pages and the sitemap.

Tag membership uses Page.derived_tags: the tags assigned in admin plus tags implied by
words in the page title.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from django.db.models import Count, Q, QuerySet

from pages.models import Page
from search.search import compute_search_priority
from tags.models import Tag

TAG_PAGE_LIST_LIMIT = 1000


@dataclass(frozen=True)
class TagCount:
    id: int
    name: str
    slug: str
    count: int


def published_pages_with_all_tags(tags: Sequence[Tag]) -> QuerySet:
    assert tags, "At least one tag is required"
    queryset = Page.objects.filter(status='published')
    for tag in tags:  # chained filters = AND across tags
        queryset = queryset.filter(derived_tags=tag)
    return queryset


def tag_counts(selected: Sequence[Tag] = ()) -> List[TagCount]:
    """Published page counts per tag; with `selected`, only pages that have all selected tags."""
    if selected:
        pages = published_pages_with_all_tags(selected)
        rows = (
            Tag.objects
            .filter(derived_pages__in=pages)
            .annotate(page_count=Count('derived_pages'))
        )
    else:
        rows = Tag.objects.annotate(
            page_count=Count('derived_pages', filter=Q(derived_pages__status='published'))
        ).filter(page_count__gt=0)
    rows = rows.order_by('-page_count', 'name').values('id', 'name', 'slug', 'page_count')
    return [TagCount(r['id'], r['name'], r['slug'], r['page_count']) for r in rows]


def pages_in_search_order(pages: QuerySet) -> List[dict]:
    """All pages ordered like site search: priority bucket (Summary, Overview, ... RCT), then newest."""
    page_rows = list(pages.values('id', 'title', 'slug', 'public_modified_date'))
    tag_names = defaultdict(list)
    tag_slugs = defaultdict(list)
    links = Page.derived_tags.through.objects.filter(page__in=pages).values_list(
        'page_id', 'tag__name', 'tag__slug'
    )
    for page_id, name, slug in links:
        tag_names[page_id].append(name)
        tag_slugs[page_id].append(slug)

    for row in page_rows:
        row['priority'] = compute_search_priority(tag_names[row['id']], tag_slugs[row['id']], row['title'])
    page_rows.sort(key=lambda r: (r['priority'], r['public_modified_date']), reverse=True)
    return page_rows


def tags_by_slug(slugs: Iterable[str]) -> List[Tag]:
    """Tags for the given slugs, in the given order; raises Tag.DoesNotExist for unknown slugs."""
    unique = list(dict.fromkeys(slugs))
    found = {tag.slug: tag for tag in Tag.objects.filter(slug__in=unique)}
    missing = [slug for slug in unique if slug not in found]
    if missing:
        raise Tag.DoesNotExist(f"Unknown tag(s): {', '.join(missing)}")
    return [found[slug] for slug in unique]
