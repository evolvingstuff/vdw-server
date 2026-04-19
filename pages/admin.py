import re
from itertools import islice
from urllib.parse import parse_qsl, urljoin, urlparse

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.widgets import FilteredSelectMultiple
from django import forms
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html, escape
from django.utils.text import slugify, unescape_string_literal
from django.template.response import TemplateResponse
from django.utils import timezone

from core.admin_filters import DateRangeFieldListFilter
from .models import Page
from tags.models import Tag


def _parse_tag_names(raw: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[\n,]", raw or "")]
    return [part for part in parts if part]


def _create_tag_with_unique_slug(*, name: str) -> Tag:
    name = " ".join((name or "").split()).strip()
    assert name, "Tag name required"

    existing = Tag.objects.filter(name=name).first()
    if existing:
        return existing

    base_slug = slugify(name)
    assert base_slug, f"Unable to slugify tag name: {name!r}"

    slug = base_slug
    counter = 2
    while Tag.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    return Tag.objects.create(name=name, slug=slug)


def _normalize_admin_search_phrase(raw_phrase: str) -> str:
    assert isinstance(raw_phrase, str), f"raw_phrase must be str, got {type(raw_phrase)}"

    phrase = raw_phrase.strip()
    if not phrase:
        return ''

    is_quoted = len(phrase) >= 2 and phrase[0] == phrase[-1] and phrase[0] in {'"', "'"}
    if is_quoted:
        phrase = unescape_string_literal(phrase)

    return slugify(phrase)


def _normalized_admin_search_phrases(raw_phrase: str) -> tuple[str, ...]:
    assert isinstance(raw_phrase, str), f"raw_phrase must be str, got {type(raw_phrase)}"

    phrase = raw_phrase.strip()
    if not phrase:
        return tuple()

    is_quoted = len(phrase) >= 2 and phrase[0] == phrase[-1] and phrase[0] in {'"', "'"}
    if is_quoted:
        phrase = unescape_string_literal(phrase)

    candidates = [phrase]
    parsed_source = phrase
    if '://' not in parsed_source and (
        parsed_source.startswith('/')
        or parsed_source.startswith('pages/')
        or '?' in parsed_source
    ):
        parsed_source = f'/{parsed_source.lstrip("/")}'

    parsed_phrase = urlparse(parsed_source)
    if parsed_phrase.path or parsed_phrase.query:
        normalized_path = parsed_phrase.path.strip('/')
        if normalized_path:
            candidates.append(normalized_path)

            if normalized_path.startswith('pages/'):
                _, _, remainder = normalized_path.partition('/')
                if remainder:
                    candidates.append(remainder)

            if '/' in normalized_path:
                candidates.append(normalized_path.rsplit('/', 1)[-1])

        for key, value in parse_qsl(parsed_phrase.query, keep_blank_values=False):
            if key in {'page', 'page_id', 'slug', 'title', 'q'}:
                candidates.append(value)

    normalized_phrases = []
    seen = set()
    for candidate in candidates:
        normalized_candidate = _normalize_admin_search_phrase(candidate)
        if not normalized_candidate or normalized_candidate in seen:
            continue
        seen.add(normalized_candidate)
        normalized_phrases.append(normalized_candidate)

    return tuple(normalized_phrases)


def _value_matches_admin_search_phrase(value: str, normalized_phrases: tuple[str, ...]) -> bool:
    assert isinstance(value, str), f"value must be str, got {type(value)}"
    assert isinstance(normalized_phrases, tuple), f"normalized_phrases must be tuple, got {type(normalized_phrases)}"

    value_slug = slugify(value)
    if not value_slug:
        return False

    value_haystack = f"-{value_slug}-"
    return any(f"-{normalized_phrase}" in value_haystack for normalized_phrase in normalized_phrases)


def _page_matches_admin_search_phrase(title: str, page_slug: str, raw_phrase: str) -> bool:
    assert isinstance(title, str), f"title must be str, got {type(title)}"
    assert isinstance(page_slug, str), f"page_slug must be str, got {type(page_slug)}"
    assert isinstance(raw_phrase, str), f"raw_phrase must be str, got {type(raw_phrase)}"

    normalized_phrases = _normalized_admin_search_phrases(raw_phrase)
    if not normalized_phrases:
        return False

    if _value_matches_admin_search_phrase(title, normalized_phrases):
        return True

    return _value_matches_admin_search_phrase(page_slug, normalized_phrases)


def _is_changelist_request(request) -> bool:
    resolver_match = getattr(request, 'resolver_match', None)
    if resolver_match is None:
        return False

    url_name = getattr(resolver_match, 'url_name', '') or ''
    return url_name.endswith('_changelist')


def _batched(iterable, batch_size: int):
    assert batch_size > 0, "batch_size must be positive"

    iterator = iter(iterable)
    while batch := list(islice(iterator, batch_size)):
        yield batch


PAGE_CHANGELIST_DEFERRED_FIELDS = (
    'content_md',
    'content_html',
    'content_text',
    'meta_description',
    'notes',
    'aliases',
    'front_matter',
    'original_tiki',
)

BULK_TAG_EXCLUDED_IDS_FIELD = "_bulk_tag_excluded_page_ids"


def _parse_bulk_action_page_ids(raw_values: list[str]) -> list[int]:
    page_ids: list[int] = []
    seen_page_ids: set[int] = set()

    for raw_value in raw_values:
        for token in raw_value.split(","):
            normalized_token = token.strip()
            if not normalized_token:
                continue

            page_id = int(normalized_token)
            if page_id in seen_page_ids:
                continue

            seen_page_ids.add(page_id)
            page_ids.append(page_id)

    return page_ids


class BulkTagPagesActionForm(forms.Form):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=FilteredSelectMultiple("Tags", is_stacked=False),
        help_text="Select existing tags to add.",
    )
    new_tags = forms.CharField(
        required=False,
        label="Create new tags",
        help_text="Comma- or newline-separated tag names.",
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
    )

    def clean_new_tags(self) -> list[str]:
        return _parse_tag_names(self.cleaned_data.get("new_tags", ""))

    def clean(self):
        cleaned = super().clean()

        has_existing = bool(cleaned.get("tags"))
        has_new = bool(cleaned.get("new_tags"))
        if not has_existing and not has_new:
            raise forms.ValidationError(
                "Select at least one existing tag or enter a new tag name."
            )

        return cleaned


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
    BULK_TAG_PAGE_BATCH_SIZE = 250
    BULK_TAG_PREVIEW_LIMIT = 50

    form = PageAdminForm
    list_display = ['markdown_link_shortcut', 'html_link_shortcut', 'title', 'status_link', 'chars_display', 'created_date_display', 'modified_date_display']
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
    readonly_fields = ['live_link', 'markdown_link_helper', 'html_link_helper', 'tiki_markdown_comparison']
    actions = ['add_tags_to_selected']
    list_per_page = 25
    show_full_result_count = False
    
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if _is_changelist_request(request):
            return queryset.defer(*PAGE_CHANGELIST_DEFERRED_FIELDS)
        return queryset

    def get_search_results(self, request, queryset, search_term):
        trimmed_search_term = search_term.strip()
        if not trimmed_search_term:
            return queryset, False

        matching_page_ids = [
            page_id
            for page_id, title, page_slug in queryset.values_list('pk', 'title', 'slug')
            if _page_matches_admin_search_phrase(title, page_slug, trimmed_search_term)
        ]

        if not matching_page_ids:
            return queryset.none(), False

        return queryset.filter(pk__in=matching_page_ids), False

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
        return timezone.localtime(obj.created_date).strftime('%Y/%m/%d')
    created_date_display.short_description = "Created"
    created_date_display.admin_order_field = 'created_date'

    def modified_date_display(self, obj):
        return timezone.localtime(obj.modified_date).strftime('%Y/%m/%d')
    modified_date_display.short_description = "Modified"
    modified_date_display.admin_order_field = 'modified_date'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

    def _bulk_add_tags_to_pages(self, queryset, tag_ids: list[int]) -> None:
        assert tag_ids, "tag_ids must not be empty"

        tags_through = Page.tags.through
        derived_through = Page.derived_tags.through
        page_id_iterator = queryset.values_list("pk", flat=True).iterator(
            chunk_size=self.BULK_TAG_PAGE_BATCH_SIZE
        )

        with transaction.atomic():
            for page_id_batch in _batched(page_id_iterator, self.BULK_TAG_PAGE_BATCH_SIZE):
                tags_links = [
                    tags_through(page_id=page_id, tag_id=tag_id)
                    for page_id in page_id_batch
                    for tag_id in tag_ids
                ]
                derived_links = [
                    derived_through(page_id=page_id, tag_id=tag_id)
                    for page_id in page_id_batch
                    for tag_id in tag_ids
                ]

                tags_through.objects.bulk_create(tags_links, ignore_conflicts=True)
                derived_through.objects.bulk_create(derived_links, ignore_conflicts=True)

    def _get_bulk_tag_selection_context(self, queryset, *, select_across: bool) -> dict:
        ordered_queryset = queryset.order_by("pk")
        selected_page_count = queryset.count()
        selected_pages_preview = list(
            ordered_queryset.values_list("title", flat=True)[: self.BULK_TAG_PREVIEW_LIMIT]
        )
        if select_across:
            # Preserve one checkbox value for the normal select-across
            # confirmation roundtrip when a selected page still exists.
            selected_page_ids = list(ordered_queryset.values_list("pk", flat=True)[:1])
        else:
            selected_page_ids = list(queryset.values_list("pk", flat=True))

        return {
            "selected_page_count": selected_page_count,
            "selected_page_ids": selected_page_ids,
            "selected_pages_preview": selected_pages_preview,
        }

    def _get_bulk_tag_excluded_page_ids(self, request) -> list[int]:
        return _parse_bulk_action_page_ids(
            request.POST.getlist(BULK_TAG_EXCLUDED_IDS_FIELD)
        )

    def add_tags_to_selected(self, request, queryset):
        select_across = request.POST.get("select_across") == "1"
        excluded_page_ids = self._get_bulk_tag_excluded_page_ids(request)

        if excluded_page_ids:
            queryset = queryset.exclude(pk__in=excluded_page_ids)

        if request.POST.get("apply"):
            form = BulkTagPagesActionForm(request.POST)
            if form.is_valid():
                existing_tags = list(form.cleaned_data["tags"])
                new_tag_names = list(form.cleaned_data["new_tags"])

                created_tags = [_create_tag_with_unique_slug(name=name) for name in new_tag_names]
                all_tags = [*existing_tags, *created_tags]

                page_count = queryset.count()
                tag_ids = [tag.pk for tag in all_tags]
                assert all(tag_ids), "All tags must be saved before bulk-add"

                self._bulk_add_tags_to_pages(queryset, tag_ids)

                self.message_user(
                    request,
                    f"Added {len(tag_ids)} tag(s) to {page_count} page(s).",
                    messages.SUCCESS,
                )
                return None
        else:
            form = BulkTagPagesActionForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Add tags to selected pages",
            "form": form,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "action_name": "add_tags_to_selected",
            "select_across": "1" if select_across else "",
            "bulk_tag_excluded_ids_field": BULK_TAG_EXCLUDED_IDS_FIELD,
            "excluded_page_ids": excluded_page_ids,
            "excluded_page_count": len(excluded_page_ids),
        }
        context.update(self._get_bulk_tag_selection_context(queryset, select_across=select_across))

        return TemplateResponse(request, "admin/posts/page/add_tags.html", context)

    add_tags_to_selected.short_description = "Add tag(s) to selected pages"

    class Media:
        js = (
            'pages/admin/form_edit_guard.js',
            'pages/admin/copy_page_link.js',
            'pages/admin/title_length_warning.js',
        )
