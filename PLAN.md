# SEO Improvement Plan

Findings from a technical SEO audit of the codebase and the live site (Sept 2026).

Before changing anything, record a Search Console baseline so the effect can be measured:
- Performance → Pages (last 16 months, compare to previous period)
- Page indexing report (also shows whether Google has noticed the www duplicates)

## High priority

### 1. Redirect www to the bare domain — DONE (branch `seo-fixes`)
- **Problem:** `https://www.vitamindwiki.com/` serves the whole site with a 200 instead of redirecting,
  so Google can see two copies of every page and split signals between them.
- **Fix:** 301 `www.vitamindwiki.com` → `https://vitamindwiki.com$request_uri`. The HTTPS server
  block is generated in `deployment-manager.py` (around line 3422, `server_names` covers all domains),
  so add a separate www server block there. The HTTP → HTTPS redirect already works.
- **Done:** alternate domains get their own HTTPS server block that 301s to the primary; port 80
  redirects straight to the primary. Goes live on the next code deploy (option 3), which
  regenerates the nginx config.

### 2. Add canonical tags — DONE (branch `seo-fixes`)
- **Problem:** No `<link rel="canonical">` anywhere. Besides www, any query-string variant
  (e.g. `/pages/<slug>/?utm=x`) returns a normal 200 page.
- **Fix:** Add `<link rel="canonical" href="https://vitamindwiki.com{{ request.path }}">` to the
  `<head>` in `templates/base.html`, using `SITE_BASE_URL` from settings, not the request host.
- **Done:** `vdw_server.context_processors.seo` builds the URL (keeps `?page=N`, drops other
  query strings); 404 pages omit it.

### 3. Output meta descriptions — DONE (branch `seo-fixes`)
- **Problem:** `templates/base.html` emits no description tag, so the `meta_description` block in
  `templates/page_detail.html` (used by the homepage and site pages) is never output, and article
  pages had none. Only 1 of 14,773 pages has `meta_description` set. Google makes its own snippet,
  often starting with the study citation.
- **Done:** `helper_functions/seo.py` uses `meta_description` when set, otherwise the first prose
  paragraph (skipping headings, citations, author and affiliation lines, link lists, Tiki leftovers),
  truncated to 155 characters. ~82% of pages get one; the rest get no tag, so Google picks a snippet.
- **To do (Dad):** the homepage's hand-set description is "Welcome to VitaminDWiki". Replace it
  in admin → Site pages → Home → Meta description.

### 12. Broken internal links
- **Problem:** 2,193 links on 1,733 published pages point to `/pages/<slug>/` URLs that don't exist
  (all 404 on the live site). They waste crawl budget, dead-end readers, and are a low-quality signal.
  - 708 are Tiki search links, `[PTSD](/pages/search-results/)`: the link text is the search term.
  - 335 are Tiki file downloads, `/pages/tiki-download-filephp/`, plus ~53 other Tiki URLs
    (`tiki-view-blog-postphp`, `tiki-indexphp`, `moved58`). The migration lost the original target.
  - ~1,100 links go to 466 slugs of renamed or never-migrated pages, e.g.
    `overview-evidence-for-vitamin-d` (27 links). ~100 have an obvious match (typos such as
    `vitamin-d-is-only-of-only-2-ways…` → `…is-one-of-only-2-ways…`); the rest need judgement.
- **Fix:** a deployment-manager option with a preview → review CSV → apply flow, like the title work:
  - Search links → the matching tag page if one exists, else site search.
  - Download links → match against CloudFront attachments by link text/filename where possible,
    otherwise unlink (keep the text).
  - Missing pages → suggest the closest existing slug (checking aliases too); apply only approved rows.
  - Where a missing slug has a clear replacement, also add it to that page's `aliases`, so outside
    links and bookmarks redirect as well.

## Medium priority

### 4. Add robots.txt — DONE (branch `seo-fixes`)
- **Problem:** `/robots.txt` returns 404.
- **Fix:** Serve a robots.txt that points to the sitemap and disallows `/admin/`, `/search/api/`
  and `/pages/*/preview/`.

### 5. Homepage title and speed — title DONE (branch `seo-fixes`), speed to do
- **Problem:** The homepage `<title>` is "Home - VitaminDWiki", which wastes the most important
  title on the site. The homepage is also the slowest page (~1.8s, versus ~0.17s for article pages).
- **Fix:** Give it a descriptive title and meta description. Profile the homepage view and
  cache it if the time is spent in queries.
