from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
import markdown2
import json


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
