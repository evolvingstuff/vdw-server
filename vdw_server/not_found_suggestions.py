"""404 handling: redirect to the intended page when it's clear, otherwise suggest matches.

The requested address is turned into words ("/The+top+10+chronic+health+problems..." ->
"The top 10 chronic health problems ...") and looked up:
  1. exact page address (slug) -> permanent redirect
  2. Meilisearch title search -> temporary redirect when the best title is very close to the
     request (same apart from a date, one is the start of the other, or >= 92% similar and
     clearly ahead of the runner-up); otherwise its closest titles are shown as suggestions.

This replaced an in-memory trigram index that used ~180 MB per worker and took seconds to
build at startup. If Meilisearch is unavailable, the plain 404 page is shown.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus

from django.http import HttpRequest
from django.urls import reverse
from django.utils.text import slugify

logger = logging.getLogger(__name__)

MAX_NOT_FOUND_SUGGESTIONS = 5
SEARCH_CANDIDATES = 10
MIN_QUERY_SLUG_LENGTH = 3
MIN_WORDS_FOR_FUZZY = 2
CLOSE_RATIO = 0.92
CLOSE_MARGIN = 0.05
MIN_PREFIX_LENGTH = 25

# Addresses that are probes or files, not page titles: don't spend a search on them.
_JUNK_EXTENSIONS = re.compile(r'\.(php|asp|aspx|jsp|cgi|env|xml|json|txt|js|css|map|ico|png|jpe?g|gif|svg|zip|gz|sql|bak|ini|yml|yaml)$', re.I)
_TRAILING_DATE_RE = re.compile(
    r'[\s\-–—]*(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*(?:\d{1,2},?\s*)?)?'
    r'(?:19|20)\d\d\s*$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NotFoundSuggestion:
    title: str
    url: str


@dataclass(frozen=True)
class NotFoundResult:
    requested_phrase: str
    redirect_url: str = ''
    permanent: bool = False
    suggestions: Tuple[NotFoundSuggestion, ...] = ()


def _extract_request_queries(request: HttpRequest) -> Tuple[str, ...]:
    phrases: List[str] = []
    path = request.path.strip('/')

    if path:
        if path.startswith('pages/'):
            _, _, remainder = path.partition('/')
            if remainder:
                phrases.append(remainder)
        elif path.lower() != 'tiki-index.php':
            phrases.append(path)

        if '/' in path:
            phrases.append(path.rsplit('/', 1)[-1])

    for key, value in parse_qsl(request.META.get('QUERY_STRING', ''), keep_blank_values=False):
        if key in {'page', 'page_id', 'slug', 'title', 'q'}:
            phrases.append(value)

    normalized_phrases: List[str] = []
    seen = set()
    for phrase in phrases:
        normalized = _humanize_query_text(phrase)
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            normalized_phrases.append(normalized)
    return tuple(normalized_phrases)


def _humanize_query_text(raw_text: str) -> str:
    decoded = unquote_plus(raw_text).strip().strip('/')
    if decoded.endswith('.html'):
        decoded = decoded[:-5]
    normalized = re.sub(r'[/_-]+', ' ', decoded)
    return re.sub(r'\s+', ' ', normalized).strip()


_LEADING_ARTICLE_RE = re.compile(r'^(?:the|a|an)\s+', re.IGNORECASE)


def _without_leading_article(text: str) -> str:
    """'The top 10 chronic...' -> 'top 10 chronic...' (titles often drop or add a leading 'The')."""
    return _LEADING_ARTICLE_RE.sub('', text.strip())


def _comparable(text: str) -> str:
    """Slug without a leading article or trailing date, for comparing a request with a title."""
    return slugify(_without_leading_article(_TRAILING_DATE_RE.sub('', text.replace('+', ' ')).strip()))


def _is_very_close(query: str, title: str, runner_up: Optional[str]) -> bool:
    q, t = _comparable(query), _comparable(title)
    if not q or not t:
        return False
    if q == t:
        return True
    shorter, longer = sorted((q, t), key=len)
    if len(shorter) >= MIN_PREFIX_LENGTH and longer.startswith(shorter):
        return True
    ratio = SequenceMatcher(a=q, b=t).ratio()
    runner_ratio = SequenceMatcher(a=q, b=_comparable(runner_up)).ratio() if runner_up else 0.0
    return ratio >= CLOSE_RATIO and ratio - runner_ratio >= CLOSE_MARGIN


def _exact_page_url(query_slug: str) -> str:
    from pages.models import Page
    from site_pages.models import SitePage

    if Page.objects.filter(status='published', slug=query_slug).exists():
        return reverse('page_detail', args=[query_slug])
    site_page = SitePage.objects.filter(is_published=True, slug=query_slug).first()
    return site_page.get_absolute_url() if site_page else ''


def _search_titles(phrase: str) -> List[NotFoundSuggestion]:
    from search.search import search_pages

    try:
        response = search_pages(
            _without_leading_article(phrase), limit=SEARCH_CANDIDATES, offset=0, matching_strategy='last'
        )
    except Exception as exc:  # search engine down or failing: fall back to the plain 404 page
        logger.warning("404 suggestions unavailable: %s", exc)
        return []
    target = _comparable(phrase)
    hits = sorted(
        response.get('hits', []),
        key=lambda hit: SequenceMatcher(a=target, b=_comparable(hit['title'])).ratio(),
        reverse=True,
    )
    return [NotFoundSuggestion(title=hit['title'], url=f"/pages/{hit['slug']}/") for hit in hits]


def resolve_not_found(request: HttpRequest) -> NotFoundResult:
    """Decide what to do with a missing address: redirect, suggestions, or plain 404."""
    phrases = _extract_request_queries(request)
    if not phrases:
        return NotFoundResult(requested_phrase='')

    for phrase in phrases:
        query_slug = slugify(phrase)
        if len(query_slug) >= MIN_QUERY_SLUG_LENGTH:
            url = _exact_page_url(query_slug)
            if url and url.rstrip('/') != request.path.rstrip('/'):
                return NotFoundResult(requested_phrase=phrases[0], redirect_url=url, permanent=True)

    phrase = phrases[0]
    if _JUNK_EXTENSIONS.search(request.path) and not request.GET.get('page'):
        return NotFoundResult(requested_phrase=phrase)
    if len(slugify(phrase).split('-')) < MIN_WORDS_FOR_FUZZY:
        return NotFoundResult(requested_phrase=phrase)

    suggestions = _search_titles(phrase)
    if suggestions:
        runner_up = suggestions[1].title if len(suggestions) > 1 else None
        if _is_very_close(phrase, suggestions[0].title, runner_up):
            return NotFoundResult(requested_phrase=phrase, redirect_url=suggestions[0].url, permanent=False)
    return NotFoundResult(requested_phrase=phrase, suggestions=tuple(suggestions[:MAX_NOT_FOUND_SUGGESTIONS]))


def get_not_found_requested_phrase(request: HttpRequest) -> str:
    """Best human-readable phrase for a missing request path (no lookups)."""
    phrases = _extract_request_queries(request)
    return phrases[0] if phrases else ''
