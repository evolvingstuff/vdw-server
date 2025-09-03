#!/usr/bin/env python3

import os
import sys
import json
import glob
import re
from pathlib import Path
from datetime import datetime
from tqdm import tqdm

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
    from posts.models import Post, Tag
    from django.utils import timezone
    from django.utils.text import slugify
    from posts.search import initialize_search_index, clear_search_index, bulk_index_posts
    
    return execute_from_command_line, Post, Tag, timezone, slugify, initialize_search_index, clear_search_index, bulk_index_posts

def run_migrations(execute_from_command_line):
    """Run Django migrations to create fresh database"""
    print("Running migrations to create fresh database...")
    execute_from_command_line(['manage.py', 'migrate'])

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
    
    return content

def process_tags(tag_names, Tag, slugify, used_slugs):
    """Create or get Tag objects for a list of tag names"""
    tags = []
    for tag_name in tag_names:
        tag_name = tag_name.strip()
        if not tag_name:
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
    """Parse date string to Django datetime"""
    # Assuming format like "2019-10-02"
    dt = datetime.strptime(date_string, '%Y-%m-%d')
    return timezone.make_aware(dt)

def main():
    print("Starting markdown to database conversion...")
    
    # Step 1: Delete existing database FIRST
    delete_database()
    
    # Step 2: Setup Django AFTER database is deleted
    execute_from_command_line, Post, Tag, timezone, slugify, initialize_search_index, clear_search_index, bulk_index_posts = setup_django()
    
    # Step 3: Run migrations 
    run_migrations(execute_from_command_line)
    
    # Step 4: Setup MeiliSearch
    print("Initializing MeiliSearch index...")
    clear_search_index()  # Start fresh
    initialize_search_index()
    
    # Step 5: Find all markdown files
    posts_dir = Path('../vdw-conversion/posts')
    markdown_files = list(posts_dir.glob('*.md'))
    
    if not markdown_files:
        raise ValueError(f"No markdown files found in {posts_dir}")
    
    print(f"Found {len(markdown_files)} markdown files to process")
    
    # Step 5: Process each file
    created_posts = 0
    used_slugs = set()  # Track slugs to avoid duplicates
    skipped_files = []  # Track files with no frontmatter
    
    for file_path in tqdm(markdown_files, desc="Converting posts"):
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        # Extract frontmatter and content
        start = file_content.find('{')
        if start == -1:
            skipped_files.append(str(file_path))
            continue
            
        frontmatter, markdown_content, frontmatter_json = extract_frontmatter_and_content(file_content)
        
        # Clean markdown content
        markdown_content = clean_markdown_content(markdown_content)
        
        # Process tags from both 'tags' and 'categories'
        all_tags = []
        if 'tags' in frontmatter:
            all_tags.extend(frontmatter['tags'])
        if 'categories' in frontmatter:
            all_tags.extend(frontmatter['categories'])
        
        tags = process_tags(all_tags, Tag, slugify, used_slugs)
        
        # Parse date
        created_date = parse_date(frontmatter['date'], timezone)
        
        # Create Post object
        post = Post.objects.create(
            title=frontmatter['title'],
            slug=frontmatter['slug'],
            content_md=markdown_content,
            status='published',
            created_date=created_date,
            original_page_id=frontmatter.get('tiki_page_id'),
            aliases='\n'.join(frontmatter.get('aliases', [])),
            front_matter=frontmatter_json
        )
        
        # Add tags
        post.tags.set(tags)
        
        created_posts += 1
    
    print(f"\nConversion complete!")
    print(f"Created {created_posts} posts")
    print(f"Total tags: {Tag.objects.count()}")
    
    if skipped_files:
        print(f"\nSkipped {len(skipped_files)} files with no frontmatter:")
        for skipped_file in skipped_files:
            print(f"  - {skipped_file}")
    
    # Step 6: Index all posts in MeiliSearch
    print(f"\nIndexing {created_posts} posts in MeiliSearch...")
    published_posts = Post.objects.filter(status='published')
    bulk_index_posts(published_posts)
    print("MeiliSearch indexing complete!")

if __name__ == '__main__':
    main()