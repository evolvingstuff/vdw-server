"""SEO helpers: meta descriptions generated from page markdown."""

from __future__ import annotations

import html
import re

META_DESCRIPTION_MAX_CHARS = 155
MIN_PARAGRAPH_CHARS = 80
MIN_PARAGRAPH_WORDS = 12

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")
_MARKDOWN_SYMBOLS_RE = re.compile(r"[*_#>`|]+")
_WHITESPACE_RE = re.compile(r"\s+")

# Paragraphs that are citation boilerplate rather than prose.
_CITATION_RE = re.compile(
    r"\bdoi\b|\bPMID\b|\bPMC\d|\bISBN\b|"
    r"\b(19|20)\d\d\s*[;,]?\s*\d+\s*\(\d+\)|"   # 2012;21(10) / 2021, 13(4)
    r"\b(19|20)\d\d\s+[A-Z][a-z]{2}\s*;|"        # 2013 Aug;
    r"\bet al\b|@|"
    r"\b(Department|Dept\.|University|Institute|Hospital|School of|Faculty of|Received|Accepted|"
    r"Correspondence|Copyright|Published online|Epub)\b",
    re.IGNORECASE,
)
_SECTION_LABEL_RE = re.compile(
    r"^(background|introduction|objectives?|aims?|purpose|context|summary|abstract|"
    r"results?|findings|conclusions?|interpretation|methods?)"
    r"(\s*/\s*(objectives?|aims?|purpose|methods?))?\s*[:.]\s*",
    re.IGNORECASE,
)
# Methods-style abstract sections describe how a study was done, not what it found;
# they only make a weak summary, so they are used only if nothing better exists.
_METHODS_LABEL_RE = re.compile(
    r"^(methods?|materials and methods|design|setting|participants|patients and methods|"
    r"study design|data sources?|search strategy)\s*[:.]",
    re.IGNORECASE,
)
_TIKI_MARKUP_RE = re.compile(r"\{[A-Za-z]+(\([^}]*\))?\}")
# Leftover Tiki wiki boilerplate and journal notices.
_BOILERPLATE_RE = re.compile(
    r"SELECT\s+hits|visitor count|tiki|This article belongs to|Special Issue|Creative Commons|"
    r"Open Access|Download the PDF|See also|Items in both categories",
    re.IGNORECASE,
)
# Author lists: "Meiqi Hao1,2, Ruoxin Xu1,2" or "Gunville CF1, Mourani PM".
_AUTHOR_TOKEN_RE = re.compile(r"\b[A-Z][a-z]+\d|\b[A-Z]{1,3}\d?,")


def _plain_text(markdown_paragraph: str) -> str:
    text = _IMAGE_RE.sub(" ", markdown_paragraph)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _TIKI_MARKUP_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = _MARKDOWN_SYMBOLS_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _link_text_share(raw: str, paragraph: str) -> float:
    link_chars = sum(len(m.group(1)) for m in _LINK_RE.finditer(raw))
    link_chars += sum(len(m) for m in re.findall(r"<a\b[^>]*>(.*?)</a>", raw, re.IGNORECASE | re.DOTALL))
    return link_chars / max(len(paragraph), 1)


def _is_prose(paragraph: str, raw: str) -> bool:
    stripped = raw.lstrip()
    if stripped.startswith(("#", "*", "-", "|", "<img", "![")) and not stripped.startswith("**"):
        return False  # headings, list items, tables, images
    words = paragraph.split()
    if len(paragraph) < MIN_PARAGRAPH_CHARS or len(words) < MIN_PARAGRAPH_WORDS:
        return False
    if _CITATION_RE.search(paragraph) or _BOILERPLATE_RE.search(paragraph):
        return False
    if len(_AUTHOR_TOKEN_RE.findall(paragraph)) >= 3:
        return False
    if _link_text_share(raw, paragraph) > 0.5:
        return False  # mostly links to other pages
    if paragraph.startswith("("):
        return False
    # Prose has plenty of lowercase words and at least one sentence; author lists,
    # affiliations and bylines are mostly capitalized fragments.
    alpha_words = [w for w in words if w[:1].isalpha()]
    lowercase_share = sum(w[0].islower() for w in alpha_words) / max(len(alpha_words), 1)
    if lowercase_share < 0.4:
        return False
    if not re.search(r"[a-z)%][.!?](\s|$)", paragraph):
        return False
    return True


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(" ,;:–-")
    return cut + "…"


def generate_meta_description(content_md: str) -> str:
    """First prose paragraph of the page, skipping headings, citations and author lines.

    Returns "" when nothing suitable is found; the template then omits the tag and
    lets the search engine pick its own snippet.
    """
    assert isinstance(content_md, str), f"content_md must be str, got {type(content_md)}"
    methods_fallback = ""
    for raw in re.split(r"\n\s*\n", content_md):
        paragraph = _plain_text(raw)
        if not _is_prose(paragraph, raw):
            continue
        if _METHODS_LABEL_RE.match(paragraph):
            methods_fallback = methods_fallback or paragraph
            continue
        return _truncate(_SECTION_LABEL_RE.sub("", paragraph), META_DESCRIPTION_MAX_CHARS)
    return _truncate(_SECTION_LABEL_RE.sub("", methods_fallback), META_DESCRIPTION_MAX_CHARS)


def meta_description_for(page) -> str:
    """Explicit meta_description if set, otherwise generated from the page content."""
    explicit = (getattr(page, "meta_description", "") or "").strip()
    if explicit:
        return _truncate(_WHITESPACE_RE.sub(" ", explicit), META_DESCRIPTION_MAX_CHARS)
    return generate_meta_description(page.content_md or "")
