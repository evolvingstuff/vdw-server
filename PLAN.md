# SEO Improvement Plan

Findings from a technical SEO audit of the codebase and the live site (Sept 2026).

Before changing anything, record a Search Console baseline so the effect can be measured:
- Performance → Pages (last 16 months, compare to previous period)
- Page indexing report (also shows whether Google has noticed the www duplicates)

## High priority

### 1. Redirect www to the bare domain
- **Problem:** `https://www.vitamindwiki.com/` serves the whole site with a 200 instead of redirecting,
  so Google can see two copies of every page and split signals between them.
- **Fix:** 301 `www.vitamindwiki.com` → `https://vitamindwiki.com$request_uri`. The HTTPS server
  block is generated in `deployment-manager.py` (around line 3422, `server_names` covers all domains),
  so add a separate www server block there. The HTTP → HTTPS redirect already works.

### 2. Add canonical tags
- **Problem:** No `<link rel="canonical">` anywhere. Besides www, any query-string variant
  (e.g. `/pages/<slug>/?utm=x`) returns a normal 200 page.
- **Fix:** Add `<link rel="canonical" href="https://vitamindwiki.com{{ request.path }}">` to the
  `<head>` in `templates/base.html`, using `SITE_BASE_URL` from settings, not the request host.

### 3. Output meta descriptions
- **Problem:** `templates/base.html` emits no description tag. The `meta_description` block in
  `templates/page_detail.html` is dead code (pages actually render
  `pages/templates/pages/page_detail.html`), and only 1 of 14,773 pages has `meta_description` set.
  Google makes its own snippet, often starting with the study citation.
- **Fix:** Add a `meta_description` block to `templates/base.html`. Use `page.meta_description`
  when set, otherwise generate ~155 characters from `content_text`, skipping leading citation
  text (journal/doi/author lines). Delete the unused `templates/page_detail.html`.

## Medium priority

### 4. Add robots.txt
- **Problem:** `/robots.txt` returns 404.
- **Fix:** Serve a robots.txt that points to the sitemap and disallows `/admin/`, `/search/api/`
  and `/pages/*/preview/`.

### 5. Homepage title and speed
- **Problem:** The homepage `<title>` is "Home - VitaminDWiki", which wastes the most important
  title on the site. The homepage is also the slowest page (~1.8s, versus ~0.17s for article pages).
- **Fix:** Give it a descriptive title and meta description. Profile the homepage view and
  cache it if the time is spent in queries.

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

## Already working well
- HTTP → HTTPS redirect.
- Compressed responses (a 107 KB page is sent as 33 KB).
- Missing trailing slash 301s to the slash version.
- Legacy Tiki and `/posts/` URLs 301 to the right page.
- Missing pages return a real 404.
- The sitemap lists all 14,812 URLs with `lastmod`.
- Article pages respond in ~0.17s.
