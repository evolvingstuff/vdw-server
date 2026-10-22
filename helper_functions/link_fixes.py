"""Apply reviewed fixes for broken internal links to page markdown.

A fix says: on page P, the link to /pages/<target>/ with text T should point to URL U.
A link is only changed when both its target and its (normalized) text match, so pages
edited since the fixes were generated are left alone rather than guessed at.
"""

from __future__ import annotations

import html
import re
from typing import Dict, Iterable, Tuple

_PAGE_URL = r'(?:https?://(?:www\.)?vitamindwiki\.com)?/pages/(?P<target>[^/"#?)\s]+)/?'
# [text](/pages/target/)  -- text may not contain "]" (nested brackets are not used for these links)
_MD_LINK_RE = re.compile(r'\[(?P<text>[^\]]*)\]\((?P<url>' + _PAGE_URL + r')\)')
# <a href="/pages/target/" ...>text</a>
_HTML_LINK_RE = re.compile(
    r'(?P<open><a\b[^>]*?\bhref=")(?P<url>' + _PAGE_URL + r')(?P<rest>"[^>]*>)(?P<text>.*?)(?P<close></a>)',
    re.IGNORECASE | re.DOTALL,
)


def normalize_link_text(text: str) -> str:
    """Link text as a reader sees it: no tags or markdown emphasis, collapsed whitespace."""
    text = html.unescape(re.sub(r'<[^>]+>', '', text))
    text = re.sub(r'[*_`]+', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()


FixMap = Dict[Tuple[str, str], str]   # (target, normalized text) -> new URL


def build_fix_map(fixes: Iterable[dict]) -> FixMap:
    return {(f['target'], normalize_link_text(f['text'])): f['new_url'] for f in fixes}


def apply_link_fixes(markdown: str, fix_map: FixMap) -> Tuple[str, int]:
    """Return (markdown with matching links repointed, number of links changed)."""
    changed = 0

    def md_link(match: re.Match[str]) -> str:
        nonlocal changed
        new_url = fix_map.get((match.group('target'), normalize_link_text(match.group('text'))))
        if not new_url:
            return match.group(0)
        changed += 1
        return f"[{match.group('text')}]({new_url})"

    def html_link(match: re.Match[str]) -> str:
        nonlocal changed
        new_url = fix_map.get((match.group('target'), normalize_link_text(match.group('text'))))
        if not new_url:
            return match.group(0)
        changed += 1
        return f"{match.group('open')}{new_url}{match.group('rest')}{match.group('text')}{match.group('close')}"

    markdown = _MD_LINK_RE.sub(md_link, markdown)
    markdown = _HTML_LINK_RE.sub(html_link, markdown)
    return markdown, changed
