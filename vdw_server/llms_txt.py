"""/llms.txt: a curated Markdown guide to the site for AI tools (https://llmstxt.org).

Generated from the in-memory topic index, so it stays current and costs nothing per
request; the text is rebuilt only when the topic index is rebuilt (i.e. after edits).
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple

from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse

from tags.services import get_topic_index

# Topics with fewer pages than this are left out of the topic list to keep the file focused.
MIN_TOPIC_PAGES = 5

_cache: Optional[Tuple[tuple, str]] = None
_cache_lock = threading.Lock()


def _absolute(path: str) -> str:
    return f"{settings.SITE_BASE_URL.rstrip('/')}{path}"


def _md_link_text(text: str) -> str:
    return text.replace('[', '(').replace(']', ')').strip()


def build_llms_txt() -> str:
    index = get_topic_index()
    rounded_count = len(index.pages) // 100 * 100  # 14,773 -> "14,700+"
    topics = [t for t in index.topic_counts if t.count >= MIN_TOPIC_PAGES]

    # Overview titles are self-explanatory ("Overview Asthma and Vitamin D"); the automatic
    # meta descriptions often start with an unrepresentative paragraph, so none are added.
    overviews = sorted(
        (page for page in index.pages.values() if page.title.lower().startswith('overview')),
        key=lambda page: page.title.lower(),
    )

    lines = [
        "# VitaminDWiki",
        "",
        f"> VitaminDWiki is a comprehensive website on vitamin D, with {rounded_count:,}+ curated pages "
        "of evidence-based studies, overviews, charts and meta-analyses about vitamin D and related nutrients "
        f"(magnesium, omega-3, vitamin K and others), organized into {len(index.topic_counts):,} topics.",
        "",
        "Most pages summarize one published study, review or meta-analysis: the title states the headline "
        "finding, and the page quotes the abstract and key figures with a link (usually a DOI) to the original "
        "paper. Overview pages summarize a whole health topic and are the best starting point. Topic pages list "
        "every page on a topic, most important first (overviews, then meta-analyses, then randomized controlled "
        "trials, then newest). Pages summarize research; they are not medical advice.",
        "",
        "## Start here",
        "",
        f"- [Topics/Subjects]({_absolute(reverse('tag_list'))}): browse all topics and combine them "
        "(e.g. Bone + Infant-Child) to find pages about all of them",
        f"- [Health problems and vitamin D]({_absolute(reverse('page_detail', args=['health-problems-and-d']))}): "
        "index of 340+ diseases and health topics related to vitamin D",
        f"- [Most recent pages]({_absolute(reverse('recent_page_list'))}): newest and recently updated pages",
        "",
        "## Overviews",
        "",
    ]
    for page in overviews:
        lines.append(f"- [{_md_link_text(page.title)}]({_absolute(page.url)})")

    lines += ["", "## Topics", ""]
    for topic in topics:
        url = _absolute(reverse('tag_pages', args=[topic.slug]))
        lines.append(f"- [{_md_link_text(topic.name)}]({url}): {topic.count:,} pages")

    lines += [
        "",
        "## Optional",
        "",
        f"- [Sitemap]({_absolute(reverse('sitemap_xml'))}): every page and topic URL with last-modified dates",
        "- [VitaminDWiki on Substack](https://hlahore.substack.com/): 300+ posts on getting enough vitamin D",
        "",
    ]
    return "\n".join(lines)


def llms_txt(request):
    global _cache
    signature = get_topic_index().signature
    cached = _cache
    if cached is None or cached[0] != signature:
        with _cache_lock:
            cached = _cache
            if cached is None or cached[0] != signature:
                cached = (signature, build_llms_txt())
                _cache = cached
    return HttpResponse(cached[1], content_type="text/plain; charset=utf-8")
