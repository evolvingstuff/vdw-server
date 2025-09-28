"""Markdown rendering helpers with preserved footnote numbers."""

import re
from typing import Iterable

import markdown2


BULLET_NUMBER_LITERAL_RE = re.compile(
    r"(?m)^(?P<prefix>\s*[*+-]\s+)(?P<number>\d+)\.(?=\s|$)"
)


DEFAULT_MARKDOWN_EXTRAS: Iterable[str] = (
    'fenced-code-blocks',
    'tables',
    'strike',
    'footnotes',
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
    markdown_text = _escape_literal_ordered_markers(markdown_text)
    html = markdown2.markdown(markdown_text, extras=DEFAULT_MARKDOWN_EXTRAS)

    if '[^' not in markdown_text:
        # Fast path when no footnotes are present
        return html

    html = _restore_inline_footnote_numbers(html)
    html = _restore_definition_numbers(html)
    html = _space_consecutive_references(html)
    return html


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
