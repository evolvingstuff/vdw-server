"""Preview of fixes for broken internal links (phase 1): reads the local SQLite database
read-only and writes an HTML report. Nothing in the database is changed.

Broken links are <a href="/pages/X/"> where X is not a published page. Three groups:
  - old Tiki search links (/pages/search-results/): the link text is the search term
  - old Tiki file downloads and other tiki-*.php addresses: original target unknown
  - pages that were renamed or never migrated
"""

from __future__ import annotations

import difflib
import html
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote_plus

from django.utils.text import slugify

from search.query import extract_search_term

SITE_BASE_URL = "https://vitamindwiki.com"
ATTACHMENTS_BASE_URL = "https://d378j1rmrlek7x.cloudfront.net"  # S3 key public/<path> -> <base>/<path>

HIGH, LIKELY, REVIEW = 'high', 'likely', 'review'

_LINK_RE = re.compile(
    r'<a\b[^>]*href="(?:https?://(?:www\.)?vitamindwiki\.com)?/pages/([^/"#?]+)/?[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TRAILING_DATE_RE = re.compile(
    r'[\s\-–—]*(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*(?:\d{1,2},?\s*)?)?'
    r'(?:19|20)\d\d\s*$',
    re.IGNORECASE,
)
_SEARCH_PREFIX_RE = re.compile(r'^(?:search\s+(?:vitamindwiki\s+)?(?:for\s+)?|click\s+here\s+for\s+)', re.I)
_GENERIC_TEXT = {'click here', 'here', 'this', 'link', 'more', 'details', 'see', 'pdf', 'file', 'this page'}


@dataclass
class Fix:
    action: str          # 'page', 'topic', 'search', 'unlink'
    url: str = ''
    label: str = ''
    confidence: str = REVIEW
    method: str = ''


@dataclass
class TargetGroup:
    kind: str            # 'search', 'tiki', 'missing', 'draft'
    target: str
    links: List[Tuple[str, str, str]] = field(default_factory=list)  # (source slug, source title, link text)
    fixes: Counter = field(default_factory=Counter)                   # Fix key -> count
    fix_objects: Dict[tuple, Fix] = field(default_factory=dict)
    link_fixes: List[Tuple[str, str, str, Fix]] = field(default_factory=list)  # (source slug, target, text, fix)


def _norm_file(text: str) -> str:
    return re.sub(r'-+', '-', slugify(text)).strip('-')


def _norm_title(text: str) -> str:
    return slugify(_TRAILING_DATE_RE.sub('', text.replace('+', ' ')).strip())


