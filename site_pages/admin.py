from django.contrib import admin
from django import forms
from django.conf import settings
from django.utils.html import format_html, escape
from urllib.parse import urljoin
from .models import SitePage


class SitePageAdminForm(forms.ModelForm):
    class Meta:
        model = SitePage
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


@admin.register(SitePage)
class SitePageAdmin(admin.ModelAdmin):
    form = SitePageAdminForm
    list_display = ['markdown_link_shortcut', 'html_link_shortcut', 'title', 'page_type', 'slug', 'is_published', 'chars_display', 'modified_date_display']
    list_filter = ['page_type', 'is_published', 'modified_date']
    search_fields = ['title', 'content_md', 'meta_description']

    def get_prepopulated_fields(self, request, obj=None):
        # Don't prepopulate slug for homepage (it's readonly)
        if obj and obj.page_type == 'homepage':
            return {}
        return {'slug': ('title',)}

    def get_fieldsets(self, request, obj=None):
        return [
            ('Page Info', {
                'fields': ('title', 'slug', 'page_type', 'is_published', 'live_link', 'markdown_link_helper', 'html_link_helper')
            }),
            ('Content', {
                'fields': ('content_md',)
            }),
            ('SEO', {
                'fields': ('meta_description',),
                'classes': ('collapse',)
            }),
            ('Statistics', {
                'fields': ('character_count', 'modified_date'),
                'classes': ('collapse',)
            }),
        ]

    def get_readonly_fields(self, request, obj=None):
        readonly = ['live_link', 'markdown_link_helper', 'html_link_helper', 'character_count', 'modified_date']
        # Protect homepage slug and type
        if obj and obj.page_type == 'homepage':
            readonly.extend(['slug', 'page_type'])
        return readonly

    def has_delete_permission(self, request, obj=None):
        # Cannot delete homepage
        if obj and obj.page_type == 'homepage':
            return False
        return super().has_delete_permission(request, obj)

    def live_link(self, obj):
        if obj.pk and obj.is_published:
            url = obj.get_absolute_url()
            return format_html('<a href="{}" target="_blank">View Live →</a>', url)
        return "Not published"
    live_link.short_description = "Live URL"

    def markdown_link_helper(self, obj):
        if not obj or not obj.pk:
            return "Save this page to generate its markdown link."

        path = obj.get_absolute_url()
        markdown_link = f'[{obj.title}]({path})'
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
        if not obj.pk:
            return format_html('<span style="color: #ccc;">—</span>')

        path = obj.get_absolute_url()
        markdown_link = f'[{obj.title}]({path})'
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
        if not obj or not obj.pk:
            return "Save this page to generate its HTML link."

        base_url = (getattr(settings, 'SITE_BASE_URL', '') or '').strip()
        if not base_url:
            raise RuntimeError('SITE_BASE_URL is not configured; cannot generate an absolute HTML link.')

        path = obj.get_absolute_url()
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
        if not obj.pk:
            return format_html('<span style="color: #ccc;">—</span>')

        base_url = (getattr(settings, 'SITE_BASE_URL', '') or '').strip()
        if not base_url:
            raise RuntimeError('SITE_BASE_URL is not configured; cannot generate an absolute HTML link.')

        path = obj.get_absolute_url()
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

    def modified_date_display(self, obj):
        return obj.modified_date.strftime('%B %d, %Y')
    modified_date_display.short_description = "Modified Date"
    modified_date_display.admin_order_field = 'modified_date'

    class Media:
        js = (
            'pages/admin/form_edit_guard.js',
            'pages/admin/copy_page_link.js',
            'pages/admin/title_length_warning.js',
        )
