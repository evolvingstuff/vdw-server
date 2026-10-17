# Fix: hundreds of tables not rendered

Status: **investigated, not started**. Reported by Dad as "100's of tables not parsed" (ASAP).

## Examples from Dad

- [Magnesium is vital to Vitamin D in 4 places (maybe 8)](https://vitamindwiki.com/pages/magnesium-is-vital-to-vitamin-d-in-4-places-maybe-8/): cause **B** below
- [50,000 IU Vitamin D weekly was the most popular for Breast Cancer](https://vitamindwiki.com/pages/50000-iu-vitamin-d-weekly-was-the-most-popular-for-breast-cancer/): cause **A** below

Neither page has a `<table>` in its rendered HTML; the table shows as raw text with pipes.

## What we found (local DB copy, Oct 2026)

About **400 published pages** are affected, from three separate causes.

| | Cause | Pages | Proposed fix |
|---|---|---|---|
| **A** | A valid markdown table sits inside an HTML box (`<div class="border" ...>...</div>`, sometimes `<span>`). markdown2 treats everything from an opening block tag to its closing tag as raw HTML and does not process markdown inside it, so the table is never converted. | ~130 | Add `markdown="1"` to opening `div`/`span`/`center`/`blockquote` tags before rendering (the `markdown-in-html` extra is already enabled). Tested on the local DB: fixes ~129 pages. |
| **B** | Tiki wiki tables never converted by the migration: `\|\|cell\| cell` rows separated by line breaks (in practice by blank lines), the whole table wrapped in `\|\|...\|\|`. | 118 pages, 129 tables | Convert Tiki tables to markdown tables in the renderer; the first row becomes the header row. |
| **C** | Tables half-converted by the migration: rows separated by blank lines, some rows without pipes, single-cell "callout" tables, mixed formats. Some are inside boxes too. | ~140 | Converter for the common patterns, plus a report listing the pages it can't handle safely, for Dad to fix by hand. |

### Raw examples

Cause B (magnesium page):

```
**Table. Recommended Daily Allowance of Magnesium** ||Age| Male| Female| Pregnancy

<6 mo| 30 mg| 30 mg

7-12 mo| 75 mg| 75 mg
...
>51 y| 420 mg| 320 mg|| **a Recommended daily allowance for females who are not pregnant...
```

Other Tiki shapes seen: tab-separated cells (`US Chamber of Commerce\t|$941,835,680`), HTML spans inside cells, two-column tables such as `||All Cause|1.71 X\n\nHeart|1.39 X||`.

Cause A (inside a yellow box):

```
<div class="border" style="background-color:#FF9;padding:15px;...">
Magnesium Chloride containing 386 mg Mg
...

| | | |
| --- | --- | --- |
|  | Mg | Control  |
| Systolic blood pressure   | -2 mm  |   * 4 mm |
...
</div>
```

Rendered on its own, this table works; with the closing `</div>` after it, it doesn't.

Cause C (half-converted):

```
BMI| 19|16

Use of Vitamin D|23%| 88%
| |
| --- |
|  **Note: Low vitamin D levels may be associated with dark skins**  |
| --- |
```

### Things that did *not* help

- Blank lines around markdown tables on their own: fixed 0 pages.
- Re-rendering the stored markdown with the current renderer: fixed 0 pages, so it isn't stale HTML.
- `markdown="1"` plus blank lines: ~136 fixed. The remaining ~138 are cause B/C shapes.

## Why this needs a database change (unlike the heading and DOI fixes)

The heading and DOI fixes run on stored HTML at display time. That isn't possible here, because the table isn't in the stored HTML at all: `content_html` has to be **re-rendered from `content_md`** for the affected pages.

## Proposed plan

1. **Renderer fixes** (`helper_functions/markdown.py`, `render_markdown`), so pages saved from now on are correct:
   - add `markdown="1"` to wrapper tags (A)
   - convert Tiki `||...||` tables to markdown tables (B)
   - handle the common half-converted shapes (C)
2. **One-time data migration** that re-renders `content_html` and `content_text` for affected pages only. It runs automatically at container start on the next option 3 deploy, like `0011_page_status_recent_cover`.
   - Use `QuerySet.update()`, so `modified_date`/`public_modified_date` don't change and no save signals fire.
   - **Don't modify `content_md`.** Dad's markdown stays as-is, so the change can always be re-rendered or reverted.
   - Take a manual S3 backup (admin backup button) just before deploying.
3. **Report** of pages the converter can't handle confidently (cause C leftovers): a CSV or a deployment-manager preview option, for Dad to fix by hand.
4. **Before/after check** on the local DB copy: count of `<table>` per page vs markdown table separators per page, and a visual check of a sample from each cause.

Suggested order: A + B first (well understood), then C with the report.

## Open questions

1. OK to run a one-time data migration on the server that re-renders stored HTML for ~400 pages? (Markdown untouched; dates untouched.)
2. Tiki tables: fix display only (renderer), or also convert the stored **markdown** so Dad sees normal markdown tables when editing? Leaning: display only for now (lower risk).
3. Tiki tables have no header concept: is "first row = header" right for most of them? Spot-check before deciding.
4. Single-cell "callout" tables (`| |` / `| --- |` / `| **Note** |`): render as a table, or convert to a styled box?

## Reproducing the counts

The investigation scripts (run locally against `db.sqlite3`):

- **Pages with unrendered markdown tables:** count lines matching
  `^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*\|[ \t|:-]*$` in `content_md`, then compare against the number of `<table` in `content_html`.
- **Tiki tables:** blocks matching `\|\|(?:(?!\|\|).)*?\|(?:(?!\|\|).)*?\|\|` (DOTALL) that contain a newline.
- **Testing a fix:** apply the preprocessing to `content_md`, call `render_markdown()`, and compare `<table` counts again.
