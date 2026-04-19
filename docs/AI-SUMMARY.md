# AI-SUMMARY

## Project: VDW Server
Django 5.2 site that manages long-form content and static pages with Markdown-to-HTML processing, MeiliSearch integration, and S3-backed media.

## Architecture
- `vdw_server/`: global settings, URL routing, custom middleware, storage config, 404 view
- `core/`: abstractions like `ContentBase` that convert Markdown into HTML/plain text on save
- `pages/`: published long-form page models, admin customizations, public views (including recent-updates list), media upload endpoints
- `site_pages/`: singleton site chrome pages (homepage/about/etc.) with slug enforcement
- `tags/`: tagging model plus tag-filtered list views
- `search/`: MeiliSearch client helpers, REST API, and templates for search UX
- `templates/`, `posts/templates/`, `tags/templates/`: site-wide layout and per-app pages
- `helper_functions/`: local utilities (e.g., auto-start MeiliSearch for dev)
- `pages/alias_cache.py` + `LegacyAliasRedirectMiddleware`: load legacy aliases at startup and issue 301 redirects to `/pages/<slug>/`
- `vdw_server/not_found_suggestions.py`: in-memory 404 suggestion index for published `Page` + `SitePage` titles/slugs
- Scripts (`conversion_md_to_db.py`, `update_database.py`, `deployment-manager.py`): data import/deployment helpers

## Design
- Pattern: abstract `ContentBase` model centralizes Markdown (`markdown2` extras: footnotes, fenced code) → HTML/text caching
- Storage: Django-Storages S3 backend enforced; media uploads crash if backend mismatch
- Search: MeiliSearch client wrappers encapsulate index config, ranking, and CRUD; production deploys persist Meili data under `/app/data/meilisearch` instead of Docker root storage
- Middleware: `AdminPageRedirectMiddleware` rewrites admin redirects to edit concrete models; `LegacyAliasRedirectMiddleware` also warms alias + 404 caches on startup
- 404 handling: custom handler ranks cached title/slug matches with token+trigram scoring; no DB scan on steady-state misses
- Error handling philosophy: internal bugs crash immediately; only external I/O gets validation

## Workflows
- Published page render: URL in `vdw_server/urls.py` → `pages.views.page_detail` → Markdown HTML decorated with file icons → template render
- Site page render: URL tail → `site_pages.views.site_page_detail` (or homepage) → shared `page_detail.html`
- Markdown preview: admin JS hits `pages.views.preview_markdown` → `markdown2` render → JSON response
- File upload: staff POST to `pages.views.upload_media` → content-type validation → S3 storage → URL returned
- Admin edit protection: `pages/static/pages/admin/form_edit_guard.js` (loaded by `pages.admin.PageAdmin` + `site_pages.admin.SitePageAdmin`) → beforeunload/navigate prompt + localStorage draft restore
- Admin copy links: `pages/admin.py` + `pages/static/pages/admin/copy_page_link.js` → copy Markdown (`[title](url)`) or HTML (`<a href="url">title</a>`) to clipboard (also used by `site_pages/admin.py`)
- Admin bulk tagging: Pages changelist action → `pages.admin.PageAdmin.add_tags_to_selected` → confirmation screen shows count + short preview, keeps Django's confirmation POST valid for `select_across`, then batch-adds tags across the filtered queryset and mirrors them into `derived_tags`
- Admin page search: `pages.admin.PageAdmin.get_search_results` → slugified title-only phrase match at the start of a word (`thyroid` matches `Thyroid Support`, not `Hypothyroidism`); keeps Django admin date/tag filters separate from visitor search ranking
- Search: frontend query → `search.views.search_api` (`limit`+`offset`, capped at 1000) → MeiliSearch (`search/search.py`) → hits + `totalHits` (shown as `1000+` when ≥1000)
- Most-recent index: `GET /pages/recent/` → `pages.views.recent_page_list` → latest 150 published pages by `modified_date` (display date `MM/YYYY`)
- Print output: shared `templates/base.html` print CSS removes fixed UI (header, TOC, scroll controls), sets page margins, and appends print metadata (URL/date) through `templates/components/print_page_metadata.html`
- Smart 404 flow: unmatched request → `vdw_server.views.custom_page_not_found` → `vdw_server.not_found_suggestions.get_not_found_suggestions()` → styled `templates/404.html` with likely matches and CTA to `/pages/recent/`
- Render-time legacy cleanup: `templates/base.html` JS rewrites bracketed legacy Tiki page-id text to clickable aliases, strips malformed `#F00 ... RCT` markup artifacts into a red `RCT` label + clean title links, normalizes stray `*`/macro artifacts, restores missing spacing around legacy inline citation links, converts `{BOX(...)} ... {BOX}` blocks into bordered containers, and calls `pages/static/pages/js/legacy_box_rendering.js` to reconstruct broken markdown-like tables/lists/hr content inside legacy HTML boxes before making markdown images open in a new tab.
- Data import: `conversion_md_to_db.py` wipes DB, runs migrations, seeds pages/site pages/tags from Markdown dumps, rebuilds search index

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export AWS_ACCESS_KEY_ID=...  # plus AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME, AWS_DEFAULT_REGION
export MEILISEARCH_MASTER_KEY=...  # and optional MEILISEARCH_URL
export DJANGO_DEBUG=true  # local runserver needs DEBUG on to serve admin/static assets
python manage.py runserver  # auto-starts MeiliSearch locally if available
```

## Quick Ref
- Settings: `vdw_server/settings.py` (apps, markdownx config, Meili env)
- 404 suggestion cache: `vdw_server/not_found_suggestions.py#L1`
- Markdown pipeline: `core/models.py#L5` (`ContentBase.save`)
- Published pages: `pages/models.py#L1`, `pages/views.py#L1`
- Site page singleton rules: `site_pages/models.py#L1`
- S3 media upload flow: `pages/views.py#L74`
- Search client & index setup: `search/search.py#L1`
- Admin redirect middleware: `vdw_server/middleware.py#L1`
- Legacy alias redirect flow: `pages/alias_cache.py`, `vdw_server/middleware.py#L1`
- Frontend post-processing fixes: `templates/base.html` + `pages/static/pages/js/legacy_box_rendering.js` (URL linkify, legacy Tiki bracket linkify, RCT cleanup, legacy box/hr conversion, box markdown reconstruction, inline-link spacing repair, clickable images)
- Print metadata partial: `templates/components/print_page_metadata.html`
- Bulk import script: `conversion_md_to_db.py#L1`
