import os
import re
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils.text import slugify
from .models import Post
import markdown2
import json


def post_list(request):
    posts = Post.objects.filter(status='published').order_by('-created_date')
    return render(request, 'posts/post_list.html', {'posts': posts})


def post_detail(request, slug):
    post = get_object_or_404(Post, slug=slug, status='published')
    
    # Add icons to the HTML content before displaying
    post.content_html = add_file_icons_to_html(post.content_html)
    
    return render(request, 'posts/post_detail.html', {'post': post})


def add_file_icons_to_html(html):
    """Add emoji icons to file links based on file extension"""
    
    # Map file extensions to emoji icons
    icon_map = {
        # Documents
        'pdf': '📄',
        'doc': '📝',
        'docx': '📝',
        'txt': '📃',
        'rtf': '📝',
        # Spreadsheets
        'xls': '📊',
        'xlsx': '📊',
        'csv': '📊',
        # Presentations  
        'ppt': '📊',
        'pptx': '📊',
        # Code/Data
        'json': '📋',
        'xml': '📋',
        'html': '🌐',
        'css': '🎨',
        'js': '💻',
        'py': '🐍',
        # Archives
        'zip': '📦',
        'rar': '📦',
        'tar': '📦',
        'gz': '📦',
        '7z': '📦',
        # Media
        'mp4': '🎬',
        'mov': '🎬',
        'avi': '🎬',
        'mkv': '🎬',
        'mp3': '🎵',
        'wav': '🎵',
        'm4a': '🎵',
        'flac': '🎵',
        # Default - no icon for unknown types
        'default': ''
    }
    
    # Pattern to match links: <a href="url">text</a>
    pattern = r'<a href="([^"]+)">([^<]+)</a>'
    
    def add_icon(match):
        url = match.group(1)
        text = match.group(2)
        
        # Only process attachment links
        if '/attachments/' not in url:
            return match.group(0)
        
        # Extract extension from URL
        ext = ''
        if '.' in url:
            ext = url.split('.')[-1].lower().split('?')[0]
        
        if ext in icon_map:
            icon = icon_map[ext]
        else:
            icon = icon_map['default']
        return f'<a href="{url}">{icon} {text}</a>'
    
    return re.sub(pattern, add_icon, html)


@staff_member_required
def preview_markdown(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        markdown_text = data['markdown']  # Will crash if missing - good!
        
        # Use same markdown settings as the model
        html = markdown2.markdown(
            markdown_text,
            extras=['fenced-code-blocks', 'tables', 'strike', 'footnotes']
        )
        
        # Add file type icons to attachment links
        html = add_file_icons_to_html(html)
        
        return JsonResponse({'html': html})
    
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@csrf_exempt
@require_http_methods(["POST"])
@staff_member_required
def upload_media(request):
    """Handle drag-and-drop media uploads to S3"""
    
    if 'file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)
    
    uploaded_file = request.FILES['file']
    
    # Validate file size (10MB limit)
    if uploaded_file.size > 10 * 1024 * 1024:
        return JsonResponse({'success': False, 'error': 'File too large (max 10MB)'}, status=400)
    
    # Validate and map file type to folder
    content_type_map = {
        # Images
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/gif': 'gif',
        'image/webp': 'webp',
        'image/svg+xml': 'svg',
        'image/bmp': 'bmp',
        # Documents
        'application/pdf': 'pdf',
        'application/msword': 'doc',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
        'application/vnd.ms-excel': 'xls',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
        'application/vnd.ms-powerpoint': 'ppt',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
        # Text
        'text/plain': 'txt',
        'text/csv': 'csv',
        'text/html': 'html',
        'text/css': 'css',
        'application/json': 'json',
        'application/xml': 'xml',
        # Archives
        'application/zip': 'zip',
        'application/x-rar-compressed': 'rar',
        'application/x-tar': 'tar',
        'application/gzip': 'gz',
        # Media
        'video/mp4': 'mp4',
        'video/quicktime': 'mov',
        'video/x-msvideo': 'avi',
        'audio/mpeg': 'mp3',
        'audio/wav': 'wav',
        'audio/x-m4a': 'm4a',
    }
    
    if uploaded_file.content_type not in content_type_map:
        return JsonResponse({'success': False, 'error': f'Invalid file type: {uploaded_file.content_type}'}, status=400)
    
    folder = content_type_map[uploaded_file.content_type]
    
    # Generate filename - use original name if available, otherwise timestamp
    import uuid
    from datetime import datetime
    original_name = uploaded_file.name
    name_part, file_ext = os.path.splitext(original_name)
    
    # Slugify the filename
    slug_name = slugify(name_part)
    if not slug_name:  # If slugify returns empty (e.g., for clipboard pastes)
        # Use appropriate prefix based on file type
        if uploaded_file.content_type.startswith('image/'):
            slug_name = f"image-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        else:
            slug_name = f"file-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    # Build S3 path with proper structure
    base_path = f"public/attachments/{folder}/{slug_name}{file_ext.lower()}"
    
    # Handle collisions
    final_path = base_path
    counter = 1
    while default_storage.exists(final_path):
        final_path = f"public/attachments/{folder}/{slug_name}-{counter}{file_ext.lower()}"
        counter += 1
    
    # Verify we're using S3 storage (S3Storage or S3Boto3Storage are both valid)
    storage_class = default_storage.__class__.__name__
    if 'S3' not in storage_class:
        raise Exception(f"WRONG STORAGE BACKEND: Using {storage_class} - not an S3 storage backend!")
    
    # Save to S3 via Django's default storage - MUST SUCCEED OR CRASH
    saved_path = default_storage.save(final_path, uploaded_file)
    if saved_path != final_path:
        raise Exception(f"S3 PATH MISMATCH: Requested '{final_path}' but got '{saved_path}'")
    
    # Verify the file actually exists in S3
    if not default_storage.exists(saved_path):
        raise Exception(f"UPLOAD FAILED: File does not exist in S3 after save: {saved_path}")
    
    file_url = default_storage.url(saved_path)
    # Remove 'public/' from the URL since CloudFront adds it automatically
    file_url = file_url.replace('/public/', '/')
    
    return JsonResponse({
        'success': True,
        'url': file_url,
        'filename': uploaded_file.name,
        'size': uploaded_file.size
    })
