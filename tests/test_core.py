"""Tests for the parts of Open Shelf that don't need a window or a network."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openshelf import catalog, net  # noqa: E402
from openshelf.bookfile import BookError, EpubBook, TextBook, parse_ocr_pages  # noqa: E402
from openshelf.db import Library  # noqa: E402
from openshelf.sanitize import clean_css, sanitize_html  # noqa: E402


def item(files, **meta):
    return {"metadata": {"identifier": "x", "mediatype": "texts", "title": "T", **meta}, "files": files}


class CatalogTests(unittest.TestCase):
    def test_adult_filter_wraps_every_query(self):
        catalog.set_adult_filter(True)
        self.addCleanup(catalog.set_adult_filter, False)
        query = parse_qs(urlparse(catalog.search_url("a OR b")).query)["q"][0]
        self.assertTrue(query.startswith("(a OR b) AND NOT title:("))
        self.assertIn("NOT subject:(erotic", query)
        catalog.set_adult_filter(False)
        self.assertEqual(parse_qs(urlparse(catalog.search_url("a OR b")).query)["q"][0], "a OR b")

    def test_free_query_excludes_lending(self):
        query = catalog.search_query("dracula")
        self.assertIn("NOT collection:(printdisabled", query)
        self.assertIn("NOT access-restricted-item:true", query)

    def test_user_text_cannot_inject_query_syntax(self):
        query = catalog.user_query('x" OR collection:printdisabled) (')
        self.assertNotIn('" OR collection:', query)
        self.assertNotIn(":printdisabled", query)
        self.assertEqual(catalog.user_query('"()*:'), "")

    def test_clean_author(self):
        self.assertEqual(catalog.clean_author("Austen, Jane, 1775-1817"), "Jane Austen")
        self.assertEqual(catalog.clean_author(["Baum, L. Frank (Lyman Frank), 1856-1919"]), "L. Frank Baum")
        self.assertEqual(catalog.clean_author("Mary Wollstonecraft Shelley"), "Mary Wollstonecraft Shelley")
        self.assertEqual(catalog.clean_author(None), "Unknown author")

    def test_private_files_are_never_offered(self):
        plan = catalog.analyze_item(item([
            {"name": "a.epub", "format": "EPUB", "private": "true", "source": "derivative"},
            {"name": "a.pdf", "format": "Text PDF", "private": "true"},
            {"name": "a_lcp.epub", "format": "LCP Encrypted EPUB"},
            {"name": "a_encrypted.pdf", "format": "ACS Encrypted PDF"},
        ]))
        self.assertFalse(plan["available"])

    def test_restricted_item_is_unavailable(self):
        plan = catalog.analyze_item(item(
            [{"name": "a.epub", "format": "EPUB", "source": "original"}],
            **{"access-restricted-item": "true"}))
        self.assertFalse(plan["available"])

    def test_smart_pick_prefers_born_digital_epub(self):
        plan = catalog.analyze_item(item([
            {"name": "pg1.epub", "format": "EPUB", "source": "original"},
            {"name": "pg1-images.epub", "format": "EPUB", "source": "original"},
            {"name": "a_jp2.zip", "format": "Single Page Processed JP2 ZIP"},
        ], imagecount="100"))
        self.assertEqual(plan["default_view"], "text")
        self.assertEqual(plan["epub"]["name"], "pg1-images.epub")
        self.assertEqual(plan["views"], ["text"])

    def test_smart_pick_prefers_scans_over_ocr_epub(self):
        plan = catalog.analyze_item(item([
            {"name": "a.epub", "format": "EPUB", "source": "derivative"},
            {"name": "a_jp2.zip", "format": "Single Page Processed JP2 ZIP"},
            {"name": "a.pdf", "format": "Text PDF"},
            {"name": "a_bw.pdf", "format": "Grayscale PDF"},
            {"name": "a_djvu.txt", "format": "DjVuTXT"},
        ], imagecount="518"))
        self.assertEqual(plan["default_view"], "scan")
        self.assertEqual(plan["pages"], 518)
        self.assertEqual(plan["pdf"]["name"], "a.pdf")

    def test_audio_tracks_ordered_and_low_bitrate_preferred(self):
        plan = catalog.analyze_item({"metadata": {"identifier": "b", "mediatype": "audio", "title": "A"}, "files": [
            {"name": "c_10.mp3", "format": "VBR MP3", "track": "10"},
            {"name": "c_2.mp3", "format": "VBR MP3", "track": "2"},
            {"name": "c_10_64kb.mp3", "format": "64Kbps MP3", "original": "c_10.mp3", "title": "Ten", "length": "08:26"},
            {"name": "c_2_64kb.mp3", "format": "64Kbps MP3", "original": "c_2.mp3", "title": "Two", "length": "507.5"},
        ]})
        self.assertEqual([t["title"] for t in plan["tracks"]], ["Two", "Ten"])
        self.assertEqual(plan["tracks"][1]["seconds"], 506)
        self.assertEqual(plan["tracks"][0]["seconds"], 507.5)

    def test_dedupe_prefers_gutenberg(self):
        books = [
            {"title": "Pride and prejudice", "author": "Jane Austen", "id": "a", "collections": ["americana"]},
            {"title": "Pride and Prejudice: A Novel", "author": "Austen", "id": "b", "collections": ["toronto"]},
            {"title": "Pride and Prejudice", "author": "Jane Austen", "id": "c", "collections": ["gutenberg"]},
            {"title": "Emma", "author": "Jane Austen", "id": "d", "collections": []},
        ]
        self.assertEqual([b["id"] for b in catalog.dedupe_editions(books)], ["c", "d"])


class SanitizerTests(unittest.TestCase):
    def test_scripts_and_handlers_removed(self):
        html, text, _ = sanitize_html(
            '<html><head><title>no</title><script>evil()</script></head><body>'
            '<p onclick="evil()" class="a">Hi<script>evil()</script></p>'
            '<iframe src="http://x"><p>inner</p></iframe>'
            '<img src="x.png" onerror="evil()"/></body></html>')
        self.assertNotIn("evil", html)
        self.assertNotIn("iframe", html)
        self.assertNotIn("inner", html)
        self.assertNotIn("<title", html)
        self.assertIn('class="a"', html)
        self.assertEqual(text, "Hi")

    def test_javascript_links_dropped_without_resolver(self):
        html, _, _ = sanitize_html('<a href="javascript:evil()">x</a>')
        self.assertEqual(html, "<a>x</a>")

    def test_entities_stay_escaped(self):
        html, text, _ = sanitize_html("<p>&lt;script&gt;a &amp; b</p>")
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(text, "<script>a & b")

    def test_self_closed_anchor_is_closed(self):
        html, _, _ = sanitize_html('<a id="x"/><p>t</p><br/>')
        self.assertEqual(html, '<a id="x"></a><p>t</p><br>')

    def test_svg_cover_image_becomes_img(self):
        html, _, _ = sanitize_html(
            '<svg><image xlink:href="c.jpg"/></svg>', resolve_image=lambda s: "osbook:/t/" + s)
        self.assertEqual(html, '<img src="osbook:/t/c.jpg">')

    def test_inline_style_filtered(self):
        html, _, _ = sanitize_html('<p style="text-align:center;position:fixed;background:url(http://x)">a</p>')
        self.assertEqual(html, '<p style="text-align:center">a</p>')

    def test_css_cannot_load_or_escape(self):
        css = clean_css('@import url(http://x); p{color:red;background:url(http://y);text-align:center}')
        self.assertNotIn("http", css)
        self.assertNotIn("@import", css)
        self.assertIn("text-align:center", css)
        self.assertEqual(clean_css("p{a:b}} body{x:y"), "")


class BookFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "b.epub"
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("META-INF/container.xml", (
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                '<rootfile full-path="OEBPS/c.opf"/></rootfiles></container>'))
            z.writestr("OEBPS/c.opf", (
                '<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<metadata><dc:title>Test Book</dc:title><dc:creator>Ann Author</dc:creator>'
                '<meta name="cover" content="img"/></metadata><manifest>'
                '<item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="two" href="sub/two.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="img" href="c.png" media-type="image/png"/>'
                '<item id="css" href="s.css" media-type="text/css"/>'
                '<item id="ncx" href="t.ncx" media-type="application/x-dtbncx+xml"/>'
                '</manifest><spine toc="ncx"><itemref idref="one"/><itemref idref="two"/></spine></package>'))
            z.writestr("OEBPS/t.ncx", (
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
                '<navPoint><navLabel><text>First</text></navLabel><content src="one.xhtml"/></navPoint>'
                '<navPoint><navLabel><text>Second</text></navLabel><content src="sub/two.xhtml#mid"/></navPoint>'
                '</navMap></ncx>'))
            z.writestr("OEBPS/s.css", "p{text-align:center;position:fixed}")
            z.writestr("OEBPS/c.png", b"png")
            z.writestr("OEBPS/secret.js", b"evil")
            z.writestr("OEBPS/one.xhtml", (
                '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
                '<link rel="stylesheet" href="s.css"/></head><body><p>The whale swam.</p>'
                '<img src="c.png"/><a href="sub/two.xhtml#mid">next</a>'
                '<a href="https://example.com/">web</a></body></html>'))
            z.writestr("OEBPS/sub/two.xhtml", (
                '<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="mid">A second whale.</p></body></html>'))

    def tearDown(self):
        self.dir.cleanup()

    def test_package_and_toc(self):
        book = EpubBook(self.path, "tok")
        self.assertEqual((book.title, book.author), ("Test Book", "Ann Author"))
        self.assertEqual(book.length, 2)
        self.assertEqual([(e.title, e.spine, e.fragment) for e in book.toc], [("First", 0, ""), ("Second", 1, "mid")])
        self.assertEqual(book.cover_image(), b"png")

    def test_chapter_rewrites_links_and_resources(self):
        chapter = EpubBook(self.path, "tok").chapter(0)
        self.assertIn('src="osbook:/tok/OEBPS/c.png"', chapter.html)
        self.assertIn('data-os-spine="1"', chapter.html)
        self.assertIn('data-os-frag="mid"', chapter.html)
        self.assertIn('data-os-ext="https://example.com/"', chapter.html)
        self.assertIn("text-align:center", chapter.css)
        self.assertNotIn("position", chapter.css)

    def test_resource_only_serves_images_inside_book(self):
        book = EpubBook(self.path, "tok")
        self.assertEqual(book.resource("OEBPS/c.png"), (b"png", "image/png"))
        self.assertIsNone(book.resource("OEBPS/secret.js"))
        self.assertIsNone(book.resource("../../etc/passwd"))

    def test_search(self):
        hits = EpubBook(self.path, "tok").search("WHALE")
        self.assertEqual([(h.spine, h.offset) for h in hits], [(0, 4), (1, 9)])
        self.assertEqual(hits[1].chapter, "Second")

    def test_fraction_round_trip(self):
        book = EpubBook(self.path, "tok")
        for spine, part in ((0, 0.25), (1, 0.5)):
            self.assertEqual(book.locate(book.fraction(spine, part))[0], spine)
            self.assertAlmostEqual(book.locate(book.fraction(spine, part))[1], part, places=6)

    def test_ocr_pages(self):
        xml = ('<BODY><OBJECT data="a"><LINE><WORD coords="1">Mr.</WORD> <WORD coords="2">Darcy</WORD></LINE></OBJECT>'
               '<OBJECT data="b"></OBJECT><OBJECT data="c"><WORD>A&amp;B</WORD></OBJECT></BODY>')
        self.assertEqual(parse_ocr_pages(xml), ["Mr. Darcy", "", "A&B"])

    def test_bad_file_raises(self):
        bad = Path(self.dir.name) / "bad.epub"
        bad.write_text("not a zip")
        with self.assertRaises(BookError):
            EpubBook(bad)

    def test_text_book_splits_chapters(self):
        path = Path(self.dir.name) / "t.txt"
        filler = "word " * 300
        path.write_text(f"Intro\n\n{filler}\n\nCHAPTER I\n\n{filler}\n\nCHAPTER II\n\n{filler}\n\n<b>x</b>")
        book = TextBook(path, "T")
        self.assertEqual(book.length, 3)
        self.assertEqual([e.title for e in book.toc], ["Beginning", "Chapter I", "Chapter Ii"])
        self.assertIn("<h2>CHAPTER II</h2>", book.chapter(2).html)
        self.assertIn("&lt;b&gt;", book.chapter(2).html)


class ClearHistoryTests(unittest.TestCase):
    def test_keeps_books_and_notes_but_forgets_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            lib = Library(os.path.join(folder, "t.db"))
            for book_id in ("kept", "wanted", "noted", "streamed"):
                lib.add_book({"id": book_id, "title": book_id})
                lib.mark_opened(book_id)
                lib.save_position(book_id, "text", {"spine": 1}, 0.5)
                lib.add_activity(book_id, 600)
            lib.set_local("kept", {"epub": "a.epub"})
            lib.set_finished("kept", True)
            lib.set_want("wanted", True)
            highlight = lib.add_highlight("noted", 0, 0, 4, "text")

            lib.clear_history()

            self.assertFalse(lib.has_book("streamed"))
            self.assertEqual(lib.books("reading"), [])
            self.assertEqual(lib.books("finished"), [])
            self.assertEqual(lib.minutes_on(date.today()), 0)
            kept = lib.book("kept")
            self.assertEqual((kept["progress"], kept["position"], kept["local"]), (0, {}, {"epub": "a.epub"}))
            self.assertEqual([b["id"] for b in lib.books("want")], ["wanted"])
            self.assertEqual([h["id"] for h in lib.highlights("noted")], [highlight])
            lib.close()


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lib = Library(os.path.join(self.dir.name, "t.db"))
        self.lib.add_book({"id": "a", "title": "A", "author": "X", "subjects": ["Whales"]})

    def tearDown(self):
        self.lib.close()
        self.dir.cleanup()

    def test_re_adding_keeps_reader_state(self):
        self.lib.save_position("a", "text", {"spine": 3}, 0.4)
        self.lib.set_want("a", True)
        self.lib.add_book({"id": "a", "title": "New title", "author": "X"})
        book = self.lib.book("a")
        self.assertEqual(book["title"], "New title")
        self.assertEqual(book["progress"], 0.4)
        self.assertEqual(book["want"], 1)
        self.assertEqual(book["position"]["text"]["spine"], 3)
        self.assertEqual(book["subjects"], ["Whales"])

    def test_finishing_is_automatic_at_the_end_and_clears_want(self):
        self.lib.set_want("a", True)
        self.lib.save_position("a", "text", {}, 0.5)
        self.assertIsNone(self.lib.book("a")["finished_at"])
        self.lib.save_position("a", "text", {}, 0.99)
        book = self.lib.book("a")
        self.assertIsNotNone(book["finished_at"])
        self.assertEqual(book["want"], 0)
        self.assertEqual(self.lib.finished_in_year(date.today().year), 1)

    def test_shelves(self):
        self.lib.add_book({"id": "b", "title": "B", "author": "Y", "kind": "audio"})
        self.lib.mark_opened("a")
        self.assertEqual([b["id"] for b in self.lib.books("reading")], ["a"])
        self.lib.set_local("b", {"track": "/tmp/x.mp3"})
        self.assertEqual([b["id"] for b in self.lib.books("library")], ["b"])
        self.assertEqual(len(self.lib.books("all", "title")), 2)

    def test_library_is_only_downloads_and_imports(self):
        self.lib.mark_opened("a")
        self.assertEqual(self.lib.books("library"), [])
        self.lib.set_local("a", {"epub": "/tmp/a.epub"})
        self.assertEqual([b["id"] for b in self.lib.books("library")], ["a"])

    def test_clear_recent_keeps_the_place(self):
        self.lib.mark_opened("a")
        self.lib.save_position("a", "text", {"spine": 1, "offset": 9}, 0.01)
        self.lib.clear_recent("a")
        self.assertEqual(self.lib.books("reading"), [])
        self.assertEqual(self.lib.book("a")["position"]["text"]["offset"], 9)

    def test_want_clears_once_properly_started(self):
        self.lib.set_want("a", True)
        self.lib.save_position("a", "text", {"spine": 0, "offset": 1}, 0.005)
        self.assertEqual(self.lib.book("a")["want"], 1)
        self.lib.save_position("a", "text", {"spine": 2, "offset": 1}, 0.03)
        self.assertEqual(self.lib.book("a")["want"], 0)

    def test_best_streak(self):
        today = date.today()
        goal = self.lib.daily_goal() * 60
        for back in (10, 11, 12, 20):
            self.lib.add_activity("a", goal, today - timedelta(days=back))
        self.assertEqual(self.lib.best_streak(), 3)

    def test_bookmark_notes(self):
        mark = self.lib.add_bookmark("a", "scan", 0, 0, 4, "Page 5")
        self.lib.update_bookmark(mark, "worth rereading")
        self.assertEqual(self.lib.bookmarks("a")[0]["note"], "worth rereading")

    def test_old_database_gains_bookmark_notes(self):
        path = Path(self.dir.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE bookmarks(id INTEGER PRIMARY KEY, book_id TEXT, view TEXT, spine INT, "
                     "offset INT, page INT, label TEXT, created REAL)")
        conn.execute("INSERT INTO bookmarks(book_id, view, spine, offset, page, label, created) "
                     "VALUES('x', 'text', 0, 0, 0, 'old', 1)")
        conn.commit()
        conn.close()
        library = Library(path)
        self.assertEqual(library.bookmarks("x")[0]["label"], "old")
        self.assertEqual(library.bookmarks("x")[0]["note"], "")

    def test_a_page_already_on_its_way_is_not_requested_twice(self):
        class FakeNet:
            def __init__(self):
                self.calls = []

            def get_json(self, url, callback, owner=None, **options):
                self.calls.append(callback)

        fake = FakeNet()
        search = net.Search.__new__(net.Search)
        search.net, search._cache, search._fetching = fake, {}, {}
        got = []
        search.run("q", lambda *r: got.append("ahead"), page=2)
        search.run("q", lambda *r: got.append("click"), page=2)
        self.assertEqual(len(fake.calls), 1)
        fake.calls[0]({"response": {"docs": [], "numFound": 0}}, "")
        self.assertEqual(got, ["ahead", "click"])

    def test_every_browse_count_ships_with_the_app(self):
        path = Path(catalog.__file__).parent / "assets" / "catalog_counts.json"
        known = json.loads(path.read_text())
        missing = [q for q in catalog.browse_count_queries() if catalog.count_key(q) not in known]
        self.assertEqual(missing, [], "Run tools/update_counts.py after changing a category.")

    def test_collections_and_cascade(self):
        cid = self.lib.create_collection("Favorites")
        self.assertEqual(self.lib.create_collection("favorites"), cid)
        self.lib.add_to_collection(cid, "a")
        self.assertEqual([b["id"] for b in self.lib.books("collection", collection=cid)], ["a"])
        self.lib.add_highlight("a", 0, 1, 5, "text")
        self.lib.remove_book("a")
        self.assertEqual(self.lib.books("collection", collection=cid), [])
        self.assertEqual(self.lib.highlights("a"), [])

    def test_streak_counts_goal_days_and_tolerates_unfinished_today(self):
        today = date(2026, 3, 10)
        self.lib.set_setting("daily_goal", 10)
        for back in (1, 2, 3):
            self.lib.add_activity("a", 600, today - timedelta(days=back))
        self.lib.add_activity("a", 60, today - timedelta(days=4))
        self.assertEqual(self.lib.streak(today), 3)
        self.lib.add_activity("a", 600, today)
        self.assertEqual(self.lib.streak(today), 4)

    def test_highlight_crud(self):
        hid = self.lib.add_highlight("a", 2, 10, 20, "hello", "blue")
        self.lib.update_highlight(hid, note="n", color="pink")
        got = self.lib.highlights("a", 2)[0]
        self.assertEqual((got["note"], got["color"]), ("n", "pink"))
        self.lib.delete_highlight(hid)
        self.assertEqual(self.lib.highlights("a"), [])


if __name__ == "__main__":
    unittest.main()
