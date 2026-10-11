from django.http import Http404, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode

from .models import Tag
from .services import TAG_PAGE_LIST_LIMIT, get_topic_index

INITIAL_TOPIC_ROWS = 100


def tag_pages(request, tag_slug):
    """All pages with a tag (plus any extra ?and=<slug> tags) in one list, in search order."""
    extra_slugs = request.GET.getlist('and')
    if 'page' in request.GET:
        # The list used to be paginated; send old ?page=N links to the single list.
        query = f"?{urlencode([('and', slug) for slug in extra_slugs])}" if extra_slugs else ''
        return HttpResponsePermanentRedirect(reverse('tag_pages', args=[tag_slug]) + query)

    index = get_topic_index()
    try:
        tags = index.tags_for_slugs([tag_slug, *extra_slugs])
    except Tag.DoesNotExist as exc:
        raise Http404(str(exc)) from exc

    pages, total = index.pages_for(tags)
    return render(request, 'tags/tag_pages.html', {
        'tag': tags[0],
        'tags': tags,
        'is_combination': len(tags) > 1,
        'pages': pages,
        'total_count': total,
        'is_truncated': total > TAG_PAGE_LIST_LIMIT,
        'list_limit': TAG_PAGE_LIST_LIMIT,
        'selected_slugs': ','.join(t.slug for t in tags),
    })


def tag_list(request):
    """Topic browser: filter topics by word start, select several to combine."""
    topics = [{'name': c.name, 'slug': c.slug, 'count': c.count} for c in get_topic_index().topic_counts]
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
    index = get_topic_index()
    try:
        selected = index.tags_for_slugs(slugs)
    except Tag.DoesNotExist as exc:
        return JsonResponse({'error': str(exc)}, status=404)
    total, counts = index.combined_counts(selected)
    return JsonResponse({'total': total, 'counts': counts})
