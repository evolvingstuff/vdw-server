# Integrated Navigation Bar and Homepage Plan

## Overview
Integrate the site navigation into the fixed top bar with the search functionality, creating a unified header experience. Also create a homepage as the main entry point for the site.

## Requirements

### Navigation Bar Layout
- **Left side**: VDW Blog (eventually logo) | Home | All Posts
- **Center**: Search bar (keep current centered position)
- **Right side**: Admin

### Homepage
- Create new Django app for homepage/core functionality
- Simple "Home Page W.I.P." placeholder for now
- Will be the default landing page at root URL (`/`)

## Implementation Steps

### 1. Create Core/Home App
- Run `python app.py startapp core` (or `home`)
- Add to `INSTALLED_APPS`
- Create homepage view and template
- Add URL pattern for root (`/`)

### 2. Redesign Global Navigation Bar
- Update `templates/components/global_search_bar.html`
- Add navigation links to left side
- Move Admin link to right side
- Keep search bar centered
- Ensure proper responsive design

### 3. Update CSS/Layout
- Use flexbox for three-section layout (left nav, center search, right admin)
- Ensure search bar remains centered
- Add proper spacing and alignment
- Mobile responsive design (possibly hamburger menu)

### 4. Clean Up Old Navigation
- Remove redundant navigation from `templates/base.html`
- Remove the old header section entirely
- Keep only the integrated top bar

### 5. Test All Pages
- Homepage (`/`)
- Posts list (`/posts/`)
- Individual posts (`/posts/<slug>/`)
- Admin (`/admin/`)
- Ensure navigation works and looks consistent

## Technical Details

### Layout Structure
```
[VDW Blog | Home | All Posts]  [    Search Bar    ]  [Admin]
    Left aligned                   Centered           Right aligned
```

### CSS Approach
- Use CSS Grid or Flexbox for the three-column layout
- Search bar in center column with max-width
- Navigation links in left column
- Admin link in right column
- Fixed positioning maintained

## Future Enhancements
- Replace "VDW Blog" text with logo
- Expand homepage with actual content
- Add more navigation items as needed
- Potentially add user account menu on right