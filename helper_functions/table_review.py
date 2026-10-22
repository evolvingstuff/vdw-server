"""Preview of the table fixes (phase 1): reads the local SQLite database read-only and
writes an HTML report for review. Nothing in the database is changed.

Phase 2 (a data migration) will apply the same functions:
  - convert_tiki_tables(): rewrites leftover Tiki tables in content_md as markdown tables
  - render_markdown() (which calls prepare_tables_for_rendering): re-renders content_html
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

from helper_functions.markdown import render_markdown
from helper_functions.tables import TableReport, _MD_SEPARATOR_RE, convert_tiki_tables, prepare_tables_for_rendering

SITE_BASE_URL = "https://vitamindwiki.com"


@dataclass
class PageResult:
    page_id: int
    title: str
    slug: str
    tables_expected: int
    tables_before: int
    tables_after: int
    tiki_reports: List[TableReport] = field(default_factory=list)
    markdown_changed: bool = False

    @property
    def url(self) -> str:
        return f"{SITE_BASE_URL}/pages/{self.slug}/"

    @property
    def still_short(self) -> bool:
        return self.tables_after < self.tables_expected


def _table_count(markdown: str) -> int:
    return sum(1 for line in markdown.split('\n') if _MD_SEPARATOR_RE.match(line))


def analyze(db_path: Path) -> List[PageResult]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, title, slug, content_md, content_html FROM posts_post WHERE status = 'published'"
        ).fetchall()
    finally:
        conn.close()

    results = []
    for page_id, title, slug, md, stored_html in rows:
        md = md or ''
        if '|' not in md:
            continue
        prepared = prepare_tables_for_rendering(md)
        converted_md, reports = convert_tiki_tables(md)
        if prepared == md.replace('\r\n', '\n') and not reports:
            continue  # nothing about this page's tables changes
        results.append(PageResult(
            page_id=page_id,
            title=title,
            slug=slug,
            # Count markdown tables before boxed tables are turned into HTML.
            tables_expected=_table_count(converted_md.replace('\r\n', '\n')),
            tables_before=(stored_html or '').count('<table'),
            tables_after=render_markdown(md).count('<table'),
            tiki_reports=reports,
            markdown_changed=converted_md != md,
        ))
    return results


# Routine adjustments that don't need a person to look at them.
_ROUTINE_NOTES = ('heading marker before the table was removed',)


def _warnings(report: TableReport) -> List[str]:
    return [n for n in report.notes if n not in _ROUTINE_NOTES]


def _pre(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


def _page_heading(result: PageResult) -> str:
    return (f'<h3><a href="{html.escape(result.url)}">{html.escape(result.title)}</a> '
            f'<small>(id {result.page_id}; tables {result.tables_before} → {result.tables_after})</small></h3>')


def write_report(results: List[PageResult], output_dir: Path) -> Path:
    converted = [(r, t) for r in results for t in r.tiki_reports if t.status == 'converted']
    skipped = [(r, t) for r in results for t in r.tiki_reports if t.status == 'skipped']
    flagged = [(r, t) for r, t in converted if _warnings(t)]
    still_short = [r for r in results if r.still_short]
    render_only = [r for r in results if not r.tiki_reports and r.tables_after > r.tables_before]

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Table fix preview</title><style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;margin:2em auto;padding:0 16px;color:#222}",
        "pre{background:#f6f8fa;border:1px solid #ddd;padding:8px;white-space:pre-wrap;font-size:12px;max-height:320px;overflow:auto}",
        "table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #bbb;padding:3px 8px;font-size:13px}",
        ".grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;align-items:start}",
        ".note{color:#8a5a00;font-size:13px}.item{border-top:1px solid #ddd;padding:10px 0}",
        "summary{cursor:pointer;font-weight:600;font-size:1.1em;margin:1em 0}",
        "</style></head><body>",
        f"<h1>Table fix preview</h1><p>Generated {datetime.now():%Y-%m-%d %H:%M} from the local database. "
        "Nothing has been changed.</p>",
        "<h2>Summary</h2><ul>",
        f"<li><b>{len(results)}</b> pages whose tables change</li>",
        f"<li>Tables rendering on those pages: <b>{sum(r.tables_before for r in results)}</b> now → "
        f"<b>{sum(r.tables_after for r in results)}</b> after "
        f"(of {sum(r.tables_expected for r in results)} tables found)</li>",
        f"<li>Tiki tables converted to markdown (phase 2 rewrites these in the stored markdown): "
        f"<b>{len(converted)}</b> on {len({r.page_id for r, _ in converted})} pages; "
        f"<b>{len(flagged)}</b> of them flagged for a closer look</li>",
        f"<li>Pages fixed by rendering only (markdown unchanged: tables in boxes, missing blank lines): "
        f"<b>{len(render_only)}</b></li>",
        f"<li>Needs a manual fix: <b>{len(skipped)}</b> skipped Tiki tables, "
        f"<b>{len(still_short)}</b> pages with tables that still don't render</li>",
        "</ul>",
    ]

    parts.append(f"<details open><summary>Needs a manual fix ({len(skipped) + len(still_short)})</summary>")
    for r, t in skipped:
        parts.append(f"<div class='item'>{_page_heading(r)}<p class='note'>Skipped Tiki table: "
                     f"{html.escape(t.reason)}</p>{_pre(t.original[:1500])}</div>")
    for r in still_short:
        parts.append(f"<div class='item'>{_page_heading(r)}<p class='note'>{r.tables_expected - r.tables_after} "
                     f"table(s) still not rendering</p></div>")
    parts.append("</details>")

    def tiki_item(r: PageResult, t: TableReport) -> str:
        notes = ''.join(f"<p class='note'>⚠ {html.escape(n)}</p>" for n in t.notes)
        return (f"<div class='item'>{_page_heading(r)}{notes}<div class='grid'>"
                f"<div><b>Tiki (now)</b>{_pre(t.original)}</div>"
                f"<div><b>Markdown (after)</b>{_pre(t.converted)}</div>"
                f"<div><b>Will look like</b>{render_markdown(t.converted)}</div></div></div>")

    parts.append(f"<details open><summary>Tiki tables flagged for a closer look ({len(flagged)})</summary>")
    parts.extend(tiki_item(r, t) for r, t in flagged)
    parts.append("</details>")
    parts.append(f"<details><summary>All other converted Tiki tables ({len(converted) - len(flagged)})</summary>")
    parts.extend(tiki_item(r, t) for r, t in converted if not _warnings(t))
    parts.append("</details>")

    parts.append(f"<details><summary>Pages fixed by rendering only ({len(render_only)})</summary><ul>")
    parts.extend(f"<li><a href='{html.escape(r.url)}'>{html.escape(r.title)}</a> — tables "
                 f"{r.tables_before} → {r.tables_after}</li>" for r in render_only)
    parts.append("</ul></details></body></html>")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"table_review_{datetime.now():%Y%m%d_%H%M%S}.html"
    out_path.write_text('\n'.join(parts), encoding='utf-8')
    return out_path


def run_table_review(db_path: Path, output_dir: Path) -> Path:
    assert db_path.exists(), f"database not found: {db_path}"
    print("\nAnalyzing tables in published pages (read-only)...")
    results = analyze(db_path)
    out_path = write_report(results, output_dir)
    converted = sum(1 for r in results for t in r.tiki_reports if t.status == 'converted')
    print(f"  {len(results)} pages change; tables rendering {sum(r.tables_before for r in results)} → "
          f"{sum(r.tables_after for r in results)}; {converted} Tiki tables converted; "
          f"{sum(1 for r in results if r.still_short)} pages still need a manual fix")
    return out_path
