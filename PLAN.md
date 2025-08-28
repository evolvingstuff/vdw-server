# Media Upload Implementation Plan

## Current Status
- ✅ S3 storage settings configured
- ✅ Settings updated to always use S3 (no local storage)
- ✅ S3 bucket structure exists with file type organization
- ✅ Upload endpoint implemented at `/admin/upload-media/`
- ✅ Copy & paste image upload working (replaced drag & drop)
- 🔧 IN PROGRESS: Fixing path structure and filename handling

## User Workflow (Copy & Paste)
**Current user experience:**
1. User copies image (from Finder, screenshot, etc.)
2. Places cursor in markdown editor where image should go
3. Pastes with Cmd+V
4. File automatically uploads to S3 in background
5. Markdown gets CloudFront URL inserted: `![filename](https://cdn.example.com/DEV/attachments/jpg/my-image.jpg)`
6. Live preview immediately shows the actual image
7. Loading placeholder shown during upload
8. Error alerts if upload fails

## S3 Bucket Structure
```
vitdwiki2/
├── DEV/
│   └── attachments/
│       ├── jpg/
│       ├── png/
│       ├── pdf/
│       ├── doc/
│       ├── docx/
│       ├── gif/
│       ├── bmp/
│       ├── mp3/
│       ├── mp4/
│       ├── webp/
│       ├── zip/
│       └── [other file type folders]
```

## Implementation Plan

### 1. ~~Custom Storage Backend~~ Direct Upload Handler
Upload view that:
- Detects file extension/type from uploaded file
- Routes files to appropriate subfolder: `DEV/attachments/{file_type}/`
- Converts filenames to URL-friendly slugs (when available from clipboard)
- For clipboard images without names: uses timestamp-based names
- Handles collisions with counter appending (filename-1.ext, filename-2.ext, etc.)

### 2. File Processing Logic
- Original filename: `My Awesome Image!.jpg`
- Slugified: `my-awesome-image.jpg`
- Final S3 path: `DEV/attachments/jpg/my-awesome-image.jpg`
- If collision: `DEV/attachments/jpg/my-awesome-image-1.jpg`

### 3. Django Upload API
Create Django view/endpoint for AJAX uploads:
- Accept multipart file uploads
- Use custom storage backend
- Return JSON with CloudFront URL or error
- File size limit: 10MB
- CSRF protection

### 4. Frontend Copy & Paste
Update markdown editor (both normal and fullscreen modes):
- Handle paste events on textareas
- Detect images in clipboard
- Show loading placeholder during upload  
- Insert markdown syntax at cursor position
- Replace placeholder with final URL when complete
- Display error alerts on failure

### 5. Markdown Integration
- Images: `![original-filename](cloudfront-url)`
- Focus on images initially, expand to PDFs later
- Live preview should immediately show uploaded images

### 6. Testing Plan
- Copy & paste various image formats (jpg, png, gif)
- Test screenshots (Cmd+Ctrl+Shift+4)
- Test copying from Preview app, Finder, web browser
- Test in both normal and fullscreen editor modes
- Verify filename collision handling
- Test file size limits and error handling
- Confirm files appear in correct S3 folders with proper structure
- Verify CloudFront delivery and live preview

## File Type Mapping
Extensions will be mapped to folders:
- `.jpg`, `.jpeg` → `jpg/`
- `.png` → `png/`
- `.pdf` → `pdf/`
- `.doc` → `doc/`
- `.docx` → `docx/`
- `.gif` → `gif/`
- And so on...

## Benefits
- ✅ No local file storage (robust deployment)
- ✅ Organized by file type (easy S3 management)
- ✅ URL-friendly filenames (SEO, readability)
- ✅ Collision handling (no overwrites)
- ✅ CloudFront-ready (fast delivery)