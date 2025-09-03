# MeiliSearch Integration Plan

## Overview
Add search functionality to VDW server using MeiliSearch, keeping SQLite database and adding search as a separate service.

## Phase 1: Setup & Basic Integration

### 1.1 Dependencies & Settings
- Install `meilisearch` Python client
- Add MeiliSearch config to `vdw_server/settings.py`:
  - Local: `http://localhost:7700`
  - Use environment variables for host/key
- Create `posts/search.py` module for all search operations

### 1.2 Document Structure
Index posts with these fields:
- `id`: Post.pk
- `title`: Post.title  
- `slug`: Post.slug
- `content`: Post.content_md (markdown content)
- `tags`: List of tag names from Post.tags
- `created_date`: For filtering/sorting
- `status`: Only index published posts

### 1.3 Index Management
- Configure searchable attributes: `title`, `content`, `tags`  
- Configure filterable attributes: `tags`, `created_date`, `status`
- Set ranking rules for relevance

## Phase 2: Sync & Management Commands

### 2.1 Sync Functions
- `sync_post_to_search(post)`: Add/update single post
- `remove_post_from_search(post_id)`: Remove single post  
- Handle MeiliSearch connection errors (fail fast per CLAUDE.md)

### 2.2 Management Command
- `python app.py search_rebuild`: Bulk sync all published posts
- Use `tqdm` for progress tracking (like conversion script)
- Clear existing index first, then batch upload

### 2.3 Django Signals
- `post_save` signal: Auto-sync on post create/update
- `post_delete` signal: Auto-remove from search
- Only sync published posts (check `status` field)

## Phase 3: Search API

### 3.1 Search View
- Add search endpoint to `posts/views.py`
- Accept query parameter, return JSON
- Configure MeiliSearch search options:
  - Highlighting for result snippets
  - Limit results (default 20)
  - Filter by published status

### 3.2 URL & Integration
- Add search URL to `posts/urls.py` 
- Integrate with existing admin interface or create simple search page
- Keep it simple - just query input and results list

## Constraints Based on Codebase

### Existing Structure
- Posts app already handles all content
- Use existing Post model and admin interface
- Work with current SQLite + Django setup
- Follow existing patterns (like `conversion_md_to_db_v1.py`)

### Technical Constraints  
- Use `app.py` instead of `manage.py` for commands
- Follow FAIL FAST AND LOUD principle - no soft error handling
- Use existing import patterns (all imports at top)
- Must work with current Post.tags ManyToMany structure

### Simplified Scope
- Only index published posts (Post.status == 'published')
- Use existing markdown content as-is (no special processing)
- Basic search only (no facets, autocomplete, etc.)
- Single search index for posts

## Implementation Steps
1. Install MeiliSearch locally with Docker
2. Add Python client and settings  
3. Create search module with sync functions
4. Build management command for bulk sync
5. Add Django signals for auto-sync
6. Create basic search view and template

## Success Criteria
- All 14,412 published posts indexed in MeiliSearch
- Search works from Django admin or simple interface
- New/updated posts automatically sync to search
- Search returns relevant results with highlighting