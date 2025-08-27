from django.contrib import admin
from django import forms
from django.urls import reverse
from django.utils.html import format_html
from .models import Post, Tag


class PostAdminForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = '__all__'
        widgets = {
            'content_md': forms.Textarea(attrs={'rows': 25, 'cols': 80}),
        }


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name']


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    form = PostAdminForm
    list_display = ['title', 'status', 'live_link', 'created_date', 'modified_date']
    list_filter = ['status', 'created_date', 'modified_date', 'tags']
    search_fields = ['title', 'content_md', 'meta_description']
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    date_hierarchy = 'created_date'
    readonly_fields = ['live_link']
    
    fieldsets = (
        ('Content', {
            'fields': ('title', 'slug', 'content_md', 'notes', 'status', 'live_link')
        }),
        ('Tags', {
            'fields': ('tags',)
        }),
        ('SEO', {
            'fields': ('meta_description',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_date',),
            'classes': ('collapse',)
        }),
    )
    
    def live_link(self, obj):
        if obj.pk and obj.status == 'published':
            url = reverse('post_detail', args=[obj.slug])
            return format_html('<a href="{}" target="_blank">View Live →</a>', url)
        elif obj.pk and obj.status == 'draft':
            return "Publish to view live"
        else:
            return "Save first"
    live_link.short_description = "Live URL"
    
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
