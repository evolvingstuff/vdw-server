# Media Upload - Extended File Support Plan

## Current Status
- ✅ Copy & paste image upload working
- ✅ S3 storage with CloudFront CDN configured
- ✅ Files organized by type in S3: `public/attachments/{type}/`
- ✅ Images display correctly in markdown preview
- 🔧 TODO: Extend to support non-image files (PDFs, docs, etc.)

## Current Limitations
1. **Frontend only accepts images**: `item.type.indexOf('image')` check ignores other files
2. **Markdown syntax hardcoded for images**: Always uses `![name](url)` 
3. **Backend validates against images only**: Limited content-type mapping

## Implementation Plan for Non-Image Files

### 1. Frontend Changes
**File: `posts/templates/admin/posts/post/change_form.html`**
- Remove image-only filter in paste handler
- Accept any file type from clipboard
- Detect file type to choose correct markdown syntax:
  - Images: `![filename](url)` - embeds image
  - Others: `[filename](url)` - creates download link

### 2. Backend Changes  
**File: `posts/views.py`**
- Expand `content_type_map` to include:
  - `application/pdf` → `pdf/`
  - `application/msword` → `doc/`
  - `application/vnd.openxmlformats-officedocument.wordprocessingml.document` → `docx/`
  - `application/vnd.ms-excel` → `xls/`
  - `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` → `xlsx/`
  - `text/plain` → `txt/`
  - `application/zip` → `zip/`
  - `video/mp4` → `mp4/`
  - `audio/mpeg` → `mp3/`

### 3. Markdown Syntax by File Type
```javascript
// Determine markdown syntax based on file type
if (file.type.startsWith('image/')) {
    markdown = `![${fileName}](${url})`;  // Embed image
} else {
    markdown = `[${fileName}](${url})`;    // Download link
}
```

### 4. User Experience
1. User copies any file (image, PDF, document, etc.)
2. Places cursor in markdown editor
3. Pastes with Cmd+V
4. File uploads to S3 with progress indicator
5. Appropriate markdown inserted:
   - Images show inline in preview
   - Documents show as clickable links

### 5. Testing Plan
- [ ] Copy & paste PDF from Finder
- [ ] Copy & paste Word document
- [ ] Copy & paste text file
- [ ] Copy & paste video file
- [ ] Verify correct markdown syntax for each type
- [ ] Confirm files upload to correct S3 folders
- [ ] Test file size limits (10MB)
- [ ] Verify error handling for unsupported types

## S3 Final Structure
```
vitdwiki2/
└── public/
    └── attachments/
        ├── jpg/
        ├── png/
        ├── gif/
        ├── pdf/
        ├── doc/
        ├── docx/
        ├── txt/
        ├── zip/
        ├── mp4/
        ├── mp3/
        └── [other types as needed]
```