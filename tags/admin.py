from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'page_count']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']
    readonly_fields = ['linked_pages']

    fieldsets = (
        ('Tag Info', {
            'fields': ('name', 'slug')
        }),
        ('Linked Pages', {
            'fields': ('linked_pages',),
            'classes': ('collapse',)  # Collapsed by default
        }),
    )

    def page_count(self, obj):
        count = obj.pages.count()
        return f"{count} page{'s' if count != 1 else ''}"
    page_count.short_description = "Pages"

    def linked_pages(self, obj):
        if not obj.pk:
            return "Save tag first to see linked pages"

        pages = obj.pages.all().order_by('-created_date')
        total_count = pages.count()

        if total_count == 0:
            return "No pages tagged with this tag"

        links = []
        for page in pages:
            admin_url = reverse('admin:posts_page_change', args=[page.pk])
            status_icon = "✅" if page.status == 'published' else "📝"
            date_str = page.created_date.strftime('%Y-%m-%d')
            links.append(f'<a href="{admin_url}">{status_icon} {page.title}</a> <small>({date_str})</small>')

        result = f'<strong>Total: {total_count} pages</strong><br><br>' + '<br>'.join(links)
        return format_html(result)
    linked_pages.short_description = "Pages with this tag"
