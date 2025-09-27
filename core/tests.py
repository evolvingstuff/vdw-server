from django.test import SimpleTestCase

from helper_functions.markdown import render_markdown


class MarkdownFootnoteTests(SimpleTestCase):
    def test_preserves_single_footnote_number(self) -> None:
        markdown = """
Paragraph with footnote[^12].

[^12]: Original twelve
"""

        html = render_markdown(markdown)

        self.assertIn('href="#fn-12">12</a>', html)
        self.assertIn('<li id="fn-12" value="12">', html)

    def test_preserves_multiple_footnote_numbers(self) -> None:
        markdown = """
First[^12] second[^7].

[^12]: Twelve note
[^7]: Seven note
"""

        html = render_markdown(markdown)

        self.assertIn('href="#fn-12">12</a>', html)
        self.assertIn('href="#fn-7">7</a>', html)
        self.assertIn('<li id="fn-12" value="12">', html)
        self.assertIn('<li id="fn-7" value="7">', html)

    def test_duplicate_reference_keeps_single_definition(self) -> None:
        markdown = """
Repeat[^3] footnote[^3].

[^3]: Only once
"""

        html = render_markdown(markdown)

        self.assertEqual(html.count('href="#fn-3">3</a>'), 2)
        self.assertEqual(html.count('<li id="fn-3" value="3">'), 1)
        self.assertNotIn('footnoteBackLink', html)

    def test_consecutive_references_get_space(self) -> None:
        markdown = """
Combo[^4][^7]

[^4]: Four
[^7]: Seven
"""

        html = render_markdown(markdown)

        self.assertIn('</sup>&nbsp;<sup class="footnote-ref" id="fnref-7">', html)

    def test_backlink_removed_from_footnote(self) -> None:
        markdown = """
Link[^2]

[^2]: Example reference
"""

        html = render_markdown(markdown)

        self.assertNotIn('footnoteBackLink', html)
        self.assertNotIn('&#8617;', html)

    def test_autolinks_plain_http_urls(self) -> None:
        markdown = """
Links[^5]

[^5]: Visit https://example.com/path for more info.
"""

        html = render_markdown(markdown)

        self.assertIn('<a href="https://example.com/path">https://example.com/path</a>', html)

    def test_does_not_wrap_existing_anchor(self) -> None:
        markdown = """
Anchor[^6]

[^6]: <a href="https://example.com">https://example.com</a>
"""

        html = render_markdown(markdown)

        self.assertEqual(html.count('href="https://example.com"'), 1)

    def test_numeric_bullets_render_as_text(self) -> None:
        markdown = """
* 123. Literal value
- 456. Another literal
* 789. Third literal
"""

        html = render_markdown(markdown)

        self.assertIn('<li>123. Literal value</li>', html)
        self.assertIn('<li>456. Another literal</li>', html)
        self.assertIn('<li>789. Third literal</li>', html)
        self.assertNotIn('<ol start="123">', html)

    def test_nested_ordered_list_still_supported(self) -> None:
        markdown = """
* Parent
    1. Child
"""

        html = render_markdown(markdown)

        self.assertIn('<ol>', html)
        self.assertIn('<li>Child</li>', html)
