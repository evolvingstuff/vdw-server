from django.test import SimpleTestCase

from helper_functions.markdown import render_markdown
from helper_functions.tables import convert_tiki_tables, prepare_tables_for_rendering


class TikiTableConversionTests(SimpleTestCase):
    def test_converts_tiki_table_with_text_before_and_after(self):
        md = (
            "**Table. Recommended Daily Allowance** ||Age| Male| Female| Pregnancy\n\n"
            "<6 mo| 30 mg| 30 mg\n\n"
            "14-18 y| 410 mg| 360 mg| 400 mg|| **a Footnote text.\n\nNext paragraph."
        )
        new, reports = convert_tiki_tables(md)
        self.assertEqual([r.status for r in reports], ['converted'])
        self.assertIn("**Table. Recommended Daily Allowance**\n\n| Age | Male | Female | Pregnancy |", new)
        self.assertIn("| <6 mo | 30 mg | 30 mg |  |", new)
        self.assertIn("| 14-18 y | 410 mg | 360 mg | 400 mg |\n\n**a Footnote text.", new)
        self.assertIn("Next paragraph.", new)
        self.assertNotIn("||", new)

    def test_single_line_break_inside_row_becomes_br(self):
        new, _ = convert_tiki_tables("||Name|Value\nmore\n\nA|1||")
        self.assertIn("| Name | Value<br>more |", new)

    def test_numeric_first_row_gets_empty_header(self):
        new, reports = convert_tiki_tables("||All Cause|1.71 X\n\nHeart|1.39 X||")
        self.assertFalse(reports[0].header_from_first_row)
        self.assertTrue(new.startswith("|  |  |\n| --- | --- |\n| All Cause | 1.71 X |"))

    def test_empty_corner_means_header(self):
        new, reports = convert_tiki_tables("|| |Low|High\n\nBMI| 19|16||")
        self.assertTrue(reports[0].header_from_first_row)
        self.assertTrue(new.startswith("|  | Low | High |"))

    def test_heading_marker_before_table_is_dropped(self):
        new, reports = convert_tiki_tables("### || |Low|High\n\nBMI| 19|16||")
        self.assertNotIn("###", new)
        self.assertIn("heading marker before the table was removed", reports[0].notes)

    def test_markdown_table_empty_cells_are_not_tiki(self):
        md = "| a || b |\n| --- | --- | --- |\n| 1 || 2 |"
        self.assertEqual(convert_tiki_tables(md), (md, []))

    def test_too_many_columns_is_skipped(self):
        md = "||" + "|".join(str(i) for i in range(20)) + "\n\nx|y||"
        new, reports = convert_tiki_tables(md)
        self.assertEqual(new, md)
        self.assertEqual(reports[0].status, 'skipped')

    def test_idempotent(self):
        once, _ = convert_tiki_tables("||A|B\n\n1|2||")
        self.assertEqual(convert_tiki_tables(once), (once, []))


class TableRenderingTests(SimpleTestCase):
    TABLE = "| a | b |\n| --- | --- |\n| 1 | 2 |"

    def test_table_inside_box_renders_and_rest_of_box_is_untouched(self):
        md = f'<div class="border">\n    indented text stays as is\n\n{self.TABLE}\n</div>'
        html = render_markdown(md)
        self.assertIn('<table>', html)
        self.assertIn('    indented text stays as is', html)
        self.assertNotIn('<pre>', html)

    def test_table_inside_box_with_bold_wrapper(self):
        md = f'<div class="border">\n<strong>\n\n{self.TABLE}\n\n</strong>\n</div>'
        self.assertIn('<table>', render_markdown(md))

    def test_table_without_blank_line_before(self):
        self.assertIn('<table>', render_markdown(f"Some text\n{self.TABLE}"))

    def test_tiki_table_renders(self):
        self.assertIn('<table>', render_markdown("||A|B\n\n1|2||"))

    def test_plain_markdown_unchanged(self):
        md = "# Title\n\nSome *text* with a | pipe.\n\n- item"
        self.assertEqual(prepare_tables_for_rendering(md), md)
