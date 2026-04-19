"""In-memory 404 suggestion index for published content."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from threading import RLock
import re
from typing import DefaultDict
from urllib.parse import parse_qsl, unquote_plus

from django.http import HttpRequest
from django.utils.text import slugify

from pages.models import Page
from site_pages.models import SitePage

MAX_NOT_FOUND_SUGGESTIONS = 10
MAX_CANDIDATES_TO_SCORE = 250
MIN_QUERY_SLUG_LENGTH = 3
_STOP_WORDS = frozenset({
    'a',
    'an',
    'and',
    'are',
    'for',
    'from',
    'in',
    'is',
    'of',
    'on',
    'or',
    'the',
    'to',
    'with',
})


@dataclass(frozen=True)
class NotFoundSuggestion:
    title: str
    url: str


@dataclass(frozen=True)
class _SuggestionIndexEntry:
    cache_key: str
    title: str
    url: str
    title_slug: str
    route_slug: str


_entries_by_key: dict[str, _SuggestionIndexEntry] = {}
_key_tokens: dict[str, frozenset[str]] = {}
_key_trigrams: dict[str, frozenset[str]] = {}
_exact_slug_index: DefaultDict[str, set[str]] = defaultdict(set)
_token_index: DefaultDict[str, set[str]] = defaultdict(set)
_trigram_index: DefaultDict[str, set[str]] = defaultdict(set)
_loaded = False
_lock = RLock()


def load_not_found_suggestions(force: bool = False) -> None:
    """Populate the in-memory suggestion index from published content."""

    global _entries_by_key, _key_tokens, _key_trigrams, _exact_slug_index, _token_index, _trigram_index, _loaded

    with _lock:
        if _loaded and not force:
            return

    entries_by_key: dict[str, _SuggestionIndexEntry] = {}
    key_tokens: dict[str, frozenset[str]] = {}
    key_trigrams: dict[str, frozenset[str]] = {}
    exact_slug_index: DefaultDict[str, set[str]] = defaultdict(set)
    token_index: DefaultDict[str, set[str]] = defaultdict(set)
    trigram_index: DefaultDict[str, set[str]] = defaultdict(set)

    pages = Page.objects.filter(status='published').only('id', 'slug', 'title')
    for page in pages.iterator():
        entry = _page_entry(page)
        _register_entry(entry, entries_by_key, key_tokens, key_trigrams, exact_slug_index, token_index, trigram_index)

    site_pages = SitePage.objects.filter(is_published=True).only('id', 'slug', 'title', 'page_type')
    for site_page in site_pages.iterator():
        entry = _site_page_entry(site_page)
        _register_entry(entry, entries_by_key, key_tokens, key_trigrams, exact_slug_index, token_index, trigram_index)

    with _lock:
        _entries_by_key = entries_by_key
        _key_tokens = key_tokens
        _key_trigrams = key_trigrams
        _exact_slug_index = exact_slug_index
        _token_index = token_index
        _trigram_index = trigram_index
        _loaded = True


def reload_not_found_suggestions() -> None:
    """Force a full rebuild of the suggestion index."""

    load_not_found_suggestions(force=True)


def clear_not_found_suggestions_cache() -> None:
    """Reset the suggestion index. Primarily used in tests."""

    global _entries_by_key, _key_tokens, _key_trigrams, _exact_slug_index, _token_index, _trigram_index, _loaded

    with _lock:
        _entries_by_key = {}
        _key_tokens = {}
        _key_trigrams = {}
        _exact_slug_index = defaultdict(set)
        _token_index = defaultdict(set)
        _trigram_index = defaultdict(set)
        _loaded = False


def get_not_found_suggestions(request: HttpRequest) -> tuple[str, tuple[NotFoundSuggestion, ...]]:
    """Return a human-readable missing phrase and up to 10 suggestions."""

    load_not_found_suggestions()

    query_texts = _extract_request_queries(request)
    if not query_texts:
        return '', tuple()

    best_scores: dict[str, float] = {}

    with _lock:
        for query_text in query_texts:
            query_slug = slugify(query_text)
            if len(query_slug) < MIN_QUERY_SLUG_LENGTH:
                continue

            query_tokens = _meaningful_tokens(query_slug)
            query_trigrams = _build_trigrams(query_slug)
            candidate_keys = _candidate_keys_for_query(query_tokens, query_trigrams)

            for cache_key in candidate_keys:
                entry = _entries_by_key[cache_key]
                entry_tokens = _key_tokens[cache_key]
                entry_trigrams = _key_trigrams[cache_key]
                score = _score_entry(query_slug, query_tokens, query_trigrams, entry, entry_tokens, entry_trigrams)

                previous_score = best_scores.get(cache_key)
                if previous_score is None or score > previous_score:
                    best_scores[cache_key] = score

        ranked_entries = sorted(
            (
                (score, _entries_by_key[cache_key])
                for cache_key, score in best_scores.items()
                if score > 0
            ),
            key=lambda item: (-item[0], item[1].title.lower(), item[1].url),
        )

    suggestions = tuple(
        NotFoundSuggestion(title=entry.title, url=entry.url)
        for _, entry in ranked_entries[:MAX_NOT_FOUND_SUGGESTIONS]
    )
    return query_texts[0], suggestions


def get_not_found_redirect_url(request: HttpRequest) -> str:
    """Return the canonical URL when the missing request is an exact normalized match."""

    load_not_found_suggestions()

    query_texts = _extract_request_queries(request)
    if not query_texts:
        return ''

    with _lock:
        for query_text in query_texts:
            query_slug = slugify(query_text)
            if len(query_slug) < MIN_QUERY_SLUG_LENGTH:
                continue

            candidate_keys = _exact_slug_index.get(query_slug)
            if not candidate_keys or len(candidate_keys) != 1:
                continue

            cache_key = next(iter(candidate_keys))
            return _entries_by_key[cache_key].url

    return ''


def get_not_found_requested_phrase(request: HttpRequest) -> str:
    """Return the best human-readable phrase for a missing request path."""

    query_texts = _extract_request_queries(request)
    if not query_texts:
        return ''
    return query_texts[0]


def upsert_page_not_found_suggestion(page: Page) -> None:
    """Update the cached entry for a content page if the index is loaded."""

    assert page.pk, "Page must have a primary key before caching"

    if page.status != 'published':
        remove_page_not_found_suggestion(page.pk)
        return

    entry = _page_entry(page)
    _upsert_entry(entry)


def remove_page_not_found_suggestion(page_id: int) -> None:
    """Remove a content page from the suggestion cache if present."""

    assert isinstance(page_id, int), f"page_id must be int, got {type(page_id)}"
    _remove_entry(f'page:{page_id}')


def upsert_site_page_not_found_suggestion(site_page: SitePage) -> None:
    """Update the cached entry for a site page if the index is loaded."""

    assert site_page.pk, "Site page must have a primary key before caching"

    if not site_page.is_published:
        remove_site_page_not_found_suggestion(site_page.pk)
        return

    entry = _site_page_entry(site_page)
    _upsert_entry(entry)


def remove_site_page_not_found_suggestion(site_page_id: int) -> None:
    """Remove a site page from the suggestion cache if present."""

    assert isinstance(site_page_id, int), f"site_page_id must be int, got {type(site_page_id)}"
    _remove_entry(f'site_page:{site_page_id}')


def get_cached_not_found_count() -> int:
    """Return the number of cached suggestion entries."""

    with _lock:
        return len(_entries_by_key)


def _page_entry(page: Page) -> _SuggestionIndexEntry:
    assert page.pk, "Page must have a primary key before caching"
    return _SuggestionIndexEntry(
        cache_key=f'page:{page.pk}',
        title=page.title,
        url=f'/pages/{page.slug}/',
        title_slug=slugify(page.title),
        route_slug=page.slug,
    )


def _site_page_entry(site_page: SitePage) -> _SuggestionIndexEntry:
    assert site_page.pk, "Site page must have a primary key before caching"
    return _SuggestionIndexEntry(
        cache_key=f'site_page:{site_page.pk}',
        title=site_page.title,
        url=site_page.get_absolute_url(),
        title_slug=slugify(site_page.title),
        route_slug=site_page.slug,
    )


def _register_entry(
    entry: _SuggestionIndexEntry,
    entries_by_key: dict[str, _SuggestionIndexEntry],
    key_tokens: dict[str, frozenset[str]],
    key_trigrams: dict[str, frozenset[str]],
    exact_slug_index: DefaultDict[str, set[str]],
    token_index: DefaultDict[str, set[str]],
    trigram_index: DefaultDict[str, set[str]],
) -> None:
    tokens = frozenset(_index_tokens(entry))
    trigrams = frozenset(_index_trigrams(entry))

    entries_by_key[entry.cache_key] = entry
    key_tokens[entry.cache_key] = tokens
    key_trigrams[entry.cache_key] = trigrams
    for slug in {entry.title_slug, entry.route_slug}:
        if slug:
            exact_slug_index[slug].add(entry.cache_key)

    for token in tokens:
        token_index[token].add(entry.cache_key)
    for trigram in trigrams:
        trigram_index[trigram].add(entry.cache_key)


def _extract_request_queries(request: HttpRequest) -> tuple[str, ...]:
    phrases: list[str] = []
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

    raw_query_string = request.META.get('QUERY_STRING', '')
    for key, value in parse_qsl(raw_query_string, keep_blank_values=False):
        if key in {'page', 'page_id', 'slug', 'title', 'q'}:
            phrases.append(value)

    normalized_phrases: list[str] = []
    seen = set()
    for phrase in phrases:
        normalized_phrase = _humanize_query_text(phrase)
        if not normalized_phrase:
            continue
        dedupe_key = normalized_phrase.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized_phrases.append(normalized_phrase)

    return tuple(normalized_phrases)


def _humanize_query_text(raw_text: str) -> str:
    decoded = unquote_plus(raw_text).strip().strip('/')
    if not decoded:
        return ''

    if decoded.endswith('.html'):
        decoded = decoded[:-5]

    normalized = re.sub(r'[/_-]+', ' ', decoded)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def _candidate_keys_for_query(query_tokens: frozenset[str], query_trigrams: frozenset[str]) -> tuple[str, ...]:
    candidate_hits: Counter[str] = Counter()

    for token in query_tokens:
        for cache_key in _token_index.get(token, ()):
            candidate_hits[cache_key] += 10

    for trigram in query_trigrams:
        for cache_key in _trigram_index.get(trigram, ()):
            candidate_hits[cache_key] += 1

    return tuple(cache_key for cache_key, _ in candidate_hits.most_common(MAX_CANDIDATES_TO_SCORE))


def _score_entry(
    query_slug: str,
    query_tokens: frozenset[str],
    query_trigrams: frozenset[str],
    entry: _SuggestionIndexEntry,
    entry_tokens: frozenset[str],
    entry_trigrams: frozenset[str],
) -> float:
    title_haystack = f'-{entry.title_slug}-'
    route_haystack = f'-{entry.route_slug}-'
    score = 0.0

    if query_slug == entry.title_slug:
        score += 250
    if query_slug == entry.route_slug:
        score += 220
    if entry.title_slug.startswith(query_slug):
        score += 80
    if entry.route_slug.startswith(query_slug):
        score += 70
    if f'-{query_slug}-' in title_haystack:
        score += 60
    if f'-{query_slug}-' in route_haystack:
        score += 55

    score += len(query_tokens.intersection(entry_tokens)) * 14
    score += len(query_trigrams.intersection(entry_trigrams)) * 1.25

    score += SequenceMatcher(a=query_slug, b=entry.title_slug).ratio() * 40
    score += SequenceMatcher(a=query_slug, b=entry.route_slug).ratio() * 30
    return score


def _index_tokens(entry: _SuggestionIndexEntry) -> tuple[str, ...]:
    tokens = set(_meaningful_tokens(entry.title_slug))
    tokens.update(_meaningful_tokens(entry.route_slug))
    return tuple(sorted(tokens))


def _index_trigrams(entry: _SuggestionIndexEntry) -> tuple[str, ...]:
    trigrams = set(_build_trigrams(entry.title_slug))
    trigrams.update(_build_trigrams(entry.route_slug))
    return tuple(sorted(trigrams))


def _meaningful_tokens(value: str) -> frozenset[str]:
    if not value:
        return frozenset()

    tokens = {
        token
        for token in value.split('-')
        if token and len(token) >= 3 and token not in _STOP_WORDS
    }
    return frozenset(tokens)


def _build_trigrams(value: str) -> frozenset[str]:
    compact = value.replace('-', ' ').strip()
    if len(compact) < 3:
        return frozenset()
    return frozenset(compact[index:index + 3] for index in range(len(compact) - 2))


def _upsert_entry(entry: _SuggestionIndexEntry) -> None:
    with _lock:
        if not _loaded:
            return
        _remove_entry_locked(entry.cache_key)
        _register_entry(entry, _entries_by_key, _key_tokens, _key_trigrams, _exact_slug_index, _token_index, _trigram_index)


def _remove_entry(cache_key: str) -> None:
    with _lock:
        if not _loaded:
            return
        _remove_entry_locked(cache_key)


def _remove_entry_locked(cache_key: str) -> None:
    entry = _entries_by_key.pop(cache_key, None)
    entry_tokens = _key_tokens.pop(cache_key, frozenset())
    entry_trigrams = _key_trigrams.pop(cache_key, frozenset())

    if entry is not None:
        for slug in {entry.title_slug, entry.route_slug}:
            if not slug:
                continue
            exact_slug_cache_keys = _exact_slug_index.get(slug)
            if exact_slug_cache_keys is None:
                continue
            exact_slug_cache_keys.discard(cache_key)
            if not exact_slug_cache_keys:
                del _exact_slug_index[slug]

    for token in entry_tokens:
        token_cache_keys = _token_index.get(token)
        if token_cache_keys is None:
            continue
        token_cache_keys.discard(cache_key)
        if not token_cache_keys:
            del _token_index[token]

    for trigram in entry_trigrams:
        trigram_cache_keys = _trigram_index.get(trigram)
        if trigram_cache_keys is None:
            continue
        trigram_cache_keys.discard(cache_key)
        if not trigram_cache_keys:
            del _trigram_index[trigram]
