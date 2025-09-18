from django.contrib import admin
from django import forms
from django.urls import reverse
from django.utils.html import format_html
from django.contrib.admin import SimpleListFilter
from .models import Post


class PostAdminForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = '__all__'
        widgets = {
            'content_md': forms.Textarea(attrs={'rows': 25, 'cols': 80}),
        }



class RedactedOnlyFilter(SimpleListFilter):
    title = 'redacted content'
    parameter_name = 'has_redacted'
    
    def lookups(self, request, model_admin):
        return (
            ('yes', 'Redacted only'),
        )
    
    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(redacted_count__gt=0)
        return queryset


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    form = PostAdminForm
    list_display = ['title', 'status', 'chars_display', 'redacted_indicator', 'tags_count', 'live_link', 'created_date_display', 'modified_date_display']
    list_filter = ['status', RedactedOnlyFilter, 'created_date', 'modified_date', 'tags']
    search_fields = ['title', 'content_md', 'meta_description']
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    date_hierarchy = 'created_date'
    readonly_fields = ['live_link', 'tiki_markdown_comparison']
    
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        from django.db.models import Count
        queryset = queryset.annotate(tags_count_annotation=Count('tags'))
        return queryset.prefetch_related('tags')

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            ('Content', {
                'fields': ('title', 'slug', 'status', 'live_link', 'content_md', 'notes')
            }),
        ]
        
        # Insert comparison section right after Content if tiki data exists
        if obj and obj.original_tiki:
            fieldsets.append(
                ('Tiki vs Markdown Comparison', {
                    'fields': ('tiki_markdown_comparison',),
                    'classes': ('collapse',),
                    'description': ''
                })
            )
        
        # Add remaining sections
        fieldsets.extend([
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
        ])
        
        return fieldsets
    
    def tiki_markdown_comparison(self, obj):
        if obj.original_tiki:
            return format_html('''
                <div style="display: flex; gap: 20px;">
                    <div style="flex: 1;">
                        <h4 style="margin: 0 0 10px 0; font-size: 13px; font-weight: bold;">Original Tiki</h4>
                        <textarea readonly rows="25" cols="90" style="width: 100%; font-family: monospace; background: #f5f5f5; border: 1px solid #ddd; padding: 8px; box-sizing: border-box; resize: vertical;">{}</textarea>
                    </div>
                    <div style="flex: 1;">
                        <h4 style="margin: 0 0 10px 0; font-size: 13px; font-weight: bold;">Converted Markdown</h4>
                        <textarea readonly rows="25" cols="90" style="width: 100%; font-family: monospace; background: #f5f5f5; border: 1px solid #ddd; padding: 8px; box-sizing: border-box; resize: vertical;">{}</textarea>
                    </div>
                </div>
            ''', obj.original_tiki, obj.content_md)
        return "No original Tiki data available"
    tiki_markdown_comparison.short_description = ""
    
    def live_link(self, obj):
        if obj.pk and obj.status == 'published':
            url = reverse('post_detail', args=[obj.slug])
            return format_html('<a href="{}" target="_blank">View Live →</a>', url)
        elif obj.pk and obj.status == 'draft':
            return "Publish to view live"
        else:
            return "Save first"
    live_link.short_description = "Live URL"
    
    def redacted_indicator(self, obj):
        if obj.redacted_count > 0:
            return format_html(
                '<span style="color: #f66; font-weight: bold;" title="{} censored sections">⚠️ {}</span>',
                obj.redacted_count, obj.redacted_count
            )
        return format_html('<span style="color: #ccc;">—</span>')
    redacted_indicator.short_description = "Redacted"
    redacted_indicator.admin_order_field = 'redacted_count'

    def chars_display(self, obj):
        return obj.character_count
    chars_display.short_description = "Chars"
    chars_display.admin_order_field = 'character_count'

    def created_date_display(self, obj):
        return obj.created_date.strftime('%B %d, %Y')
    created_date_display.short_description = "Created Date"
    created_date_display.admin_order_field = 'created_date'

    def modified_date_display(self, obj):
        return obj.modified_date.strftime('%B %d, %Y')
    modified_date_display.short_description = "Modified Date"
    modified_date_display.admin_order_field = 'modified_date'

    def tags_count(self, obj):
        # Use the annotated count if available, otherwise fall back to counting
        count = getattr(obj, 'tags_count_annotation', None) or obj.tags.count()
        if count > 0:
            return format_html('<span style="font-weight: bold;">{}</span>', count)
        return format_html('<span style="color: #ccc;">—</span>')
    tags_count.short_description = "Tags"
    tags_count.admin_order_field = 'tags_count_annotation'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

