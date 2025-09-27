from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

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
