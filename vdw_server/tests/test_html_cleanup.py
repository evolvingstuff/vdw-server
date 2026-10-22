from django.test import SimpleTestCase

from helper_functions.html_cleanup import balance_divs, remove_empty_table_headers, unwrap_legacy_code_blocks
from helper_functions.markdown import render_markdown


class BalanceDivsTests(SimpleTestCase):
    def test_drops_stray_closing_div(self):
        self.assertEqual(balance_divs('<p>a</p></div><table></table>'), '<p>a</p><table></table>')

    def test_closes_unclosed_div(self):
        self.assertEqual(balance_divs('<div class="box"><p>a</p>'), '<div class="box"><p>a</p></div>')

    def test_balanced_html_untouched_and_idempotent(self):
        html = '<div><div>x</div></div><p>y</p>'
        self.assertEqual(balance_divs(html), html)
        once = balance_divs('</div><div>')
        self.assertEqual(balance_divs(once), once)


class LegacyCodeBlockTests(SimpleTestCase):
    def test_escaped_link_becomes_real_link(self):
        html = '<pre><code>  1. &lt;a href="/pages/x/"&gt;Magnesium and Vitamin D&lt;/a&gt; contains\n</code></pre>'
        self.assertEqual(
            unwrap_legacy_code_blocks(html),
            '<p class="legacy-text">1. <a href="/pages/x/">Magnesium and Vitamin D</a> contains</p>',
        )

    def test_plain_text_keeps_line_breaks_and_escaped_symbols(self):
        html = '<pre><code>  line one &lt; 5 ng\n  line two\n</code></pre>'
        self.assertEqual(unwrap_legacy_code_blocks(html), '<p class="legacy-text">line one &lt; 5 ng<br>\nline two</p>')

    def test_empty_block_removed(self):
        self.assertEqual(unwrap_legacy_code_blocks('<p>a</p><pre><code>      \n</code></pre>'), '<p>a</p>')

    def test_render_markdown_applies_cleanup(self):
        html = render_markdown('Text\n\n      <a href="/pages/x/">Link</a>\n\n</div>\n\nAfter')
        self.assertNotIn('<pre>', html)
        self.assertIn('<a href="/pages/x/">Link</a>', html)
        self.assertNotIn('</div>', html)


class EmptyTableHeaderTests(SimpleTestCase):
    def test_empty_header_row_removed(self):
        html = render_markdown("| | |\n| --- | --- |\n| All Cause | 1.71 X |")
        self.assertNotIn('<thead>', html)
        self.assertIn('<td>All Cause</td>', html)

    def test_real_header_kept(self):
        html = render_markdown("| Age | Male |\n| --- | --- |\n| 1-3 y | 80 mg |")
        self.assertIn('<th>Age</th>', html)
        self.assertEqual(remove_empty_table_headers(html), html)
