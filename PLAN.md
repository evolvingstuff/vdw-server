# Search Refactoring and Global Search Bar Plan

## Overview
First refactor search functionality out of the `posts` app into a dedicated `search` app, then implement a global search bar that works across all page types.

## Phase 1: Extract Search from Posts App

### Why Refactor First
- Search will be used across multiple content types (posts, tags, future content)
- Homepage and other non-post pages need search functionality
- Search is a distinct feature that shouldn't be tied to posts
- Moving it later will be more complex as the codebase grows

### Implementation Steps

#### 1. Create Search App
- Run `python manage.py startapp search`
- Add `'search'` to `INSTALLED_APPS` in settings
- Create `search/templates/search/` directory structure

#### 2. Move Search Logic
- Move `posts/search.py` → `search/search.py`
- Move search views from `posts/views.py` → `search/views.py`
- Update imports in moved files
- Update any references to search functions

#### 3. Move Templates
- Move `posts/templates/posts/search.html` → `search/templates/search/search.html`
- Update template extends/includes if needed
- Ensure template still works with new location

#### 4. Update URL Configuration
- Move search URL patterns from posts to search app
- Create `search/urls.py` with search patterns
- Include search URLs in main `vdw_server/urls.py`
- Update any hardcoded URL references

#### 5. Test Existing Search
- Verify `/search/` page still works
- Verify `/search/api/` endpoint still works
- Verify search results display correctly
- Fix any broken imports or references

## Phase 2: Global Search Bar Implementation

### Requirements
- Fixed/sticky positioning at top of all pages
- Real-time search with dropdown results
- Reuse refactored search API
- Generic component for all page types
- Keep existing search page intact

### Implementation Steps

#### 1. Create Base Template Structure
- Create project-level `templates/` directory
- Create `templates/base.html` as main base template
- Move common styles and structure from `posts/templates/posts/base.html`
- Update existing templates to extend new base

#### 2. Create Global Search Component
- Create `templates/components/global_search_bar.html`
- Include search input and dropdown container
- Add component to main base template

#### 3. Add Fixed Positioning CSS
- Position: fixed, top: 0, full width
- High z-index, consistent styling
- Add body padding-top to account for fixed bar
- Responsive design for mobile

#### 4. Implement Dropdown Functionality
- JavaScript for real-time search
- Debounced input handling
- Fetch from refactored search API
- Dropdown results display
- Keyboard navigation (arrows, enter, escape)
- Click outside to close

#### 5. Integration Testing
- Test on post list pages
- Test on post detail pages
- Test on future tag pages
- Test on existing search page (no conflicts)
- Test mobile responsiveness
- Test accessibility

## File Structure After Refactoring

```
search/
├── __init__.py
├── apps.py
├── search.py           # Moved from posts/search.py
├── views.py           # Search views moved from posts/views.py
└── templates/search/
    └── search.html    # Moved from posts/templates/posts/search.html

templates/              # New project-level templates
├── base.html          # Main base template
└── components/
    └── global_search_bar.html

posts/templates/posts/
├── base.html          # Updated to extend main base.html
├── post_list.html
└── post_detail.html
```

## Benefits of This Approach
1. **Clean separation**: Search logic separated from posts
2. **Scalability**: Easy to extend search to other content types
3. **Maintainability**: Search changes don't affect posts app
4. **Reusability**: Global search bar can be used anywhere
5. **Future-proof**: Foundation for tags, categories, homepage search