from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from pages.models import Page
from site_pages.models import SitePage
from vdw_server.admin_views import CODE_SEARCH_PAGE_SIZE


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

    def test_search_excludes_regular_page_with_negative_text(self):
        Page.objects.create(
            title="Included Positive Match",
            slug="included-positive-match",
            content_md="Flu appears here without the excluded term",
            status="published",
        )
        Page.objects.create(
            title="Excluded Negative Match",
            slug="excluded-negative-match",
            content_md="Flu appears here with fluoride",
            status="published",
        )

        response = self.client.get(
            reverse("admin_code_search"),
            {"q": "Flu", "exclude_q": "fluoride"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Included Positive Match")
        self.assertNotContains(response, "Excluded Negative Match")

    def test_search_excludes_regular_page_with_negative_text_in_title(self):
        Page.objects.create(
            title="Included Flu Page",
            slug="included-flu-page",
            content_md="Flu appears here without the excluded term",
            status="published",
        )
        Page.objects.create(
            title="Excluded Fluoride Page",
            slug="excluded-fluoride-page",
            content_md="Flu appears here too",
            status="published",
        )

        response = self.client.get(
            reverse("admin_code_search"),
            {"q": "flu", "exclude_q": "fluoride"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Included Flu Page")
        self.assertNotContains(response, "Excluded Fluoride Page")

    def test_search_normalizes_leading_minus_in_exclude_text(self):
        Page.objects.create(
            title="Included Flu Page",
            slug="included-flu-page",
            content_md="Flu appears here without the excluded term",
            status="published",
        )
        Page.objects.create(
            title="Excluded Fluoride Page",
            slug="excluded-fluoride-page",
            content_md="Flu appears here too",
            status="published",
        )

        response = self.client.get(
            reverse("admin_code_search"),
            {"q": "flu", "exclude_q": "-fluoride"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Included Flu Page")
        self.assertNotContains(response, "Excluded Fluoride Page")

    def test_search_paginates_all_regular_page_matches(self):
        total_matches = CODE_SEARCH_PAGE_SIZE + 5
        for index in range(total_matches):
            Page.objects.create(
                title=f"Paginated Flu Page {index}",
                slug=f"paginated-flu-page-{index}",
                content_md="Flu appears here",
                status="published",
            )

        response = self.client.get(reverse("admin_code_search"), {"q": "flu"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_results"]), CODE_SEARCH_PAGE_SIZE)
        self.assertEqual(response.context["page_pagination"]["total_count"], total_matches)
        self.assertContains(response, f"Showing 1-{CODE_SEARCH_PAGE_SIZE}")
        self.assertContains(response, f"of {total_matches}")
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, "page_results_page=2")

        second_response = self.client.get(
            reverse("admin_code_search"),
            {"q": "flu", "page_results_page": "2"},
        )

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(len(second_response.context["page_results"]), 5)
        self.assertContains(
            second_response,
            f"Showing {CODE_SEARCH_PAGE_SIZE + 1}-{total_matches}",
        )
        self.assertContains(second_response, "Page 2 of 2")

    def test_search_pagination_total_respects_exclude_text(self):
        included_matches = CODE_SEARCH_PAGE_SIZE + 5
        for index in range(included_matches):
            Page.objects.create(
                title=f"Included Flu Page {index}",
                slug=f"included-flu-page-{index}",
                content_md="Flu appears here",
                status="published",
            )
        for index in range(7):
            Page.objects.create(
                title=f"Excluded Fluoride Page {index}",
                slug=f"excluded-fluoride-page-{index}",
                content_md="Flu appears here",
                status="published",
            )

        response = self.client.get(
            reverse("admin_code_search"),
            {"q": "flu", "exclude_q": "fluoride"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_pagination"]["total_count"], included_matches)
        self.assertEqual(response.context["result_count"], included_matches)
        self.assertContains(response, f"of {included_matches}")

    def test_search_excludes_site_page_with_negative_text(self):
        SitePage.objects.create(
            title="Included Site Page",
            slug="included-site-page",
            page_type="custom",
            content_md="Flu appears here without the excluded term",
            is_published=True,
        )
        SitePage.objects.create(
            title="Excluded Site Page",
            slug="excluded-site-page",
            page_type="custom",
            content_md="Flu appears here with fluoride",
            is_published=True,
        )

        response = self.client.get(
            reverse("admin_code_search"),
            {"q": "Flu", "exclude_q": "fluoride"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Included Site Page")
        self.assertNotContains(response, "Excluded Site Page")

    def test_search_excludes_site_page_with_negative_text_in_slug(self):
        SitePage.objects.create(
            title="Included Site Page",
            slug="included-site-page",
            page_type="custom",
            content_md="Flu appears here without the excluded term",
            is_published=True,
        )
        SitePage.objects.create(
            title="Excluded Site Page",
            slug="excluded-fluoride-site-page",
            page_type="custom",
            content_md="Flu appears here too",
            is_published=True,
        )

        response = self.client.get(
            reverse("admin_code_search"),
            {"q": "flu", "exclude_q": "fluoride"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Included Site Page")
        self.assertNotContains(response, "Excluded Site Page")

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
