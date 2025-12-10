from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from pages.models import Page
from site_pages.models import SitePage
from vdw_server.sitemap_utils import refresh_sitemap


class SitemapGenerationTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.sitemap_path = Path(self.tempdir.name) / 'sitemap.xml'

        self.index_patch = patch('pages.signals.index_page')
        self.remove_patch = patch('pages.signals.remove_page_from_search')
        self.index_patch.start()
        self.remove_patch.start()
        self.addCleanup(self.index_patch.stop)
        self.addCleanup(self.remove_patch.stop)

    def test_refresh_sitemap_includes_only_published_entries(self):
        homepage = SitePage.objects.create(
            title='Home',
            page_type='homepage',
            content_md='# Welcome',
            is_published=True,
        )
        SitePage.objects.create(
            title='Hidden',
            slug='hidden',
            page_type='custom',
            content_md='hidden',
            is_published=False,
        )

        published_page = Page.objects.create(
            title='Public Page',
            slug='public-page',
            content_md='content',
            status='published',
        )
        Page.objects.create(
            title='Draft Page',
            slug='draft-page',
            content_md='draft',
            status='draft',
        )

        desired_timestamp = timezone.make_aware(datetime(2024, 1, 2, 3, 4, 5), datetime_timezone.utc)
        SitePage.objects.filter(pk=homepage.pk).update(modified_date=desired_timestamp)
        Page.objects.filter(pk=published_page.pk).update(modified_date=desired_timestamp)

        with override_settings(SITEMAP_FILE_PATH=self.sitemap_path):
            path = refresh_sitemap('https://example.com/')

        self.assertEqual(path, self.sitemap_path)
        xml_payload = self.sitemap_path.read_text()

        self.assertIn('<loc>https://example.com/</loc>', xml_payload)
        self.assertIn('<loc>https://example.com/pages/public-page/</loc>', xml_payload)
        self.assertNotIn('draft-page', xml_payload)
        self.assertNotIn('hidden', xml_payload)
        self.assertIn('2024-01-02T03:04:05+00:00', xml_payload)

    def test_sitemap_view_serves_generated_file(self):
        xml_contents = '<?xml version="1.0" encoding="utf-8"?><urlset></urlset>'
        self.sitemap_path.write_text(xml_contents)

        with override_settings(SITEMAP_FILE_PATH=self.sitemap_path):
            response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        body = b''.join(response.streaming_content).decode('utf-8')
        self.assertEqual(body, xml_contents)

    def test_missing_sitemap_returns_404(self):
        with override_settings(SITEMAP_FILE_PATH=self.sitemap_path):
            response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 404)
