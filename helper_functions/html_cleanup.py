"""Repairs for HTML left malformed by the Tiki -> markdown migration.

Applied when a page is saved (render_markdown) and again when stored HTML is displayed
(pages.views.prepare_content_html); both functions are idempotent.
"""

from __future__ import annotations

import re

_DIV_TAG_RE = re.compile(r'<(/?)div\b[^>]*>', re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r'<pre><code>(.*?)</code></pre>', re.DOTALL)
# Escaped HTML tags inside code blocks, e.g. &lt;a href="/pages/x/"&gt; ... &lt;/a&gt;
_ESCAPED_TAG_RE = re.compile(
    r'&lt;(/?(?:a|img|span|strong|b|i|em|u|br|font|div|p|sup|sub|table|tr|td|th|ul|ol|li)\b.*?)&gt;',
    re.IGNORECASE | re.DOTALL,
)


def balance_divs(html: str) -> str:
    """Drop closing </div> tags that have no matching opening tag, and close any left open.

    A stray </div> in page content closes the site's own content wrapper early, so the
    rest of the page loses its styling (e.g. tables without borders).
    """
    if '<div' not in html.lower() and '</div' not in html.lower():
        return html
    depth = 0
    out = []
    pos = 0
    for tag in _DIV_TAG_RE.finditer(html):
        if tag.group(1):
            if depth == 0:
                out.append(html[pos:tag.start()])  # stray closing tag: drop it
                pos = tag.end()
                continue
            depth -= 1
        else:
            depth += 1
    out.append(html[pos:])
    return ''.join(out) + '</div>' * depth


def unwrap_legacy_code_blocks(html: str) -> str:
    """Show code blocks as normal text.

    The site has no real code; its code blocks come from lines indented by 4+ spaces in
    migrated content, so they showed prose, numbered lists or even raw HTML
    (<a href=...>) in a grey monospace box. Escaped HTML tags are restored, line breaks
    kept, and empty blocks removed.
    """
    if '<pre><code>' not in html:
        return html

    def unwrap(match: re.Match[str]) -> str:
        text = match.group(1)
        if not text.strip():
            return ''
        text = _ESCAPED_TAG_RE.sub(lambda m: '<' + m.group(1).replace('&quot;', '"') + '>', text)
        lines = [line.strip() for line in text.strip('\n').split('\n')]
        return '<p class="legacy-text">' + '<br>\n'.join(line for line in lines) + '</p>'

    return _CODE_BLOCK_RE.sub(unwrap, html)


_EMPTY_THEAD_RE = re.compile(r'<thead>\s*<tr>(?:\s*<th[^>]*>\s*</th>)+\s*</tr>\s*</thead>\s*', re.IGNORECASE)


def remove_empty_table_headers(html: str) -> str:
    """Drop header rows whose cells are all empty.

    Markdown tables require a header row, so tables without real headings (many migrated
    ones, and Tiki tables whose first row is data) get "| | |" headers that otherwise show
    as a blank strip above the table.
    """
    if '<thead>' not in html:
        return html
    return _EMPTY_THEAD_RE.sub('', html)
