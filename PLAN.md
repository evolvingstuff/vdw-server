# Media Upload Implementation Plan

## Current Status
- ✅ S3 storage settings configured
- ✅ Settings updated to always use S3 (no local storage)
- ✅ S3 bucket structure exists with file type organization

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

### 1. Custom Storage Backend
Create a custom Django storage class that:
- Detects file extension/type from uploaded file
- Routes files to appropriate subfolder: `DEV/attachments/{file_type}/`
- Converts filenames to URL-friendly slugs
- Handles collisions with counter appending (filename-1.ext, filename-2.ext, etc.)

### 2. File Processing Logic
- Original filename: `My Awesome Image!.jpg`
- Slugified: `my-awesome-image.jpg`
- Final S3 path: `DEV/attachments/jpg/my-awesome-image.jpg`
- If collision: `DEV/attachments/jpg/my-awesome-image-1.jpg`

### 3. Model Updates
Add media fields to Post model:
- `featured_image` - ImageField for post thumbnail
- Consider future: inline attachments, galleries, etc.

### 4. Admin Interface
Update Django admin to:
- Show image upload field
- Preview uploaded images
- Display S3 URLs

### 5. Testing Plan
- Upload various file types (jpg, png, pdf, etc.)
- Test filename collision handling
- Verify files appear in correct S3 folders
- Test image display in admin interface
- Verify CloudFront delivery (if configured)

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