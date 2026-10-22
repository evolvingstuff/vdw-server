"""Search query helpers: old Tiki search links and OR queries.

Old Tiki pages linked to searches with texts like
  "Search VitaminDWiki for ICU OR “critical care” OR “intensive care”"
extract_search_term() turns such link text into the query ("ICU OR "critical care" OR ...")
and split_or_query() splits a query on OR so each alternative can be searched separately
(Meilisearch has no OR operator; quoted phrases are supported natively).
"""

from __future__ import annotations

import re
from typing import List

_CURLY_QUOTES = str.maketrans({'“': '"', '”': '"', '„': '"', '″': '"'})

_LEADING_RE = re.compile(
    r'^(?:'
    r'(?:se?a?r?ch|serach|seach)\b\s*(?:for\s+)?(?:vit\w*d\w*wiki|vdw)?\s*(?:for\b)?\s*:?'
    r'|all\s+items\s+(?:with\s+the\s+word|containing|for|with|about)\b'
    r')\s*',
    re.IGNORECASE,
)
_TRAILING_RE = re.compile(r'\s+(?:in|on)\s+(?:vit\w*d\w*wiki|vdw)\b.*$', re.IGNORECASE)
_OR_RE = re.compile(r'\s+OR\s+')  # upper-case OR only, as in Tiki search syntax


def _strip_parens(text: str) -> str:
    text = text.strip()
    while text.startswith('('):
        text = text[1:].strip()
    while text.endswith(')') and text.count('(') < text.count(')'):
        text = text[:-1].strip()
    return text


def extract_search_term(link_text: str) -> str:
    """The search query an old "Search VitaminDWiki for X" link meant ("" if none)."""
    text = re.sub(r'\s+', ' ', link_text.translate(_CURLY_QUOTES)).strip()
    text = _LEADING_RE.sub('', text)
    text = _TRAILING_RE.sub('', text)
    text = _strip_parens(text)
    text = re.sub(r'\s+and\s+', ' ', text, flags=re.IGNORECASE)  # Tiki AND = all words, the default
    return text.strip(' :')


def split_or_query(query: str) -> List[str]:
    """Alternatives of an OR query: 'ICU OR "critical care"' -> ['ICU', '"critical care"']."""
    query = query.translate(_CURLY_QUOTES)
    parts = [_strip_parens(part) for part in _OR_RE.split(query)]
    seen, alternatives = set(), []
    for part in parts:
        part = part.strip()
        if part and part.lower() not in seen:
            seen.add(part.lower())
            alternatives.append(part)
    return alternatives
