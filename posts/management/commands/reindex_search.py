from django.core.management.base import BaseCommand
from django.db import transaction
from posts.models import Post
from search.search import clear_search_index, initialize_search_index, bulk_index_posts


class Command(BaseCommand):
    help = 'Clear and rebuild the Meilisearch search index'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear-only',
            action='store_true',
            help='Only clear the index, do not rebuild',
        )

    def handle(self, *args, **options):
        self.stdout.write('🔍 Starting search index rebuild...')
        
        try:
            # Clear existing index
            self.stdout.write('🧹 Clearing existing search index...')
            clear_search_index()
            self.stdout.write(self.style.SUCCESS('✅ Search index cleared'))
            
            if options['clear_only']:
                self.stdout.write(self.style.SUCCESS('🎉 Index clearing completed'))
                return
            
            # Initialize fresh index
            self.stdout.write('🏗️  Initializing fresh search index...')
            initialize_search_index()
            self.stdout.write(self.style.SUCCESS('✅ Search index initialized'))
            
            # Get all published posts
            posts = Post.objects.filter(status='published')
            post_count = posts.count()
            
            if post_count == 0:
                self.stdout.write(self.style.WARNING('⚠️  No published posts found to index'))
                return
            
            # Bulk index all posts
            self.stdout.write(f'📝 Indexing {post_count} published posts...')
            bulk_index_posts(list(posts))
            
            self.stdout.write(
                self.style.SUCCESS(f'🎉 Successfully indexed {post_count} posts!')
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error during search index rebuild: {str(e)}')
            )
            raise