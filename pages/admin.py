from django.contrib import admin
from django import forms
from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html, escape
from django.db.models import Count
from urllib.parse import urljoin
from core.admin_filters import DateRangeFieldListFilter
from .models import Page


class PageAdminForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = '__all__'
        widgets = {
            'title': forms.Textarea(attrs={
                'rows': 2,
                'cols': 80,
                'style': 'min-height: 48px; min-width: 320px;',
                'spellcheck': 'true',
            }),
            'content_md': forms.Textarea(attrs={'rows': 25, 'cols': 80}),
        }


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    form = PageAdminForm
    list_display = ['markdown_link_shortcut', 'html_link_shortcut', 'title', 'status_link', 'chars_display', 'tags_count', 'created_date_display', 'modified_date_display']
    list_display_links = ('title',)
    list_filter = [
        'status',
        ('created_date', DateRangeFieldListFilter),
        ('modified_date', DateRangeFieldListFilter),
        'tags',
    ]
    search_fields = ('title',)
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ['tags']
    date_hierarchy = 'created_date'
    readonly_fields = ['live_link', 'markdown_link_helper', 'html_link_helper', 'tiki_markdown_comparison']
    
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.annotate(tags_count_annotation=Count('tags'))
        return queryset.prefetch_related('tags')

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            ('Content', {
                'fields': ('title', 'slug', 'status', 'live_link', 'markdown_link_helper', 'html_link_helper', 'content_md', 'notes')
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
            url = reverse('page_detail', args=[obj.slug])
            return format_html('<a href="{}" target="_blank">View Live →</a>', url)
        elif obj.pk and obj.status == 'draft':
            return "Publish to view live"
        else:
            return "Save first"
    live_link.short_description = "Live URL"

    def status_link(self, obj):
        assert obj.slug, 'Page.slug missing; cannot build status link'

        if obj.status == 'published':
            url = reverse('page_detail', args=[obj.slug])
        else:
            url = reverse('page_preview', args=[obj.slug])

        return format_html('<a href="{}" target="_blank">{}</a>', url, obj.get_status_display())
    status_link.short_description = 'Status'
    status_link.admin_order_field = 'status'

    def markdown_link_helper(self, obj):
        if not obj or not obj.pk or not obj.slug:
            return "Save this page to generate its markdown link."

        url = reverse('page_detail', args=[obj.slug])
        markdown_link = f'[{obj.title}]({url})'
        return format_html(
            '<div class="vdw-copy-markdown-field">'
            '  <code class="vdw-copy-markdown-preview">{}</code>'
            '  <button type="button" class="button vdw-copy-markdown-button" '
            'data-copy-markdown="{}" data-copy-label="Copy Markdown link" data-copy-success="Copied!">'
            'Copy Markdown Link'
            '  </button>'
            '</div>',
            markdown_link,
            markdown_link,
        )
    markdown_link_helper.short_description = "Markdown link"

    def markdown_link_shortcut(self, obj):
        if not obj.pk or not obj.slug:
            return format_html('<span style="color: #ccc;">—</span>')

        url = reverse('page_detail', args=[obj.slug])
        markdown_link = f'[{obj.title}]({url})'
        return format_html(
            '<button type="button" class="vdw-copy-link-icon" data-copy-markdown="{}" '
            'data-copy-label="🔗" data-copy-success="Copied!" aria-label="Copy markdown link for {}" '
            'title="Copy markdown link for {}" style="border: none; background: none; padding: 0 4px; cursor: pointer; font-size: 16px;">🔗</button>',
            markdown_link,
            obj.title,
            obj.title,
        )
    markdown_link_shortcut.short_description = "MD"

    def html_link_helper(self, obj):
        if not obj or not obj.pk or not obj.slug:
            return "Save this page to generate its HTML link."

        path = reverse('page_detail', args=[obj.slug])
        base_url = (getattr(settings, 'SITE_BASE_URL', '') or '').strip()

        if not base_url:
            raise RuntimeError('SITE_BASE_URL is not configured; cannot generate an absolute HTML link.')

        absolute_url = urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
        html_link = f'<a href="{escape(absolute_url)}">{escape(obj.title)}</a>'
        return format_html(
            '<div class="vdw-copy-html-field">'
            '  <code class="vdw-copy-html-preview">{}</code>'
            '  <button type="button" class="button vdw-copy-html-button" '
            'data-copy-html="{}" data-copy-plain="{}" data-copy-label="Copy HTML link" data-copy-success="Copied!">'
            'Copy HTML Link'
            '  </button>'
            '</div>',
            html_link,
            html_link,
            absolute_url,
        )
    html_link_helper.short_description = "HTML link"

    def html_link_shortcut(self, obj):
        if not obj.pk or not obj.slug:
            return format_html('<span style="color: #ccc;">—</span>')

        path = reverse('page_detail', args=[obj.slug])
        base_url = (getattr(settings, 'SITE_BASE_URL', '') or '').strip()

        if not base_url:
            raise RuntimeError('SITE_BASE_URL is not configured; cannot generate an absolute HTML link.')

        absolute_url = urljoin(base_url.rstrip('/') + '/', path.lstrip('/'))
        html_link = f'<a href="{escape(absolute_url)}">{escape(obj.title)}</a>'
        return format_html(
            '<button type="button" class="vdw-copy-link-icon" data-copy-html="{}" data-copy-plain="{}" '
            'data-copy-label="⟨/⟩" data-copy-success="Copied!" aria-label="Copy HTML link for {}" '
            'title="Copy HTML link for {}" style="border: none; background: none; padding: 0 4px; cursor: pointer; font-size: 13px;">⟨/⟩</button>',
            html_link,
            absolute_url,
            obj.title,
            obj.title,
        )
    html_link_shortcut.short_description = "HTML"

    def chars_display(self, obj):
        return obj.character_count
    chars_display.short_description = "Chars"
    chars_display.admin_order_field = 'character_count'

    def created_date_display(self, obj):
        return obj.created_date.strftime('%m/%y')
    created_date_display.short_description = "Created"
    created_date_display.admin_order_field = 'created_date'

    def modified_date_display(self, obj):
        return obj.modified_date.strftime('%m/%y')
    modified_date_display.short_description = "Modified"
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

    class Media:
        js = (
            'pages/admin/form_edit_guard.js',
            'pages/admin/copy_page_link.js',
            'pages/admin/title_length_warning.js',
        )