class Resolver:
    def __init__(self, conn: sqlite3.Connection, pdf_keys: Optional[List[str]] = None):
        rows = conn.execute("SELECT slug, status, title, aliases FROM posts_post").fetchall()
        self.published = {slug: title for slug, status, title, _ in rows if status == 'published'}
        self.drafts = {slug for slug, status, _, _ in rows if status != 'published'}
        self.slugs = list(self.published)

        self.by_title: Dict[str, Set[str]] = defaultdict(set)
        self.by_alias: Dict[str, Set[str]] = defaultdict(set)
        for slug, status, title, aliases in rows:
            if status != 'published':
                continue
            self.by_title[_norm_title(title)].add(slug)
            for alias in (aliases or '').splitlines():
                alias = alias.strip().lstrip('/')
                if alias and not alias.isdigit():
                    self.by_alias[slugify(alias.replace('+', ' '))].add(slug)
                    self.by_alias[_norm_title(alias)].add(slug)

        self.tags: Dict[str, Tuple[str, str]] = {}
        for name, slug in conn.execute("SELECT name, slug FROM tags_tag"):
            self.tags[slugify(name)] = (slug, name)
            self.tags.setdefault(slug, (slug, name))
        self._close_cache: Dict[str, List[Tuple[str, float]]] = {}

        # PDFs in S3 (public/attachments/...), indexed by a normalized file name.
        self.pdfs: Dict[str, str] = {}
        self.pdfs_without_date: Dict[str, str] = {}
        for key in pdf_keys or []:
            if not key.lower().endswith('.pdf') or not key.startswith('public/'):
                continue
            stem = _norm_file(key.rsplit('/', 1)[1][:-4])
            self.pdfs.setdefault(stem, key)
            self.pdfs_without_date.setdefault(_norm_file(_TRAILING_DATE_RE.sub('', stem.replace('-', ' '))), key)
        self._pdf_stems = list(self.pdfs)
        self._pdf_nodate_stems = list(self.pdfs_without_date)

    def _pdf_fix(self, key: str, confidence: str, method: str) -> Fix:
        return Fix('file', f"{ATTACHMENTS_BASE_URL}/{key[len('public/'):]}", key.rsplit('/', 1)[1], confidence, method)

    def resolve_download(self, text: str, source_title: str) -> Fix:
        """Old Tiki file download: find the PDF in S3 by link text, else by the page's title."""
        if self.pdfs:
            name = _norm_file(re.sub(r'\.pdf$', '', text.strip(), flags=re.I))
            generic = not name or text.strip().lower() in _GENERIC_TEXT or name.startswith(('click-here', 'download'))
            if not generic and len(name) >= 6:
                if name in self.pdfs:
                    return self._pdf_fix(self.pdfs[name], HIGH, 'link text matches the PDF file name')
                close = difflib.get_close_matches(name, self._pdf_stems, n=1, cutoff=0.85)
                if close:
                    return self._pdf_fix(self.pdfs[close[0]], LIKELY, 'link text is very close to the PDF file name')
            title = _norm_file(_TRAILING_DATE_RE.sub('', source_title))
            if title:
                hit = self.pdfs_without_date.get(title)
                if not hit:
                    close = difflib.get_close_matches(title, self._pdf_nodate_stems, n=1, cutoff=0.9)
                    hit = self.pdfs_without_date[close[0]] if close else None
                if hit:
                    return self._pdf_fix(hit, LIKELY, 'page title matches the PDF file name')
        return Fix('unlink', '', 'Remove the link, keep the text', REVIEW,
                   'no matching PDF found' if self.pdfs else 'old Tiki download; PDF list not loaded')

    def _page(self, slug: str, confidence: str, method: str) -> Fix:
        return Fix('page', f'/pages/{slug}/', self.published[slug], confidence, method)

    def _topic(self, text: str) -> Optional[Fix]:
        key = slugify(_SEARCH_PREFIX_RE.sub('', text).strip())
        if key in self.tags:
            slug, name = self.tags[key]
            return Fix('topic', f'/tags/{slug}/', f'Topic: {name}', HIGH, 'link text is a topic name')
        return None

    def _close(self, target: str) -> List[Tuple[str, float]]:
        if target not in self._close_cache:
            matches = difflib.get_close_matches(target, self.slugs, n=2, cutoff=0.75)
            self._close_cache[target] = [(m, difflib.SequenceMatcher(None, target, m).ratio()) for m in matches]
        return self._close_cache[target]

    def resolve_missing(self, target: str, text: str) -> Fix:
        unique = lambda s: next(iter(s)) if len(s) == 1 else None

        slug = unique(self.by_alias.get(target, set()))
        if slug:
            return self._page(slug, HIGH, 'address is an old name of this page')

        text_key = _norm_title(text)
        if len(text_key) >= 12 and text.strip().lower() not in _GENERIC_TEXT:
            slug = unique(self.by_title.get(text_key, set()) | self.by_alias.get(text_key, set()))
            if slug:
                return self._page(slug, HIGH, 'link text matches this page title')

        topic = self._topic(text) or self._topic(target.replace('-', ' '))
        if topic:
            return topic

        prefix = [s for s in self.slugs if (s.startswith(target) or target.startswith(s)) and min(len(s), len(target)) >= 20]
        if len(prefix) == 1:
            return self._page(prefix[0], LIKELY, 'address is a shortened/lengthened form of this page')

        close = self._close(target)
        if close and close[0][1] >= 0.9 and (len(close) == 1 or close[0][1] - close[1][1] > 0.03):
            return self._page(close[0][0], LIKELY, f'address is {close[0][1]:.0%} similar')

        if len(text_key) >= 20:
            title_close = difflib.get_close_matches(text_key, list(self.by_title), n=1, cutoff=0.9)
            if title_close:
                slug = unique(self.by_title[title_close[0]])
                if slug:
                    return self._page(slug, LIKELY, 'link text is very close to this page title')

        if close:
            return self._page(close[0][0], REVIEW, f'possible match ({close[0][1]:.0%} similar address)')
        return Fix('unlink', '', 'Remove the link, keep the text', REVIEW, 'no matching page found')

    def resolve_search(self, text: str) -> Fix:
        topic = self._topic(text)
        if topic:
            return topic
        term = extract_search_term(text)
        if not term or term.lower() in _GENERIC_TEXT:
            return Fix('unlink', '', 'Remove the link, keep the text', REVIEW, 'search link without a usable term')
        return Fix('search', f'/search/?q={quote_plus(term)}', f'Search: {term}', HIGH, 'old Tiki search link')


