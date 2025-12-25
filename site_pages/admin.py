from django.contrib import admin
from django import forms
from django.utils.html import format_html
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
    list_display = ['title', 'page_type', 'slug', 'is_published', 'chars_display', 'modified_date_display']
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
                'fields': ('title', 'slug', 'page_type', 'is_published', 'live_link')
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
        readonly = ['live_link', 'character_count', 'modified_date']
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

    def chars_display(self, obj):
        return obj.character_count
    chars_display.short_description = "Chars"
    chars_display.admin_order_field = 'character_count'

    def modified_date_display(self, obj):
        return obj.modified_date.strftime('%B %d, %Y')
    modified_date_display.short_description = "Modified Date"
    modified_date_display.admin_order_field = 'modified_date'

    class Media:
        js = ('pages/admin/form_edit_guard.js',)
