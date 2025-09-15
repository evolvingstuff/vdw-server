# Feature: Admin Login Redirect to Previous Page

## Goal
When clicking the admin link from a post page and logging in, automatically redirect to the editing screen for that specific post instead of the default admin dashboard.

## Current Behavior
- User views a post (e.g., `/post/XYZ`)
- User clicks "Admin" link
- User logs in
- User is redirected to default admin dashboard
- User must manually navigate to find and edit the post

## Desired Behavior
- User views a post (e.g., `/post/XYZ`)
- User clicks "Admin" link
- User logs in
- User is automatically redirected to the edit screen for post XYZ

## Implementation Plan

### 1. Analyze Current Implementation
- Find where the admin link is rendered in templates
- Locate the login view/controller
- Understand current redirect logic after login

### 2. Capture Origin Context
- Modify admin link to include a `next` parameter with current page URL
- For post pages, enhance this to include post ID or direct edit URL

### 3. Modify Login Flow
- Update login view to preserve the `next` parameter
- After successful login, check if `next` parameter contains a post reference
- If it's a post page, transform redirect to go directly to edit screen
- Otherwise, use standard redirect logic

### 4. Handle Edge Cases
- Ensure security: validate and sanitize redirect URLs
- Handle cases where post might not exist or user lacks permissions
- Fallback to default admin page if redirect fails

## Technical Details

### URL Pattern Mapping
- Post view URL: `/post/<id>` or similar
- Post edit URL: `/admin/posts/<id>/edit` or Django admin pattern
- Need to map from viewing URL to editing URL

### Security Considerations
- Only allow redirects to same domain
- Validate user has permission to edit the specific post
- Sanitize any user-provided redirect URLs

## Testing Checklist
- [ ] Admin link from homepage redirects to admin dashboard after login
- [ ] Admin link from post page redirects to post edit screen after login
- [ ] Invalid/malicious redirect URLs are handled safely
- [ ] Users without edit permissions get appropriate error/redirect
- [ ] Logout and re-login preserves intended redirect