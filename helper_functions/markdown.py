"""Markdown rendering helpers with preserved footnote numbers."""

import re
from typing import Iterable

import markdown2

from helper_functions.html_cleanup import balance_divs, remove_empty_table_headers, unwrap_legacy_code_blocks
from helper_functions.tables import prepare_tables_for_rendering


BULLET_NUMBER_LITERAL_RE = re.compile(
    r"(?m)^(?P<prefix>\s*[*+-]\s+)(?P<number>\d+)\.(?=\s|$)"
)


DEFAULT_MARKDOWN_EXTRAS: Iterable[str] = (
    'fenced-code-blocks',
    'tables',
    'strike',
    'footnotes',
    'markdown-in-html',
)

FOOTNOTE_REF_RE = re.compile(
    r'<sup class="footnote-ref" id="fnref-(?P<label>\d+)">'  # noqa: E501
    r'<a href="#fn-\d+"(?P<attrs>[^>]*)>(?P<number>\d+)</a>'
    r'</sup>'
)

FOOTNOTE_BLOCK_RE = re.compile(
    r'(<div class="footnotes">\s*<hr />\s*<ol>)'
    r'(?P<body>.*?)'
    r'(</ol>\s*</div>)',
    re.DOTALL,
)

FOOTNOTE_LI_RE = re.compile(
    r'<li id="fn-(?P<label>\d+)"(?P<attrs>[^>]*)>(?P<body>.*?)</li>',
    re.DOTALL,
)

BACKLINK_TITLE_RE = re.compile(
    r'(title="Jump back to footnote )\d+( in the text\.)"'
)

CONSECUTIVE_FOOTNOTE_REFS_RE = re.compile(
    r'</sup>(?=<sup class="footnote-ref")'
)

FOOTNOTE_BACKLINK_RE = re.compile(
    r'(?:\s|&#160;)*<a[^>]*class="[^"]*footnoteBackLink[^"]*"[^>]*>.*?</a>',
    re.DOTALL,
)

URL_RE = re.compile(r'https?://[^\s<]+')


def render_markdown(markdown_text: str) -> str:
    """Render Markdown with defaults and preserve original footnote numbers."""
    # Make leftover Tiki tables and tables inside HTML boxes render (see helper_functions/tables.py).
    markdown_text = prepare_tables_for_rendering(markdown_text)
    markdown_text = _escape_literal_ordered_markers(markdown_text)
    html = markdown2.markdown(markdown_text, extras=DEFAULT_MARKDOWN_EXTRAS)

    if '[^' in markdown_text:
        html = _restore_inline_footnote_numbers(html)
        html = _restore_definition_numbers(html)
        html = _space_consecutive_references(html)
    html = linkify_bare_doi_urls(fix_doi_link_periods(demote_headings(html)))
    return remove_empty_table_headers(balance_divs(unwrap_legacy_code_blocks(html)))


_HEADING_OPEN_RE = re.compile(r'<h([1-6])(\s[^>]*)?>', re.IGNORECASE)
_HEADING_CLOSE_RE = re.compile(r'</h([1-6])\s*>', re.IGNORECASE)
_HAS_H1_RE = re.compile(r'<h1[\s>]', re.IGNORECASE)
_IMG_RE = re.compile(r'<img\b(?![^>]*\bloading=)', re.IGNORECASE)


def demote_headings(html: str) -> str:
    """Shift content headings down one level so the page title is the only <h1>.

    Each shifted heading gets an `md-hN` class (N = original level) so it keeps its
    original size. Only HTML that still contains an <h1> is changed, which makes this
    safe to apply both when saving and again when displaying older stored HTML.
    """
    if not _HAS_H1_RE.search(html):
        return html

    def open_tag(match: re.Match[str]) -> str:
        level = int(match.group(1))
        attrs = match.group(2) or ''
        css_class = f'md-h{level}'
        if re.search(r'\bclass="', attrs):
            attrs = re.sub(r'\bclass="', f'class="{css_class} ', attrs, count=1)
        else:
            attrs = f' class="{css_class}"{attrs}'
        return f'<h{min(level + 1, 6)}{attrs}>'

    html = _HEADING_OPEN_RE.sub(open_tag, html)
    return _HEADING_CLOSE_RE.sub(lambda m: f'</h{min(int(m.group(1)) + 1, 6)}>', html)


