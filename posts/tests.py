from datetime import datetime
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from conversion_md_to_db import get_created_and_modified_dates
from posts.admin import PostAdmin
from posts.models import Post


class PostAdminSearchTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = PostAdmin(Post, self.site)

        self.index_patch = patch('posts.signals.index_post')
        self.remove_patch = patch('posts.signals.remove_post_from_search')
        self.index_patch.start()
        self.remove_patch.start()

        self.title_hit = Post.objects.create(
            title="Needle in Title",
            content_md="Body text",
            status='published',
        )
        self.content_hit = Post.objects.create(
            title="Completely Different",
            content_md="Contains the needle keyword",
            status='published',
        )

    def tearDown(self):
        self.index_patch.stop()
        self.remove_patch.stop()

    def test_search_matches_title(self):
        request = self.factory.get('/admin/posts/post/', {'q': 'needle'})
        queryset = Post.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, 'needle')

        self.assertIn(self.title_hit, results)

    def test_search_ignores_content_only_matches(self):
        request = self.factory.get('/admin/posts/post/', {'q': 'needle'})
        queryset = Post.objects.all()

        results, _ = self.admin.get_search_results(request, queryset, 'needle')

        self.assertNotIn(self.content_hit, results)


class ConversionDateParsingTests(SimpleTestCase):
    def test_lastmod_used_when_present(self):
        frontmatter = {
            'date': '2024-01-01',
            'lastmod': '2024-02-03',
        }

        created, modified = get_created_and_modified_dates(frontmatter, timezone)

        expected_created = timezone.make_aware(datetime(2024, 1, 1))
        expected_modified = timezone.make_aware(datetime(2024, 2, 3))

        self.assertEqual(created, expected_created)
        self.assertEqual(modified, expected_modified)

    def test_missing_lastmod_defaults_to_created(self):
        frontmatter = {
            'date': '2024-01-01',
        }

        created, modified = get_created_and_modified_dates(frontmatter, timezone)

        expected_created = timezone.make_aware(datetime(2024, 1, 1))

        self.assertEqual(created, expected_created)
        self.assertEqual(modified, expected_created)

    def test_iso_datetime_with_z_suffix(self):
        frontmatter = {
            'date': '2024-01-01T00:00:00Z',
            'lastmod': '2024-01-01T05:30:00Z',
        }

        created, modified = get_created_and_modified_dates(frontmatter, timezone)

        expected_created = timezone.make_aware(datetime(2024, 1, 1))
        expected_modified = timezone.make_aware(datetime(2024, 1, 1, 5, 30))

        self.assertEqual(created, expected_created)
        self.assertEqual(modified, expected_modified)
