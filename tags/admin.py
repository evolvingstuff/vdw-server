from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import Tag


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'post_count']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']
    readonly_fields = ['linked_posts']

    fieldsets = (
        ('Tag Info', {
            'fields': ('name', 'slug')
        }),
        ('Linked Posts', {
            'fields': ('linked_posts',),
            'classes': ('collapse',)  # Collapsed by default
        }),
    )

    def post_count(self, obj):
        count = obj.posts.count()
        return f"{count} post{'s' if count != 1 else ''}"
    post_count.short_description = "Posts"

    def linked_posts(self, obj):
        if not obj.pk:
            return "Save tag first to see linked posts"

        posts = obj.posts.all().order_by('-created_date')
        total_count = posts.count()

        if total_count == 0:
            return "No posts tagged with this tag"

        links = []
        for post in posts:
            admin_url = reverse('admin:posts_post_change', args=[post.pk])
            status_icon = "✅" if post.status == 'published' else "📝"
            date_str = post.created_date.strftime('%Y-%m-%d')
            links.append(f'<a href="{admin_url}">{status_icon} {post.title}</a> <small>({date_str})</small>')

        result = f'<strong>Total: {total_count} posts</strong><br><br>' + '<br>'.join(links)
        return format_html(result)
    linked_posts.short_description = "Posts with this tag"
