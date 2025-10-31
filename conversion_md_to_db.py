#!/usr/bin/env python3

import os
import sys
import json
import glob
import re
from pathlib import Path
from datetime import datetime
from tqdm import tqdm
from helper_functions.meilisearch import *


DISALLOWED_TAG_NAMES = {
    'ai',
    'top news',
    'z',
    'z-section',
    'video page names',
    'old name',
}

def delete_database():
    """Delete existing SQLite database"""
    db_path = Path('db.sqlite3')
    if db_path.exists():
        print(f"Deleting existing database: {db_path}")
        db_path.unlink()

def setup_django():
    """Setup Django environment"""
    sys.path.append('.')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vdw_server.settings')
    
    import django
    django.setup()
    
    # Import Django components
    from django.core.management import execute_from_command_line
    from pages.models import Page
    from tags.models import Tag
    from site_pages.models import SitePage
    from django.utils import timezone
    from django.utils.text import slugify

    return execute_from_command_line, Page, Tag, SitePage, timezone, slugify

def run_migrations(execute_from_command_line):
    """Run Django migrations to create fresh database"""
    print("Running migrations to create fresh database...")
    execute_from_command_line(['manage.py', 'migrate'])


def create_homepage(SitePage):
    """Create initial homepage"""
    homepage, created = SitePage.objects.get_or_create(
        page_type='homepage',
        defaults={
            'title': 'Home',
            'slug': 'home',
            'content_md': '''# Home Page W.I.P.

Welcome to VDW Blog

[Browse All Pages →](/pages/)''',
            'is_published': True,
            'meta_description': 'Welcome to VDW Blog'
        }
    )

    if created:
        print("✅ Homepage created successfully")
    else:
        print("ℹ️  Homepage already exists")


def create_superuser():
    """Create superuser from environment variables"""
    import os
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    
    # Get superuser details from environment
    username = os.environ.get('DJANGO_SUPERUSER_NAME')
    email = os.environ.get('DJANGO_SUPERUSER_EMAIL') 
    password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
    
    if not all([username, email, password]):
        raise ValueError("Missing superuser environment variables: DJANGO_SUPERUSER_NAME, DJANGO_SUPERUSER_EMAIL, DJANGO_SUPERUSER_PASSWORD")
    
    # Delete existing superuser if exists
    User.objects.filter(username=username).delete()
    
    print(f"Creating superuser: {username}")
    User.objects.create_superuser(
        username=username,
        email=email, 
        password=password
    )

def extract_frontmatter_and_content(file_content):
    """Extract JSON frontmatter and markdown content from file"""
    # Find the first { and matching }
    start = file_content.find('{')
    if start == -1:
        raise ValueError("No JSON frontmatter found")
    
    brace_count = 0
    end = start
    for i, char in enumerate(file_content[start:], start):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                end = i + 1
                break
    
    if brace_count != 0:
        raise ValueError("Malformed JSON frontmatter - unmatched braces")
    
    frontmatter_json = file_content[start:end]
    frontmatter = json.loads(frontmatter_json)
    
    # Everything after the frontmatter is markdown content
    markdown_content = file_content[end:].strip()
    
    return frontmatter, markdown_content, frontmatter_json

def search_and_replace(content):
    """Apply search and replace operations to fix common content issues"""
    # Fix attachment path inconsistencies: attachments/jpeg/ -> attachments/jpg/
    content = content.replace('attachments/jpeg/', 'attachments/jpg/')
    
    # Add more search/replace operations here as needed
    # content = content.replace('old_pattern', 'new_pattern')
    
    return content

def clean_markdown_content(content):
    """Clean markdown content by removing Hugo shortcodes and unwanted elements"""
    # Remove Hugo shortcodes like {{< toc >}}
    content = re.sub(r'\{\{<.*?>\}\}', '', content)
    
    # Remove leading --- separators and empty lines
    content = content.lstrip()
    if content.startswith('---'):
        # Remove the --- line and any following empty lines
        lines = content.split('\n')
        # Skip the first --- line
        lines = lines[1:]
        # Skip any empty lines after the ---
        while lines and not lines[0].strip():
            lines = lines[1:]
        content = '\n'.join(lines)
    
    # Apply search and replace operations
    content = search_and_replace(content)
    
    return content

def process_tags(tag_names, Tag, slugify, used_slugs):
    """Create or get Tag objects for a list of tag names, skipping disallowed entries."""
    tags = []
    for tag_name in tag_names:
        tag_name = tag_name.strip()
        if not tag_name:
            continue

        if tag_name.lower() in DISALLOWED_TAG_NAMES:
            continue
            
        # Check if tag already exists by name
        existing_tag = Tag.objects.filter(name=tag_name).first()
        if existing_tag:
            tags.append(existing_tag)
            continue
        
        # Generate slug and check against in-memory set
        slug = slugify(tag_name)
        if slug in used_slugs:
            # Use the first tag with this slug
            existing_tag = Tag.objects.get(slug=slug)
            tags.append(existing_tag)
        else:
            # Create new tag
            tag = Tag.objects.create(name=tag_name, slug=slug)
            used_slugs.add(slug)
            tags.append(tag)
    
    return tags

