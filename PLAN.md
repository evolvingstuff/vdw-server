# Markdown to Database Conversion Plan

## Overview
Import all markdown posts from `../vdw-conversion/posts/` into the SQLite database, extracting frontmatter metadata and preserving the markdown content.

## Data Mapping

### From Frontmatter (JSON):
- `title` -> Post.title
- `slug` -> Post.slug  
- `date` -> Post.created_date
- `tags` -> Create/link Tag objects via Post.tags (ManyToMany)
- `categories` -> Also add as tags (categories are just tags in our schema)
- `tiki_page_id` -> Post.original_page_id
- `aliases` -> Post.aliases (newline-separated)
- Full JSON frontmatter -> Post.front_matter (for debugging)

### From File Content:
- Markdown content (after frontmatter) -> Post.content_md
- Auto-generated from content_md -> Post.content_html (using same markdown2 method as admin preview)
- Auto-generated from content_html -> Post.content_text (stripped HTML, same as admin save)

### Default Values:
- Post.status -> 'published' (all imported posts are published)
- Post.modified_date -> auto_now (Django handles this)
- Post.meta_description -> empty (can be populated later)
- Post.notes -> empty
- Post.derived_tags -> copied from tags (until ontology implemented)

## Schema Changes Required

1. **Add front_matter field to Post model**
   - Add `front_matter = models.TextField(blank=True, null=True, editable=False)` 
   - No migration needed - database will be deleted and recreated

## Implementation Steps

1. **Setup Django Environment**
   - Import Django settings
   - Setup Django ORM
   - Import Post and Tag models

2. **Clear Existing Data**
   - Delete existing SQLite database file (db.sqlite3)
   - Run Django migrations to recreate fresh database from scratch

3. **Parse Markdown Files**
   - Iterate through all `.md` files in `../vdw-conversion/posts/`
   - For each file:
     - Read file content
     - Extract JSON frontmatter (between first `{` and `}` block)
     - Extract markdown content (everything after frontmatter)
     - Remove any Hugo shortcodes like `{{< toc >}}`

4. **Clean Markdown Content**
   - Remove Hugo shortcodes (e.g., `{{< toc >}}`)
   - Decode Unicode escape sequences
   - Clean up any unwanted HTML spans/styling

5. **Process Tags**
   - For each unique tag from both `tags` and `categories`:
     - Create or get Tag object
     - Auto-generate slug from tag name

6. **Create Post Objects**
   - Create Post instance with mapped fields
   - Save post (triggers auto-generation of HTML and text)
   - Link tags via ManyToMany relationship

7. **Error Handling**
   - FAIL FAST AND LOUD principle:
     - No try/except blocks
     - Let any errors crash immediately
     - Clear error messages for debugging

8. **Validation**
   - Count imported posts
   - Verify all files processed
   - Report summary statistics

## Notes
- Frontmatter format is JSON (not YAML)
- Some posts may have HTML/styling in markdown (preserve as-is)
- Hugo shortcodes should be removed or handled appropriately
- All imported posts are considered 'published' status