def analyze(db_path: Path, pdf_keys: Optional[List[str]] = None) -> Tuple[Dict[Tuple[str, str], TargetGroup], int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        resolver = Resolver(conn, pdf_keys)
        rows = conn.execute(
            "SELECT slug, title, content_html FROM posts_post WHERE status = 'published'"
        ).fetchall()
    finally:
        conn.close()

    groups: Dict[Tuple[str, str], TargetGroup] = {}
    total = 0
    for source_slug, source_title, content_html in rows:
        for target, raw_text in _LINK_RE.findall(content_html or ''):
            target = target.strip()
            if target in resolver.published:
                continue
            text = html.unescape(re.sub(r'<[^>]+>', '', raw_text)).strip()
            total += 1
            if target == 'search-results':
                kind, fix = 'search', resolver.resolve_search(text)
                key = (kind, fix.label)
            elif target in ('tiki-download-filephp', 'ttiki-download-filephp'):
                kind, fix = 'download', resolver.resolve_download(text, source_title)
                key = (kind, fix.label if fix.action == 'file' else 'no matching PDF')
            elif target.startswith(('tiki-', 'ttiki-')):
                kind = 'tiki'
                fix = Fix('unlink', '', 'Remove the link, keep the text', REVIEW,
                          'old Tiki address; original page is not recorded in the database')
                key = (kind, target)
            elif target in resolver.drafts:
                kind, fix = 'draft', Fix('unlink', '', 'Links to an unpublished (draft) page', REVIEW, 'target is a draft')
                key = (kind, target)
            else:
                kind, fix = 'missing', resolver.resolve_missing(target, text)
                key = (kind, target)
            group_target = fix.label if kind in ('search', 'download') else target
            group = groups.setdefault(key, TargetGroup(kind=kind, target=group_target))
            group.links.append((source_slug, source_title, text))
            group.link_fixes.append((source_slug, target, text, fix))
            fix_key = (fix.action, fix.url, fix.confidence, fix.method)
            group.fixes[fix_key] += 1
            group.fix_objects[fix_key] = fix
    return groups, total


def _esc(value: str) -> str:
    return html.escape(value or '')


def _fix_cell(group: TargetGroup) -> str:
    parts = []
    for key, count in group.fixes.most_common():
        fix = group.fix_objects[key]
        target = (f"<a href='{_esc(SITE_BASE_URL + fix.url)}'>{_esc(fix.label)}</a>" if fix.url else _esc(fix.label))
        parts.append(f"<div><span class='conf {fix.confidence}'>{fix.confidence}</span> {target}"
                     f"{f' ×{count}' if len(group.fixes) > 1 else ''}<br><small>{_esc(fix.method)}</small></div>")
    return ''.join(parts)


def _group_rows(groups: List[TargetGroup], show_target: bool = True) -> str:
    rows = []
    for g in sorted(groups, key=lambda g: -len(g.links)):
        texts = Counter(t for _, _, t in g.links)
        sample_texts = '; '.join(f'“{_esc(t)}”' + (f' ×{n}' if n > 1 else '') for t, n in texts.most_common(3))
        sources = sorted({(s, t) for s, t, _ in g.links})
        source_links = ', '.join(f"<a href='{SITE_BASE_URL}/pages/{_esc(s)}/'>{_esc(t[:60])}</a>" for s, t in sources[:3])
        more = f" +{len(sources) - 3} more" if len(sources) > 3 else ''
        target = f"<code>{_esc(g.target)}</code>" if show_target else _esc(g.target)
        rows.append(f"<tr><td>{target}</td><td class='n'>{len(g.links)}</td><td>{sample_texts}</td>"
                    f"<td>{_fix_cell(g)}</td><td class='src'>{source_links}{more}</td></tr>")
    return ("<table><thead><tr><th>Broken target</th><th>Links</th><th>Link text</th><th>Proposed fix</th>"
            "<th>On pages</th></tr></thead><tbody>" + ''.join(rows) + "</tbody></table>")


def write_report(groups: Dict[Tuple[str, str], TargetGroup], total: int, output_dir: Path) -> Path:
    by_kind: Dict[str, List[TargetGroup]] = defaultdict(list)
    for g in groups.values():
        by_kind[g.kind].append(g)
    confidence = Counter()
    for g in groups.values():
        for (action, _, conf, _), n in g.fixes.items():
            confidence[conf] += n
    links_in = lambda kind: sum(len(g.links) for g in by_kind[kind])
    pages = {s for g in groups.values() for s, _, _ in g.links}

    missing = by_kind['missing']
    missing_by_conf = Counter()
    for g in missing:
        for (_, _, conf, _), n in g.fixes.items():
            missing_by_conf[conf] += n

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Broken link preview</title><style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:1300px;margin:2em auto;padding:0 16px;color:#222}",
        "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #ddd;padding:4px 6px;vertical-align:top;text-align:left}",
        "th{background:#eef2f6}td.n{text-align:right}td.src{font-size:12px;color:#555}code{font-size:12px}",
        ".conf{display:inline-block;border-radius:3px;padding:0 5px;font-size:11px;font-weight:600;color:#fff}",
        ".high{background:#2e7d32}.likely{background:#1565c0}.review{background:#c77700}",
        "summary{cursor:pointer;font-weight:600;font-size:1.1em;margin:1.2em 0 .5em}",
        "</style></head><body>",
        f"<h1>Broken internal links: preview</h1><p>Generated {datetime.now():%Y-%m-%d %H:%M} from the local "
        "database. Nothing has been changed.</p>",
        "<h2>Summary</h2><ul>",
        f"<li><b>{total:,}</b> broken internal links on <b>{len(pages):,}</b> published pages</li>",
        f"<li>Old Tiki search links: <b>{links_in('search'):,}</b> → topic page when the text is a topic, "
        "otherwise a site search for the text (needs the /search/ page switched on)</li>",
        f"<li>Old Tiki file downloads: <b>{links_in('download'):,}</b> → matched to the PDF in S3 for "
        f"<b>{sum(len(g.links) for g in by_kind['download'] if g.target != 'Remove the link, keep the text'):,}</b>; "
        "the rest: remove the link, keep the text</li>",
        f"<li>Other old Tiki addresses (blog posts, tiki-index.php, ...): <b>{links_in('tiki'):,}</b> → remove the "
        "link, keep the text</li>",
        f"<li>Renamed or missing pages: <b>{links_in('missing'):,}</b> links to {len(missing)} addresses → "
        f"<span class='conf high'>high</span> {missing_by_conf[HIGH]:,} · "
        f"<span class='conf likely'>likely</span> {missing_by_conf[LIKELY]:,} · "
        f"<span class='conf review'>review</span> {missing_by_conf[REVIEW]:,}</li>",
        f"<li>Links to draft (unpublished) pages: <b>{links_in('draft'):,}</b></li>",
        f"<li>Overall: <span class='conf high'>high</span> {confidence[HIGH]:,} · "
        f"<span class='conf likely'>likely</span> {confidence[LIKELY]:,} · "
        f"<span class='conf review'>review</span> {confidence[REVIEW]:,}</li>",
        "</ul>",
        "<p><b>Confidence:</b> <span class='conf high'>high</span> exact match (old page name, exact title, topic "
        "name, or search term); <span class='conf likely'>likely</span> near-identical address or title; "
        "<span class='conf review'>review</span> a guess or no match (needs a person).</p>",
        "<p>For renamed pages, the plan is to also add the old address as a redirect to the new page, so links "
        "from other websites work too.</p>",
    ]
    sections = [
        ('missing', 'Renamed or missing pages', True, True),
        ('search', 'Old Tiki search links (grouped by proposed fix)', False, True),
        ('download', 'Old Tiki file downloads (grouped by matched PDF)', False, True),
        ('tiki', 'Other old Tiki addresses', True, False),
        ('draft', 'Links to draft pages', True, False),
    ]
    for kind, title, show_target, open_ in sections:
        if not by_kind[kind]:
            continue
        parts.append(f"<details{' open' if open_ else ''}><summary>{title} — {links_in(kind):,} links, "
                     f"{len(by_kind[kind])} groups</summary>{_group_rows(by_kind[kind], show_target)}</details>")
    parts.append("</body></html>")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"link_review_{datetime.now():%Y%m%d_%H%M%S}.html"
    out_path.write_text('\n'.join(parts), encoding='utf-8')
    return out_path


