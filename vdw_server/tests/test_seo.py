from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from helper_functions.seo import generate_meta_description, meta_description_for
from pages.models import Page


class MetaDescriptionTests(SimpleTestCase):
    def test_skips_citation_author_and_affiliation_lines(self):
        content = (
            "Inflamm Allergy Drug Targets. 2013 Aug;12(4):239-45.\n\n"
            "Gunville CF1, Mourani PM, Ginde AA.\n\n"
            "1Section of Critical Care, Department of Pediatrics, University of Colorado School of Medicine.\n\n"
            "Vitamin D is well known for its classic role in the maintenance of bone mineral density. "
            "However, vitamin D also has an important influence on the immune system."
        )
        self.assertTrue(generate_meta_description(content).startswith("Vitamin D is well known"))

    def test_strips_markdown_and_section_label_and_truncates(self):
        content = (
            "**Background:** Glutathione is the most abundant endogenous antioxidant and a critical "
            "regulator of oxidative stress, and maintaining optimal levels matters for health. " * 3
        )
        description = generate_meta_description(content)
        self.assertTrue(description.startswith("Glutathione is"))
        self.assertLessEqual(len(description), 155)
        self.assertTrue(description.endswith("…"))

    def test_prefers_findings_over_methods_paragraph(self):
        content = (
            "Methods: A systematic search was performed in PubMed and Scopus up to December 2020 "
            "for studies of vitamin D and COVID-19 severity.\n\n"
            "Results: Low vitamin D was associated with a five-fold higher risk of severe COVID-19 "
            "across the included studies."
        )
        self.assertTrue(generate_meta_description(content).startswith("Low vitamin D was associated"))

    def test_uses_methods_paragraph_when_nothing_else(self):
        content = (
            "Methods: A systematic search was performed in PubMed and Scopus up to December 2020 "
            "for studies of vitamin D and COVID-19 severity."
        )
        self.assertTrue(generate_meta_description(content).startswith("A systematic search"))

    def test_returns_empty_when_no_prose(self):
        content = "#### A heading\n\n* list item\n\n<img src='x.png' alt='image'>"
        self.assertEqual(generate_meta_description(content), "")

    def test_explicit_meta_description_wins(self):
        page = Page(title="T", content_md="Some long prose paragraph " * 10, meta_description="Explicit.")
        self.assertEqual(meta_description_for(page), "Explicit.")


@override_settings(ALLOWED_HOSTS=['*'])
class SeoHeadTagTests(TestCase):
    def setUp(self):
        for target in ('pages.signals.index_page', 'pages.signals.remove_page_from_search'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = Page.objects.create(
            title="Vitamin D and bones",
            status="published",
            content_md="Vitamin D helps the body absorb calcium, which is needed for strong bones in "
                       "children and adults alike.",
        )

    def test_page_has_canonical_on_primary_domain_without_query(self):
        response = self.client.get(f"/pages/{self.page.slug}/?utm_source=x", HTTP_HOST="www.vitamindwiki.com")
        self.assertContains(
            response, f'<link rel="canonical" href="{settings.SITE_BASE_URL}/pages/{self.page.slug}/">'
        )
        self.assertContains(response, '<meta name="description" content="Vitamin D helps the body absorb')

    def test_canonical_keeps_pagination(self):
        response = self.client.get("/pages/?page=2")
        self.assertContains(response, f'<link rel="canonical" href="{settings.SITE_BASE_URL}/pages/?page=2">')

    def test_robots_txt(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        body = response.content.decode()
        self.assertIn("Disallow: /admin/", body)
        self.assertIn(f"Sitemap: {settings.SITE_BASE_URL}/sitemap.xml", body)


class HttpsNginxConfigTests(SimpleTestCase):
    def _render(self, provisioning):
        path = Path(settings.BASE_DIR) / "deployment-manager.py"
        spec = importlib.util.spec_from_file_location("deployment_manager_seo", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        deployer = module.DockerDeployment.__new__(module.DockerDeployment)
        deployer.provisioning = provisioning
        return deployer._render_https_nginx()

    def test_alternate_domains_redirect_to_primary(self):
        config = self._render({"primary_domain": "example.com", "alt_domains": ["www.example.com"]})
        self.assertIn("return 301 https://example.com$request_uri;", config)
        self.assertIn("server_name www.example.com;", config)
        self.assertIn("server_name example.com;", config)
        self.assertNotIn("https://$host", config)
