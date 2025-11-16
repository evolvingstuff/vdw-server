# Plan: Legacy Alias Redirect Support

## Goal
Load all legacy URL aliases from the database at startup, keep them in memory, and redirect any incoming request that matches a legacy alias (including tiki query-string forms) to the canonical Django page route without issuing per-request database queries.

## Steps
1. **Alias Data Model Analysis**
   - Enumerate how aliases are stored on `Page.aliases` (newline-separated, leading `/`).
   - Define normalization rules so `/foo`, `foo`, and tiki query-string aliases resolve deterministically.
   - Decide canonical redirect target (likely `page_detail`), HTTP status (permanent 301), and collision behavior (log + first-write wins).
   - Success: documented normalization/redirect decisions baked into helper module docstring/tests.

2. **In-memory Alias Cache**
   - Build a `pages/alias_cache.py` helper that loads every `Page` once, parses aliases, and populates fast lookup tables for: plain paths, tiki `page`, and tiki `page_id` parameters.
   - Include a `load_alias_redirects()` function invoked at startup plus a `get_redirect(slug_or_params)` helper for middleware/tests.
   - Guard against bad rows (empty alias strings, whitespace, duplicates) with explicit asserts/logging while still loading the rest.
   - Success: module can be imported without triggering DB hits yet, and calling `load_alias_redirects()` yields deterministic dicts cover all forms.

3. **App Startup Hook & Refresh Capability**
   - Call `load_alias_redirects()` from `PagesConfig.ready()` after signal setup so aliases populate once per process start.
   - Provide a lightweight `reload_alias_redirects()` API (or reuse `load...`) so future admin actions/tests can refresh without restart.
   - Success: running Django shell and importing the helper shows populated cache immediately after startup; no per-request DB queries.

4. **Request Handling Middleware**
   - Introduce a `LegacyAliasRedirectMiddleware` that sits before view resolution, consults the in-memory cache, and issues a `HttpResponsePermanentRedirect` to `reverse('page_detail', args=[slug])` when a match is found.
   - Handle all four forms: `/alias`, `/12345`, `/tiki-index.php?page=alias`, `/tiki-index.php?page_id=12345` (with or without leading `/`).
   - Ensure middleware skips admin/static paths and leaves unmatched requests untouched.
   - Success: manual request objects hitting those paths return immediate 301 responses pointing to `/pages/<slug>/`.

5. **Automated Tests & Docs**
   - Add tests in `pages/tests.py` covering cache normalization and middleware behavior for each alias form plus a no-match case.
   - Update developer docs (README or similar) to mention alias caching behavior and where to adjust it if new aliases are added.
   - Success: tests fail before the feature, pass after, and docs describe the new redirect strategy.