def confident_fixes(groups: Dict[Tuple[str, str], TargetGroup]) -> Dict[str, List[dict]]:
    """Fixes safe to apply without review, per source page: high/likely matches to an
    existing page, topic or PDF. Searches (need the /search/ page) and removals are left out."""
    by_page: Dict[str, List[dict]] = defaultdict(list)
    seen = set()
    for group in groups.values():
        for source_slug, target, text, fix in group.link_fixes:
            if fix.confidence not in (HIGH, LIKELY) or fix.action not in ('page', 'topic', 'file'):
                continue
            key = (source_slug, target, text)
            if key in seen:
                continue
            seen.add(key)
            by_page[source_slug].append({
                'target': target, 'text': text, 'new_url': fix.url,
                'confidence': fix.confidence, 'method': fix.method,
            })
    return dict(sorted(by_page.items()))


def search_link_fixes(groups: Dict[Tuple[str, str], TargetGroup]) -> Dict[str, List[dict]]:
    """Old Tiki search links -> /search/?q=<term>, per source page (applied by migration 0014)."""
    by_page: Dict[str, List[dict]] = defaultdict(list)
    seen = set()
    for group in groups.values():
        if group.kind != 'search':
            continue
        for source_slug, target, text, fix in group.link_fixes:
            if fix.action != 'search' or (source_slug, text) in seen:
                continue
            seen.add((source_slug, text))
            by_page[source_slug].append({'target': target, 'text': text, 'new_url': fix.url})
    return dict(sorted(by_page.items()))


