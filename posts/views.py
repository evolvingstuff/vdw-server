from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from .models import Post
import markdown2
import json


def post_list(request):
    posts = Post.objects.filter(status='published').order_by('-created_date')
    return render(request, 'posts/post_list.html', {'posts': posts})


def post_detail(request, slug):
    post = get_object_or_404(Post, slug=slug, status='published')
    return render(request, 'posts/post_detail.html', {'post': post})


@staff_member_required
def preview_markdown(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            markdown_text = data.get('markdown', '')
            
            # Use same markdown settings as the model
            html = markdown2.markdown(
                markdown_text,
                extras=['fenced-code-blocks', 'tables', 'strike', 'footnotes']
            )
            
            return JsonResponse({'html': html})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Method not allowed'}, status=405)