_DOI_LINK_TRAILING_PERIOD_RE = re.compile(
    r'(?P<open><a\b[^>]*?\bhref=")(?P<url>https?://(?:dx\.)?doi\.org/[^"]*?)\.+(?P<rest>"[^>]*>)'
    r'(?P<text>(?:(?!<a\b).)*?)(?P<close></a>)',  # text stops at nested <a> (malformed content)
    re.IGNORECASE | re.DOTALL,
)
_DOI_HREF_TRAILING_PERIOD_RE = re.compile(
    r'(\bhref="https?://(?:dx\.)?doi\.org/[^"]*?)\.+"', re.IGNORECASE
)


def fix_doi_link_periods(html: str) -> str:
    """Drop the sentence period that got pulled into DOI links ("…/MD.0000000000010702.").

    Real DOIs don't end in "."; with it, doi.org can't resolve the link. The period is
    removed from the URL and moved outside the link text, so the sentence still ends with
    one. Applied when saving and again when displaying older stored HTML (idempotent).
    """
    def fix(match: re.Match[str]) -> str:
        text = match.group('text')
        trailing = ''
        if text.endswith('.'):
            text, trailing = text[:-1], '.'
        return f"{match.group('open')}{match.group('url')}{match.group('rest')}{text}{match.group('close')}{trailing}"

    html = _DOI_LINK_TRAILING_PERIOD_RE.sub(fix, html)
    # Any remaining DOI hrefs (e.g. links nested inside other links) just lose the period.
    return _DOI_HREF_TRAILING_PERIOD_RE.sub(r'\1"', html)


_HTML_TAG_SPLIT_RE = re.compile(r'(<[^>]+>)')
_TAG_NAME_RE = re.compile(r'<\s*(/?)\s*([a-zA-Z0-9]+)')
_BARE_DOI_URL_RE = re.compile(r'https?://(?:dx\.)?doi\.org/[^\s<>"\']+', re.IGNORECASE)
_NO_LINKIFY_TAGS = {'a', 'code', 'pre', 'script', 'style', 'textarea'}


def _split_doi_url(url: str) -> tuple[str, str]:
    """Split trailing sentence punctuation off a DOI URL; keep parentheses that belong to it."""
    trailing = ''
    while url:
        last = url[-1]
        if last in '.,;:!?\'"]':
            pass
        elif last == ')' and url.count('(') < url.count(')'):
            pass
        else:
            break
        trailing = last + trailing
        url = url[:-1]
    return url, trailing


def linkify_bare_doi_urls(html: str) -> str:
    """Turn plain-text https://doi.org/... addresses into links (markdown doesn't auto-link).

    Text already inside a link, code block or script is left alone, so this is safe to run
    on stored HTML at display time and again when saving (idempotent).
    """
    if 'doi.org/' not in html.lower():
        return html
    parts = _HTML_TAG_SPLIT_RE.split(html)
    skip_depth = 0
    for i, part in enumerate(parts):
        if part.startswith('<'):
            tag = _TAG_NAME_RE.match(part)
            if tag and tag.group(2).lower() in _NO_LINKIFY_TAGS and not part.endswith('/>'):
                skip_depth = max(0, skip_depth + (-1 if tag.group(1) else 1))
            continue
        if skip_depth or 'doi.org/' not in part.lower():
            continue

        def link(match: re.Match[str]) -> str:
            url, trailing = _split_doi_url(match.group(0))
            if len(url) <= len('https://doi.org/'):
                return match.group(0)
            return f'<a href="{url}">{url}</a>{trailing}'

        parts[i] = _BARE_DOI_URL_RE.sub(link, part)
    return ''.join(parts)


