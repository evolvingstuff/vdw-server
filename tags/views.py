from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from posts.models import Post, Tag


def tag_posts(request, tag_slug):
    """Display all posts with a specific tag"""
    tag = get_object_or_404(Tag, slug=tag_slug)
    posts_list = Post.objects.filter(
        tags=tag, 
        status='published'
    ).order_by('-created_date')
    
    # Add pagination - 20 posts per page (same as All Posts)
    paginator = Paginator(posts_list, 20)
    page_number = request.GET.get('page')
    posts = paginator.get_page(page_number)
    
    return render(request, 'tags/tag_posts.html', {
        'tag': tag,
        'posts': posts
    })