- **Done:** title comes from `HOMEPAGE_TITLE` in `vdw_server/settings.py` (edit the wording there).
- **Update:** the homepage responded in 0.15s on a later check, so the 1.8s was probably a cold
  cache. Low priority unless it recurs.

### 6. Image alt text and lazy loading
- **Problem:** Nearly all images have `alt="image"` (16 of 17 on a sample page), and none use
  `loading="lazy"`.
- **Fix:** Add `loading="lazy"` in the markdown renderer (`helper_functions/markdown.py`). For alt
  text, derive it from the image filename or nearby caption, or leave it empty for decorative
  images; `alt="image"` is worse than either.

### 7. Show who is behind the site (E-E-A-T)
- **Problem:** No author, no About page (the homepage is the only site page), no structured data.
  For health content, Google looks for evidence of who writes it and their background.
- **Fix:** Needs input from Dad on what to show. Then add an About page, author info on pages,
  and JSON-LD `Article` markup with author, `datePublished` (`created_date`) and `dateModified`
  (`public_modified_date`).

### 13. Out-of-range page numbers return 200
- **Problem:** `/pages/?page=99999`, `/tags/vitamin-d/?page=999` and `/pages/?page=abc` return a
  normal page (a copy of the last page) instead of 404, giving endless duplicate URLs.
  There are 741 real `/pages/` list pages.
- **Fix:** return 404 for non-numeric or out-of-range `page` values in the page list, recent and
  tag views (`Paginator.page()` rather than `get_page()`).

### 14. Tag pages missing from the sitemap
- **Problem:** the 480 tag pages (`/tags/<slug>/`) are good topic hubs but aren't in the sitemap
  and have no meta description.
- **Fix:** add tag pages to the sitemap generation; give them a description such as
  "N VitaminDWiki pages about <tag>: …".

### 15. Second `<h1>` inside page content
- **Problem:** 1,810 pages have `<h1>` headings in their content (markdown `# Heading`) as well as
  the page title `<h1>`.
- **Fix:** shift content headings down one level when rendering markdown (`#` → `<h2>`, and so on),
  then re-render stored HTML.

## Worth reviewing

### 8. Thin pages
- 319 published pages have fewer than 300 characters of text; 1,386 have fewer than 1,000.
- Very short pages can weigh on site-wide quality. Review them and decide page by page whether to
  merge into a related page, expand, or `noindex`.

### 9. Duplicate titles
- 11 titles are shared by more than one published page. Make each unique.

### 10. Long titles
- About half of titles run past ~65 characters and get cut off in results.
- Dropping the " - VitaminDWiki" suffix from article `<title>`s would free 15 characters. Minor.

### 11. Overclaiming titles (in progress)
- LLM review tool: `deployment-manager.py` option 20 (`helper_functions/title_review.py`), which
  writes CSVs to `tmp/title_review/`. The latest prompts rewrite ~9% of titles (~1,400 site-wide).
- Next: run `all` with `gpt-5.6-sol` and review the CSV. Then build the apply phase:
  - An `approve` column in the CSV; edited `suggested_title` values are respected.
  - Title update plus new slug; old slug appended to `aliases` for a 301.
  - Rewrite internal links and link text that matches the old title.
  - Freeze each page's current derived tags.
  - Change log to allow a revert.
- Consider changing titles first and slugs in a later step, so any traffic change can be traced
  to its cause.
- Expected effect on rankings is modest; the main benefit is accuracy and credibility.

### 16. Caching headers and image weight
- CSS/JS under `/static/` and CloudFront images are sent without `Cache-Control`, so returning
  visitors re-check them. Add long cache lifetimes in nginx for `/static/` and on the CloudFront
  distribution. Some images are large (e.g. a 357 KB diagram); consider resizing or compressing.

### 17. Public "Admin" link
- Every public page shows an "Admin" link in the header. Harmless, but visitors don't need it;
  show it only to logged-in staff.

## Already working well
- HTTP → HTTPS redirect.
- Compressed responses (a 107 KB page is sent as 33 KB).
- Missing trailing slash 301s to the slash version.
- Legacy Tiki and `/posts/` URLs 301 to the right page.
- Missing pages return a real 404.
- The sitemap lists all 14,812 URLs with `lastmod`.
- Article pages respond in ~0.17s.
- All 14,812 sitemap URLs are unique and on the canonical host; a random sample of 25 all return 200.
- Missing pages and tags return a real 404; page previews require an admin login.
- Security headers (HSTS, `X-Frame-Options`) are in place.
