import meilisearch
from django.conf import settings
from .models import Post


def get_search_client():
    """Get MeiliSearch client instance"""
    return meilisearch.Client(
        settings.MEILISEARCH_URL, 
        settings.MEILISEARCH_MASTER_KEY
    )


def initialize_search_index():
    """Initialize MeiliSearch index with proper configuration"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    
    # Configure searchable attributes - only search on plain text content
    index.update_searchable_attributes([
        'title',
        'content',  # Plain text version for searching
        'tags'
    ])
    
    # Configure filterable attributes
    index.update_filterable_attributes([
        'status',
        'created_date',
        'tags'
    ])
    
    # TODO: Disable typo tolerance to prevent "Metallica" matching "metallic"
    # The update_typo_tolerance() method is breaking search functionality
    # Need to find correct MeiliSearch Python client API for typo tolerance
    # index.update_typo_tolerance({
    #     'enabled': False
    # })
    
    # Configure ranking rules (default is good for now)
    # Words, Typo, Proximity, Attribute, Sort, Exactness
    
    return index


def clear_search_index():
    """Delete all documents from search index"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    index.delete_all_documents()
    

def format_post_for_search(post):
    """Convert Post object to search document format"""
    return {
        'id': post.pk,
        'title': post.title,
        'slug': post.slug,
        'content': post.content_text,  # Plain text for searching
        'content_html': post.content_html,  # HTML for display in results
        'tags': [tag.name for tag in post.tags.all()],
        'status': post.status,
        'created_date': post.created_date.isoformat()
    }


def index_post(post):
    """Index a single post in MeiliSearch"""
    if post.status != 'published':
        return  # Only index published posts
    
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    
    document = format_post_for_search(post)
    index.add_documents([document])


def remove_post_from_search(post_id):
    """Remove a post from search index"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    index.delete_document(post_id)


def bulk_index_posts(posts_queryset):
    """Index multiple posts in batches"""
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    
    batch_size = 100
    batch = []
    
    for post in posts_queryset:
        if post.status == 'published':
            batch.append(format_post_for_search(post))
        
        if len(batch) >= batch_size:
            index.add_documents(batch)
            batch = []
    
    # Add remaining posts
    if batch:
        index.add_documents(batch)


def search_posts(query, limit=20):
    """Search posts and return results"""
    if not query.strip():
        return {'hits': [], 'query': query}
    
    client = get_search_client()
    index = client.index(settings.MEILISEARCH_INDEX_NAME)
    
    results = index.search(query, {
        'limit': limit,
        'filter': 'status = published',
        'attributesToHighlight': ['title', 'content'],
        'cropLength': 150,
        'attributesToCrop': ['content']
    })
    
    return results