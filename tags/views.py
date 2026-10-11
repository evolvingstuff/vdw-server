from django.http import Http404, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode

from .models import Tag
from .services import (
    TAG_PAGE_LIST_LIMIT,
    pages_in_search_order,
    published_pages_with_all_tags,
    tag_counts,
    tags_by_slug,
)

INITIAL_TOPIC_ROWS = 100


def tag_pages(request, tag_slug):
    """All pages with a tag (plus any extra ?and=<slug> tags) in one list, in search order."""
    extra_slugs = request.GET.getlist('and')
    if 'page' in request.GET:
        # The list used to be paginated; send old ?page=N links to the single list.
        query = f"?{urlencode([('and', slug) for slug in extra_slugs])}" if extra_slugs else ''
        return HttpResponsePermanentRedirect(reverse('tag_pages', args=[tag_slug]) + query)

    try:
        tags = tags_by_slug([tag_slug, *extra_slugs])
    except Tag.DoesNotExist as exc:
        raise Http404(str(exc)) from exc

    ordered = pages_in_search_order(published_pages_with_all_tags(tags))
    return render(request, 'tags/tag_pages.html', {
        'tag': tags[0],
        'tags': tags,
        'is_combination': len(tags) > 1,
        'pages': ordered[:TAG_PAGE_LIST_LIMIT],
        'total_count': len(ordered),
        'is_truncated': len(ordered) > TAG_PAGE_LIST_LIMIT,
        'list_limit': TAG_PAGE_LIST_LIMIT,
    })


def tag_list(request):
    """Topic browser: filter topics by word start, select several to combine."""
    topics = [
        {'name': t.name, 'slug': t.slug, 'count': t.count}
        for t in tag_counts()
    ]
    return render(request, 'tags/tag_list.html', {
        'topics': topics,
        'initial_rows': INITIAL_TOPIC_ROWS,
        'list_limit': TAG_PAGE_LIST_LIMIT,
    })


def tag_counts_api(request):
    """Counts of pages per topic among pages that have every topic in ?tags=a,b."""
    slugs = [slug for slug in request.GET.get('tags', '').split(',') if slug]
    if not slugs:
        return JsonResponse({'error': 'tags parameter required'}, status=400)
    try:
        selected = tags_by_slug(slugs)
    except Tag.DoesNotExist as exc:
        return JsonResponse({'error': str(exc)}, status=404)
    total = published_pages_with_all_tags(selected).count()
    return JsonResponse({
        'total': total,
        'counts': {t.slug: t.count for t in tag_counts(selected)},
    })
