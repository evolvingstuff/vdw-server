"""Helpers for building and persisting the static sitemap."""

from __future__ import annotations

from datetime import timezone as datetime_timezone
import tempfile
from pathlib import Path
from typing import List, Sequence, Tuple
from xml.etree.ElementTree import Element, SubElement, tostring

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from pages.models import Page
from site_pages.models import SitePage


UrlEntry = Tuple[str, str | None]


def refresh_sitemap(base_url: str) -> Path:
    """Regenerate the sitemap XML and atomically write it to disk."""
    normalized_base = _normalize_base_url(base_url)
    entries = _collect_entries(normalized_base)
    xml_payload = _render_xml(entries)
    return _write_to_disk(xml_payload)


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip()
    if not normalized:
        raise ValueError("base_url cannot be empty")
    # Drop trailing slash so pathname concatenation is consistent.
    while normalized.endswith('/'):
        normalized = normalized[:-1]
    if '://' not in normalized:
        raise ValueError(f"base_url must include protocol (got {normalized!r})")
    return normalized


def _collect_entries(base_url: str) -> List[UrlEntry]:
    entries: List[UrlEntry] = []

    site_pages = SitePage.objects.filter(is_published=True).order_by('slug')
    for site_page in site_pages:
        path = site_page.get_absolute_url()
        entries.append((
            _absolute_url(base_url, path),
            _format_lastmod(site_page.modified_date),
        ))

    pages = Page.objects.filter(status='published').order_by('slug')
    for page in pages:
        path = reverse('page_detail', args=[page.slug])
        entries.append((
            _absolute_url(base_url, path),
            _format_lastmod(page.modified_date),
        ))

    # Keep output deterministic regardless of query ordering.
    entries.sort(key=lambda entry: entry[0])
    return entries


def _absolute_url(base_url: str, path: str) -> str:
    if not path.startswith('/'):
        raise ValueError(f"Sitemap paths must start with '/': {path!r}")
    return f"{base_url}{path}"


def _format_lastmod(value) -> str | None:
    if value is None:
        return None
    aware = value
    if timezone.is_naive(aware):
        aware = timezone.make_aware(aware, datetime_timezone.utc)
    aware = aware.astimezone(datetime_timezone.utc)
    return aware.isoformat(timespec='seconds')


def _render_xml(entries: Sequence[UrlEntry]) -> str:
    urlset = Element('urlset', {
        'xmlns': 'http://www.sitemaps.org/schemas/sitemap/0.9',
    })
    for loc, lastmod in entries:
        url_el = SubElement(urlset, 'url')
        loc_el = SubElement(url_el, 'loc')
        loc_el.text = loc
        if lastmod:
            lastmod_el = SubElement(url_el, 'lastmod')
            lastmod_el.text = lastmod

    xml_bytes = tostring(urlset, encoding='utf-8', xml_declaration=True)
    return xml_bytes.decode('utf-8')


def _write_to_disk(xml_payload: str) -> Path:
    sitemap_path = Path(getattr(settings, 'SITEMAP_FILE_PATH', settings.BASE_DIR / 'sitemap.xml'))
    sitemap_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False, dir=str(sitemap_path.parent)) as handle:
        handle.write(xml_payload)
        tmp_name = handle.name

    tmp_path = Path(tmp_name)
    tmp_path.replace(sitemap_path)
    return sitemap_path
