# Editable Pages Implementation Plan

## Overview
Implement an editable homepage and static pages system using a shared ContentBase abstract model that provides markdown editing functionality to both Posts and Pages. Include admin redirect functionality so clicking "Admin" on any page takes you directly to edit mode for that page.

## Architecture Decision
- **ContentBase**: Abstract model with markdown/html/text fields and conversion logic
- **Post**: Inherits from ContentBase (existing blog posts)
- **Page**: Inherits from ContentBase (static pages like homepage, about, contact)
- **Homepage**: Special singleton page (only one can exist)
- **Admin Redirect**: Extend existing post redirect system to work with pages

## Implementation Steps

### Step 1: Create ContentBase Abstract Model
**File**: `posts/models.py`

1. Extract common fields from Post model:
   - content_md, content_html, content_text, character_count fields

2. Extract markdown processing logic:
   - Markdown to HTML conversion
   - HTML stripping for plain text
   - Character count calculation

3. Create abstract base class with save method containing shared logic

### Step 2: Refactor Post Model
**File**: `posts/models.py`

1. Remove fields that are now in ContentBase
2. Update Post to inherit from ContentBase
3. Update Post.save() to call parent save while keeping Post-specific logic

### Step 3: Create Page Model
**File**: `posts/models.py`

1. Create Page model inheriting from ContentBase
2. Add page-specific fields: title, slug, page_type, is_published, meta_description, modified_date
3. Define PAGE_TYPES choices including homepage, about, contact, custom
4. Implement save method with:
   - Homepage singleton enforcement (only one homepage can exist)
   - Auto-slug generation from title
   - Special slug handling for homepage
5. Add get_absolute_url method with homepage special case
6. Add database constraint for homepage uniqueness
7. Add appropriate Meta class with ordering

### Step 4: Extend Admin Redirect System for Pages
**Files**: JavaScript in templates, middleware

1. **Update JavaScript** (in global search bar template):
   - Extend existing post redirect logic to detect page URLs
   - Handle homepage (/) as special case
   - Handle other page URLs (/about/, /contact/, etc.)
   - Set appropriate next parameter for page editing

2. **Update Middleware**:
   - Extend existing AdminPostRedirectMiddleware
   - Add page URL pattern detection
   - Add Page model lookup by slug
   - Redirect to page edit URL after admin login
   - Handle homepage special case (slug vs URL mismatch)

3. **URL Pattern Matching**:
   - Homepage: "/" → redirect to homepage edit
   - Other pages: "/slug/" → redirect to page edit by slug
   - Maintain existing post redirect functionality

### Step 5: Create and Run Migrations
**Commands**: makemigrations and migrate

**Migration Considerations**:
1. ContentBase is abstract (no table created)
2. Post model fields moved but data preserved
3. New Page table created
4. Homepage uniqueness constraint added

### Step 6: Set Up Page Admin Interface
**File**: `posts/admin.py`

1. Create PageAdminForm with markdown textarea widget
2. Create PageAdmin with:
   - List display including page type, slug, status, character count
   - Filtering by page type and publication status
   - Search functionality across title and content
   - Fieldsets for organized editing (Page Info, Content, SEO, Statistics)
   - Readonly fields for calculated values
   - Live link functionality
   - Character count display with custom method
   - Date formatting for clean display

3. Add special homepage protection:
   - Prevent homepage deletion
   - Make homepage slug and type readonly
   - Prevent multiple homepage creation

### Step 7: Add URL Routing
**File**: `vdw_blog/urls.py`

1. Add page URL patterns after existing routes but before catch-all
2. Homepage route at root path
3. Generic page route with slug parameter
4. Maintain existing post routing under /posts/
5. Ensure proper order to avoid conflicts

### Step 8: Create Views
**File**: `posts/views.py`

1. **Homepage view**:
   - Look up homepage page object
   - Fallback to static template if no homepage exists
   - Pass page context to template

2. **Page detail view**:
   - Look up page by slug
   - Handle 404 for unpublished pages
   - Redirect homepage slug access to root URL
   - Pass page context to template

### Step 9: Create Templates
**File**: `templates/page_detail.html`

1. Create template extending base layout
2. Handle both homepage and regular pages
3. Conditional title display (no title for homepage)
4. Render markdown content as HTML
5. Add staff edit links for admin users
6. Include proper meta tags for SEO

### Step 10: Create Initial Homepage
**Options**: Management command or manual creation

1. Create management command to set up initial homepage
2. Handle case where homepage already exists
3. Set reasonable default content with welcome message and posts link
4. Make it idempotent (safe to run multiple times)

### Step 11: Update Navigation (Optional)
**File**: `templates/base.html`

1. Consider adding context processor for navigation pages
2. Update navigation to show published pages
3. Maintain existing Home and All Posts links

## Admin Redirect Implementation Details

### JavaScript Updates
1. Detect current URL pattern (root, page slug, post slug)
2. Map URL patterns to appropriate admin redirect targets
3. Handle edge cases (homepage vs other pages)
4. Maintain existing post functionality

### Middleware Updates
1. Extend existing middleware class
2. Add page pattern matching alongside post patterns
3. Add Page model import and lookup logic
4. Handle slug-to-edit-URL translation for pages
5. Maintain error handling and fallbacks

### URL Pattern Handling
- `/` (homepage) → `/admin/posts/page/{homepage_id}/change/`
- `/about/` → `/admin/posts/page/{about_id}/change/`
- `/posts/slug/` → `/admin/posts/post/{post_id}/change/` (existing)
- Other paths → default admin behavior

## Testing Plan

1. **Model Tests**:
   - ContentBase functionality shared correctly
   - Homepage singleton enforcement works
   - Page slug generation and uniqueness
   - Character count calculation accuracy

2. **Admin Tests**:
   - Page creation and editing interface
   - Homepage protection (no delete, no duplicate)
   - Admin form validation and widgets

3. **View Tests**:
   - Homepage renders correctly
   - Page detail views work
   - 404 handling for unpublished pages
   - URL redirects work correctly

4. **Admin Redirect Tests**:
   - Homepage admin redirect works
   - Other page admin redirects work
   - Existing post redirects still work
   - Edge cases and error handling

5. **Integration Tests**:
   - End-to-end page creation and editing
   - Admin redirect flow from different page types
   - Markdown rendering consistency between posts and pages

## Rollback Plan

If issues arise:
1. Git reset to undo code changes
2. Database restore if migrations were applied
3. Revert to static homepage template
4. Disable middleware if redirect issues occur

## Success Criteria

- [ ] Homepage is editable in admin interface
- [ ] Can create additional pages (about, contact, etc.)
- [ ] Only one homepage can exist (enforced)
- [ ] Markdown editing works identically to posts
- [ ] URLs work correctly (/, /about/, etc.)
- [ ] Character count displays in admin
- [ ] Posts continue working exactly as before
- [ ] Admin redirect works for homepage and other pages
- [ ] Admin redirect preserves existing post functionality
- [ ] No data loss from existing posts or admin redirects