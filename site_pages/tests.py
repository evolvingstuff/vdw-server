from types import SimpleNamespace

from django.contrib.admin.sites import AdminSite
from django.urls import reverse
from django.test import RequestFactory, TestCase

from site_pages.admin import SitePageAdmin
from site_pages.models import SitePage


class SitePageAdminQueryOptimizationTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = SitePageAdmin(SitePage, self.site)

    def test_changelist_queryset_defers_large_text_fields(self):
        request = self.factory.get('/admin/site_pages/sitepage/')
        request.resolver_match = SimpleNamespace(url_name='site_pages_sitepage_changelist')

        queryset = self.admin.get_queryset(request)
        sql = str(queryset.query)

        self.assertNotIn('"pages_page"."content_md"', sql)
        self.assertNotIn('"pages_page"."content_html"', sql)
        self.assertNotIn('"pages_page"."content_text"', sql)
        self.assertIn('"pages_page"."title"', sql)

    def test_change_queryset_keeps_large_text_fields_available(self):
        request = self.factory.get('/admin/site_pages/sitepage/1/change/')
        request.resolver_match = SimpleNamespace(url_name='site_pages_sitepage_change')

        queryset = self.admin.get_queryset(request)
        sql = str(queryset.query)

        self.assertIn('"pages_page"."content_md"', sql)
        self.assertIn('"pages_page"."content_html"', sql)
        self.assertIn('"pages_page"."content_text"', sql)


class SitePageDetailPrintTemplateTests(TestCase):
    def test_site_page_detail_renders_print_metadata(self):
        page = SitePage.objects.create(
            title="About Print Layouts",
            slug="about-print-layouts",
            page_type="custom",
            content_md="Body copy for a site page",
            is_published=True,
        )

        response = self.client.get(reverse('site_page_detail', args=[page.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="print-page-metadata"')
        self.assertContains(response, 'class="post-footer print-only-footer"')
        self.assertContains(response, f'URL: http://testserver/{page.slug}/')