def add_lazy_loading(html: str) -> str:
    """Lazy-load every image except the first, which is usually visible on load."""
    count = 0

    def add(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return match.group(0) if count == 1 else '<img loading="lazy"'

    return _IMG_RE.sub(add, html)


def _restore_inline_footnote_numbers(html: str) -> str:
    """Replace sequential footnote reference numbers with their original labels."""

    def replace(match: re.Match[str]) -> str:
        label = match.group('label')
        attrs = match.group('attrs')
        return (
            f'<sup class="footnote-ref" id="fnref-{label}">'  # noqa: E501
            f'<a href="#fn-{label}"{attrs}>{label}</a>'
            '</sup>'
        )

    return FOOTNOTE_REF_RE.sub(replace, html)


def _restore_definition_numbers(html: str) -> str:
    """Ensure footnote definitions keep original numbers and remove duplicates."""
    match = FOOTNOTE_BLOCK_RE.search(html)
    if not match:
        return html

    body = match.group('body')
    seen_labels: set[str] = set()
    rebuilt_items: list[str] = []

    for li_match in FOOTNOTE_LI_RE.finditer(body):
        label = li_match.group('label')
        if label in seen_labels:
            continue
        seen_labels.add(label)
        attrs = li_match.group('attrs')
        # Remove existing value attribute to avoid duplication
        attrs = re.sub(r'\svalue="\d+"', '', attrs)
        li_body = li_match.group('body')
        li_body = BACKLINK_TITLE_RE.sub(
            lambda m: f'{m.group(1)}{label}{m.group(2)}"',
            li_body,
        )
        li_body = _remove_backlinks(li_body)
        li_body = _autolink_urls(li_body)
        rebuilt_items.append(
            f'<li id="fn-{label}"{attrs} value="{label}">{li_body}</li>'
        )

    rebuilt_body = ''.join(rebuilt_items)
    return (
        html[: match.start('body')]
        + rebuilt_body
        + html[match.end('body') :]
    )


def _space_consecutive_references(html: str) -> str:
    """Ensure consecutive footnote references are visually separated."""
    return CONSECUTIVE_FOOTNOTE_REFS_RE.sub('</sup>&nbsp;', html)


def _remove_backlinks(fragment: str) -> str:
    """Strip footnote backlink anchors from a definition fragment."""
    return FOOTNOTE_BACKLINK_RE.sub('', fragment)


def _autolink_urls(fragment: str) -> str:
    """Wrap bare HTTP(S) URLs in anchor tags without touching existing links."""

    def should_link(prefix: str) -> bool:
        last_lt = prefix.rfind('<')
        last_gt = prefix.rfind('>')
        if last_lt > last_gt:
            # Inside an HTML tag/attribute
            return False
        last_open_anchor = prefix.rfind('<a')
        if last_open_anchor != -1:
            last_close_anchor = prefix.rfind('</a>')
            if last_close_anchor < last_open_anchor:
                return False
        return True

    result: list[str] = []
    last_end = 0
    for match in URL_RE.finditer(fragment):
        start, end = match.span()
        url = match.group(0)
        result.append(fragment[last_end:start])
        if should_link(fragment[:start]):
            result.append(f'<a href="{url}">{url}</a>')
        else:
            result.append(url)
        last_end = end

    result.append(fragment[last_end:])
    return ''.join(result)


def _escape_literal_ordered_markers(markdown_text: str) -> str:
    """Prevent numeric bullets like "* 123." from becoming nested ordered lists."""

    def replace(match: re.Match[str]) -> str:
        prefix = match.group('prefix')
        number = match.group('number')
        return f'{prefix}{number}' + r'\.'

    return BULLET_NUMBER_LITERAL_RE.sub(replace, markdown_text)


__all__ = ['render_markdown']
