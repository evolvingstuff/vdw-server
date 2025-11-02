from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Count, Q
from pages.models import Page
from .models import Tag


def tag_pages(request, tag_slug):
    """Display all pages with a specific tag"""
    tag = get_object_or_404(Tag, slug=tag_slug)
    pages_list = Page.objects.filter(
        tags=tag,
        status='published'
    ).order_by('-created_date')

    # Add pagination - 20 pages per page (same as All Pages)
    paginator = Paginator(pages_list, 20)
    page_number = request.GET.get('page')
    pages = paginator.get_page(page_number)

    return render(request, 'tags/tag_pages.html', {
        'tag': tag,
        'pages': pages
    })


def tag_list(request):
    """Display all tags with optional published page counts."""
    tags = (
        Tag.objects
        .annotate(pub_count=Count('pages', filter=Q(pages__status='published')))
        .filter(pub_count__gt=0)
    )
    return render(request, 'tags/tag_list.html', {
        'tags': tags,
    })
