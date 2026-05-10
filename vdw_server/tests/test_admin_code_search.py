from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from pages.models import Page
from site_pages.models import SitePage


class AdminCodeSearchTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="admin",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.staff_user)

        self.index_patch = patch("pages.signals.index_page")
        self.remove_patch = patch("pages.signals.remove_page_from_search")
        self.index_patch.start()
        self.remove_patch.start()
        self.addCleanup(self.index_patch.stop)
        self.addCleanup(self.remove_patch.stop)

    def test_search_finds_regular_page_markdown_source(self):
        image_url = "https://d378j1rmrlek7x.cloudfront.net/attachments/jpg/genes-restrict.jpg"
        page = Page.objects.create(
            title="Image Source Page",
            slug="image-source-page",
            content_md=f'<img src="{image_url}" alt="image" width="300">',
            status="published",
        )

        response = self.client.get(reverse("admin_code_search"), {"q": image_url})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Image Source Page")
        self.assertContains(response, "Markdown source")
        self.assertContains(response, image_url)
        self.assertContains(response, reverse("admin:posts_page_change", args=[page.pk]))

    def test_search_finds_regular_page_original_tiki(self):
        marker = "legacy-tiki-source-marker"
        page = Page.objects.create(
            title="Converted Source Page",
            slug="converted-source-page",
            content_md="Converted markdown without the marker",
            status="draft",
        )
        Page.objects.filter(pk=page.pk).update(original_tiki=f"Original source with {marker}")

        response = self.client.get(reverse("admin_code_search"), {"q": marker})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Converted Source Page")
        self.assertContains(response, "Original Tiki")
        self.assertContains(response, marker)

    def test_search_finds_site_page_generated_html(self):
        marker = "site-html-only-marker"
        site_page = SitePage.objects.create(
            title="Generated HTML Site Page",
            slug="generated-html-site-page",
            page_type="custom",
            content_md="Markdown without the marker",
            is_published=True,
        )
        SitePage.objects.filter(pk=site_page.pk).update(
            content_html=f'<section data-code-marker="{marker}"></section>'
        )

        response = self.client.get(reverse("admin_code_search"), {"q": marker})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Generated HTML Site Page")
        self.assertContains(response, "Generated HTML")
        self.assertContains(response, marker)
        self.assertContains(response, reverse("admin:pages_sitepage_change", args=[site_page.pk]))

    def test_empty_search_does_not_show_results(self):
        Page.objects.create(
            title="Existing Page",
            slug="existing-page",
            content_md="Body text",
            status="published",
        )

        response = self.client.get(reverse("admin_code_search"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Existing Page")