def parse_date(date_string, timezone):
    """Parse ISO-ish date strings into timezone-aware datetimes."""
    if not date_string:
        raise ValueError("Date string is required")

    value = date_string.strip()

    # Support both simple YYYY-MM-DD and fuller ISO 8601 strings.
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = datetime.strptime(value, '%Y-%m-%d')
        except ValueError as exc:
            raise ValueError(f"Unsupported date format: {value}") from exc

    if timezone.is_naive(dt):
        return timezone.make_aware(dt)

    return dt.astimezone(timezone.get_current_timezone())


def get_created_and_modified_dates(frontmatter, timezone):
    """Return created/modified datetimes derived from frontmatter."""
    created = parse_date(frontmatter['date'], timezone)
    last_modified_raw = frontmatter.get('lastmod')
    modified = parse_date(last_modified_raw, timezone) if last_modified_raw else created
    return created, modified

def main():
    print("Starting markdown to database conversion...")
    
    # Step 1: Delete existing database FIRST
    delete_database()
    
    # Step 2: Setup Django AFTER database is deleted
    execute_from_command_line, Page, Tag, SitePage, timezone, slugify = setup_django()
    
    # Step 3: Run migrations 
    run_migrations(execute_from_command_line)
    
    # Step 4: Create superuser
    create_superuser()

    # Step 5: Create homepage
    create_homepage(SitePage)

    # Step 6: Start MeiliSearch (restart it fresh to ensure clean state)
    start_meilisearch()

    # Step 7: Find all markdown files
    pages_dir = Path('../vdw-posts/posts')
    pages_tiki_dir = Path('../vdw-posts/posts_tiki')
    markdown_files = list(pages_dir.glob('*.md'))
    
    if not markdown_files:
        raise ValueError(f"No markdown files found in {pages_dir}")
    
    print(f"Found {len(markdown_files)} markdown files to process")
    
    # Step 8: Process each file
    created_pages = 0
    used_slugs = set()  # Track slugs to avoid duplicates
    skipped_files = []  # Track files with no frontmatter
    
    for file_path in tqdm(markdown_files, desc="Converting pages"):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            # Extract frontmatter and content
            start = file_content.find('{')
            if start == -1:
                skipped_files.append(str(file_path))
                continue
                
            frontmatter, markdown_content, frontmatter_json = extract_frontmatter_and_content(file_content)
        except Exception as e:
            print(f"\nERROR processing file: {file_path}")
            print(f"Error details: {e}")
            raise
        
        # Clean markdown content
        markdown_content = clean_markdown_content(markdown_content)
        
        # Load corresponding tiki file (MUST exist)
        tiki_file_path = pages_tiki_dir / f"{file_path.stem}.tiki"
        if not tiki_file_path.exists():
            raise FileNotFoundError(f"Missing required tiki file: {tiki_file_path}")
        
        with open(tiki_file_path, 'r', encoding='utf-8') as f:
            tiki_content = f.read()
        
        # Process tags from both 'tags' and 'categories'
        all_tags = []
        if 'tags' in frontmatter:
            all_tags.extend(frontmatter['tags'])
        if 'categories' in frontmatter:
            all_tags.extend(frontmatter['categories'])

        admin_tag_present = any(
            isinstance(tag_name, str) and tag_name.strip().lower() == 'admin only'
            for tag_name in all_tags
        )

        tags = process_tags(all_tags, Tag, slugify, used_slugs)
        
        # Parse dates from frontmatter
        created_date, modified_date = get_created_and_modified_dates(frontmatter, timezone)
        
        # Calculate redacted count from sections_excluded (must exist)
        redacted_count = len(frontmatter['sections_excluded'])
        
        # Create Page object
        page = Page.objects.create(
            title=frontmatter['title'],
            slug=frontmatter['slug'],
            content_md=markdown_content,
            status='draft' if admin_tag_present else 'published',
            created_date=created_date,
            modified_date=modified_date,
            original_page_id=frontmatter.get('tiki_page_id'),
            original_tiki=tiki_content,
            aliases='\n'.join(frontmatter.get('aliases', [])),
            front_matter=frontmatter_json,
            redacted_count=redacted_count
        )
        
        # Add tags
        page.tags.set(tags)
        
        # Fix modified_date after tags are set (tags.set() triggers another save)
        # Use direct database update to bypass auto_now=True so we preserve frontmatter lastmod
        Page.objects.filter(pk=page.pk).update(modified_date=modified_date)
        
        created_pages += 1
    
    print(f"\nConversion complete!")
    print(f"Created {created_pages} pages")
    print(f"Total tags: {Tag.objects.count()}")

    if skipped_files:
        print(f"\nSkipped {len(skipped_files)} files with no frontmatter:")
        for skipped_file in skipped_files:
            print(f"  - {skipped_file}")

    # Step 9: Index all pages in MeiliSearch using management command
    print(f"\nIndexing {created_pages} pages in MeiliSearch...")
    try:
        execute_from_command_line(['manage.py', 'reindex_search'])
        print("MeiliSearch indexing complete!")
    except Exception as e:
        # External system failure (MeiliSearch or client). Report clearly and continue.
        print(f"❌ MeiliSearch indexing failed: {e}")
        print("The database import completed, but search indexing did not.\n"
              "Tip: upgrade the 'meilisearch' Python client to match your server and re-run `python manage.py reindex_search`.")

if __name__ == '__main__':
    main()
