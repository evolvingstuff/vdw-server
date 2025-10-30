from django.core.management.base import BaseCommand
from pages.models import Page
from search.search import clear_search_index, initialize_search_index, bulk_index_pages


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
            
            # Get all published pages. Use iterator to avoid materializing all rows at once
            pages = Page.objects.filter(status='published')
            page_count = pages.count()
            
            if page_count == 0:
                self.stdout.write(self.style.WARNING('⚠️  No published pages found to index'))
                return
            
            # Bulk index all pages with streaming iteration to avoid large SQLite temp files
            self.stdout.write(f'📝 Indexing {page_count} published pages...')
            bulk_index_pages(pages.iterator(chunk_size=1000))
            
            self.stdout.write(
                self.style.SUCCESS(f'🎉 Successfully indexed {page_count} pages!')
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error during search index rebuild: {str(e)}')
            )
            raise