def write_fixes_file(groups: Dict[Tuple[str, str], TargetGroup], path: Path) -> Tuple[Path, int]:
    import json

    fixes = confident_fixes(groups)
    count = sum(len(v) for v in fixes.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'generated': datetime.now().isoformat(timespec='seconds'),
        'description': 'High/likely-confidence fixes for broken internal links, applied by migration '
                       '0013_fix_broken_links. Keyed by source page slug.',
        'fixes': fixes,
    }, indent=1, ensure_ascii=False), encoding='utf-8')
    return path, count


def list_attachment_keys_from_s3(env: Dict[str, Optional[str]]) -> List[str]:
    """All object keys under public/attachments/ in the site's S3 bucket (read-only)."""
    import boto3

    s3 = boto3.client(
        's3',
        aws_access_key_id=env.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=env.get('AWS_SECRET_ACCESS_KEY'),
        region_name=env.get('AWS_DEFAULT_REGION') or None,
    )
    keys: List[str] = []
    for page in s3.get_paginator('list_objects_v2').paginate(
        Bucket=env['AWS_STORAGE_BUCKET_NAME'], Prefix='public/attachments/'
    ):
        keys += [obj['Key'] for obj in page.get('Contents', [])]
    return keys


def run_link_review(db_path: Path, output_dir: Path, pdf_keys: Optional[List[str]] = None) -> Path:
    assert db_path.exists(), f"database not found: {db_path}"
    print("\nFinding broken internal links in published pages (read-only)...")
    groups, total = analyze(db_path, pdf_keys)
    out_path = write_report(groups, total, output_dir)
    fixes_path, fix_count = write_fixes_file(groups, out_path.with_name(out_path.stem + '_fixes.json'))
    print(f"  {fix_count:,} confident fixes written to {fixes_path}")
    confidence = Counter()
    for g in groups.values():
        for (_, _, conf, _), n in g.fixes.items():
            confidence[conf] += n
    print(f"  {total:,} broken links; proposed fixes: {confidence[HIGH]:,} high, "
          f"{confidence[LIKELY]:,} likely, {confidence[REVIEW]:,} need review")
    return out_path
