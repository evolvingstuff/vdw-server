# Prod Hardening Plan

## Goal
Harden production so a bad runtime state is less likely, production 500s are diagnosable, and the admin DB restore flow cannot quietly put the site back online in a broken SQLite state.

## Scope
- Replace the Docker production app server with Gunicorn instead of Django `runserver`
- Add explicit request-boundary error logging plus a custom 500 handler/page
- Harden the admin SQLite restore flow so it fails closed and does not leave the running app serving immediately after a blind DB swap
- Keep manual post-deploy testing as the validation path; no automated smoke-test workflow in this feature

## Workstreams

### 1. Production App Server
- Add Gunicorn to production dependencies
- Change the Docker runtime command to serve `vdw_server.wsgi:application` with Gunicorn on port 8000
- Preserve current Docker/deployment-manager behavior otherwise so option `3` remains a code-only rebuild/restart path

Success criteria:
- Docker build succeeds locally
- The app starts under Gunicorn and serves existing pages/admin routes
- No local-dev workflow is accidentally switched to Gunicorn outside Docker

### 2. Error Visibility
- Add structured logging in Django settings for request failures and app errors to container stdout/stderr
- Add a request/exception logging layer that records method, path, host, remote address, and traceback for unhandled exceptions
- Add `handler500` plus a simple `500.html` template so production users see a stable error page instead of the generic opaque response

Success criteria:
- Unhandled exceptions produce a traceback in Docker logs
- Production 500 responses render through the custom handler
- Existing 404 behavior remains unchanged

### 3. Admin Restore Hardening
- Refactor the admin restore flow to validate the downloaded SQLite backup before putting it live
- Run SQLite integrity validation (`PRAGMA integrity_check`) and basic application invariants after restore
- Keep maintenance mode engaged until validation passes
- Replace the current “swap DB file and keep serving in the same process” behavior with a safer post-restore path:
  - either force a clean Django/Gunicorn restart after restore
  - or fail closed and require an explicit restart before lifting maintenance
- Ensure restore failures roll back cleanly to the pre-restore DB

Success criteria:
- A bad backup never returns the site to public traffic
- A successful restore leaves the app running only after validation and a clean process restart path
- The homepage/site-page invariants still hold after restore

### 4. Tests
- Add coverage for the new Gunicorn/server config where practical
- Add focused tests for custom 500 handling/logging behavior
- Add focused tests for restore validation/rollback behavior and maintenance-lock semantics

Success criteria:
- Relevant test suite passes locally
- New restore-path tests fail for the old unsafe behavior and pass for the hardened flow

### 5. Docs
- Update deployment/ops docs to reflect Gunicorn in production
- Document the hardened restore behavior and any restart expectations/operators steps

Success criteria:
- `DEPLOYMENT-INSTRUCTIONS.md` matches the actual production runtime and restore workflow

## Risks / Decisions To Resolve During Implementation
- Safest mechanism for triggering a clean restart from an admin-initiated restore inside Docker
- How strict the post-restore invariant checks should be without creating false positives
- Whether restore success should redirect immediately or present a maintenance/restart completion screen

## Validation Plan
- Run targeted Django tests for views/middleware/admin restore logic
- Run a local Docker rebuild/start to verify Gunicorn boots correctly
- Manually exercise:
  - homepage
  - a published `/pages/<slug>/` page
  - admin login
  - admin backup/restore failure path

## Out of Scope
- Automated post-deploy smoke tests in `deployment-manager.py`
- Migrating production from SQLite to Postgres
