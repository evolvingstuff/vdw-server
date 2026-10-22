import sqlite3
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from helper_functions.link_review import HIGH, LIKELY, REVIEW, Resolver, analyze


def _make_db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE posts_post (slug TEXT, status TEXT, title TEXT, aliases TEXT, content_html TEXT)")
    conn.execute("CREATE TABLE tags_tag (name TEXT, slug TEXT)")
    conn.executemany("INSERT INTO posts_post VALUES (?, ?, ?, ?, ?)", [
        ('osteoporosis-medication-7x-more-effective', 'published',
         'Osteoporosis medication 7X more effective when more than 33 ng of vitamin D', '/Old+Osteo+Name+–+June+2011', ''),
        ('vitamin-d-is-one-of-only-2-ways-to-easily-prevent-nafld', 'published', 'Vitamin D is one of only 2 ways to easily prevent NAFLD', '', ''),
        ('source', 'published', 'Source page', '', (
            '<a href="/pages/moved58/">Osteoporosis medication 7X more effective when more than 33 ng of vitamin D – June 2011</a>'
            '<a href="/pages/old-osteo-name/">x</a>'
            '<a href="/pages/search-results/">PTSD</a>'
            '<a href="/pages/search-results/">Search VitaminDWiki for CPY24</a>'
            '<a href="/pages/tiki-download-filephp/">A PDF</a>'
            '<a href="/pages/vitamin-d-is-only-of-only-2-ways-to-easily-prevent-nafld/">typo</a>'
            '<a href="/pages/gone-forever-page-about-nothing-in-particular/">Gone page</a>'
            '<a href="/pages/source/">fine</a>'
        )),
    ])
    conn.executemany("INSERT INTO tags_tag VALUES (?, ?)", [('PTSD', 'ptsd')])
    conn.commit()
    conn.close()


class LinkReviewTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'db.sqlite3'
        _make_db(self.db)

    def _fixes(self):
        groups, total = analyze(self.db)
        found = {}
        for g in groups.values():
            for key, _ in g.fixes.items():
                found[(g.kind, g.target)] = g.fix_objects[key]
        return found, total

    def test_classifies_and_resolves(self):
        fixes, total = self._fixes()
        self.assertEqual(total, 7)  # the link to an existing page is not counted

        renamed = fixes[('missing', 'moved58')]
        self.assertEqual((renamed.url, renamed.confidence), ('/pages/osteoporosis-medication-7x-more-effective/', HIGH))
        alias = fixes[('missing', 'old-osteo-name')]
        self.assertEqual((alias.url, alias.confidence), ('/pages/osteoporosis-medication-7x-more-effective/', HIGH))
        typo = fixes[('missing', 'vitamin-d-is-only-of-only-2-ways-to-easily-prevent-nafld')]
        self.assertEqual(typo.url, '/pages/vitamin-d-is-one-of-only-2-ways-to-easily-prevent-nafld/')
        self.assertIn(typo.confidence, (HIGH, LIKELY))
        self.assertEqual(fixes[('missing', 'gone-forever-page-about-nothing-in-particular')].action, 'unlink')

        self.assertEqual(fixes[('search', 'Topic: PTSD')].url, '/tags/ptsd/')
        self.assertEqual(fixes[('search', 'Search: CPY24')].url, '/search/?q=CPY24')
        download = fixes[('download', 'Remove the link, keep the text')]
        self.assertEqual((download.action, download.confidence), ('unlink', REVIEW))

    def test_download_links_match_pdfs_in_s3(self):
        # "A PDF" is too generic to match a file name, so the linking page's title is used.
        pdfs = ['public/attachments/pdf/source-page---2010.pdf', 'public/attachments/jpg/x.jpg']
        groups, _ = analyze(self.db, pdfs)
        download = next(g for g in groups.values() if g.kind == 'download')
        fix = next(iter(download.fix_objects.values()))
        self.assertEqual(fix.action, 'file')
        self.assertEqual(fix.url, 'https://d378j1rmrlek7x.cloudfront.net/attachments/pdf/source-page---2010.pdf')
        self.assertEqual(fix.confidence, LIKELY)


class ApplyLinkFixesTests(SimpleTestCase):
    def test_repoints_markdown_and_html_links_matching_target_and_text(self):
        from helper_functions.link_fixes import apply_link_fixes, build_fix_map

        fix_map = build_fix_map([
            {'target': 'tiki-download-filephp', 'text': 'Toxic level - 2008', 'new_url': 'https://cdn/x.pdf'},
            {'target': 'search-results', 'text': 'PTSD', 'new_url': '/tags/ptsd/'},
        ])
        md = ('* [Toxic level - 2008](/pages/tiki-download-filephp/) file\n'
              '[**PTSD**](https://vitamindwiki.com/pages/search-results/)\n'
              '<a href="/pages/search-results/">PTSD</a>\n'
              '[Other text](/pages/search-results/)')
        new, count = apply_link_fixes(md, fix_map)
        self.assertEqual(count, 3)
        self.assertIn('[Toxic level - 2008](https://cdn/x.pdf) file', new)
        self.assertIn('[**PTSD**](/tags/ptsd/)', new)
        self.assertIn('<a href="/tags/ptsd/">PTSD</a>', new)
        self.assertIn('[Other text](/pages/search-results/)', new)  # different text: untouched
        self.assertEqual(apply_link_fixes(new, fix_map), (new, 0))
