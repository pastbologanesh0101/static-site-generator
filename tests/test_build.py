"""Unit tests for the static site generator's build pipeline.

Run with either:
    python -m pytest tests/
    python -m unittest discover -s tests
"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build import FrontMatterError, build_site, slugify  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"


def write_content(base: Path, kind: str, filename: str, text: str) -> Path:
    sub = "posts" if kind == "post" else "pages"
    d = base / sub
    d.mkdir(parents=True, exist_ok=True)
    path = d / filename
    path.write_text(text, encoding="utf-8")
    return path


def make_post(title: str, date: str, tags, body: str, draft: bool = False) -> str:
    tags_yaml = "[" + ", ".join(tags) + "]" if tags else "[]"
    draft_line = "draft: true\n" if draft else ""
    return (
        "---\n"
        f"title: {title}\n"
        f"date: {date}\n"
        f"tags: {tags_yaml}\n"
        f"{draft_line}"
        "---\n\n"
        f"{body}\n"
    )


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.content_dir = self.root / "content"
        self.output_dir = self.root / "output"
        self.static_dir = self.root / "static"
        self.content_dir.mkdir()

    def build(self, **kwargs):
        static_dir = kwargs.pop("static_dir", self.static_dir)
        build_site(
            content_dir=self.content_dir,
            templates_dir=TEMPLATES_DIR,
            output_dir=self.output_dir,
            static_dir=static_dir,
            **kwargs,
        )

    # 1. Markdown body renders to correct HTML.
    def test_markdown_body_renders_to_html(self):
        write_content(
            self.content_dir,
            "post",
            "sample.md",
            make_post("Sample Post", "2026-01-10", ["python"],
                      "This is **bold** and *italic* text.\n\n## Section"),
        )
        self.build()
        html = (self.output_dir / "posts" / "sample" / "index.html").read_text()
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)
        self.assertIn("<h2>Section</h2>", html)

    # 2. Front matter fields (title/date/tags) reach the rendered template.
    def test_front_matter_fields_rendered_in_template(self):
        write_content(
            self.content_dir,
            "post",
            "meta-post.md",
            make_post("My Great Title", "2026-02-01", ["release", "python"], "Body text."),
        )
        self.build()
        html = (self.output_dir / "posts" / "meta-post" / "index.html").read_text()
        self.assertIn("<h1>My Great Title</h1>", html)
        self.assertIn("2026-02-01", html)
        self.assertIn("release", html)
        self.assertIn("python", html)

    # 3. Clean-URL output path convention for posts and pages.
    def test_clean_url_path_for_post_and_page(self):
        write_content(self.content_dir, "post", "hello-world.md",
                      make_post("Hello", "2026-01-01", [], "Hi"))
        write_content(self.content_dir, "page", "about.md",
                      "---\ntitle: About\n---\n\nAbout body.")
        self.build()
        self.assertTrue((self.output_dir / "posts" / "hello-world" / "index.html").is_file())
        self.assertTrue((self.output_dir / "about" / "index.html").is_file())

    # 4. Index lists posts sorted by date descending and excludes drafts.
    def test_index_sorted_desc_and_excludes_drafts(self):
        write_content(self.content_dir, "post", "a.md", make_post("Post A", "2026-01-01", [], "a"))
        write_content(self.content_dir, "post", "b.md", make_post("Post B", "2026-03-01", [], "b"))
        write_content(self.content_dir, "post", "c.md", make_post("Post C", "2026-02-01", [], "c"))
        write_content(self.content_dir, "post", "d-draft.md",
                      make_post("Draft D", "2026-04-01", [], "d", draft=True))
        self.build()
        index_html = (self.output_dir / "index.html").read_text()
        pos_b = index_html.index("Post B")
        pos_c = index_html.index("Post C")
        pos_a = index_html.index("Post A")
        self.assertLess(pos_b, pos_c)
        self.assertLess(pos_c, pos_a)
        self.assertNotIn("Draft D", index_html)

    # 5. A per-tag page lists exactly the posts with that tag.
    def test_tag_page_lists_exact_matching_posts(self):
        write_content(self.content_dir, "post", "p1.md",
                      make_post("Py Post", "2026-01-01", ["python"], "x"))
        write_content(self.content_dir, "post", "p2.md",
                      make_post("Other Post", "2026-01-02", ["golang"], "y"))
        write_content(self.content_dir, "post", "p3.md",
                      make_post("Py Post 2", "2026-01-03", ["python", "tutorial"], "z"))
        self.build()
        tag_html = (self.output_dir / "tags" / "python" / "index.html").read_text()
        self.assertIn("Py Post", tag_html)
        self.assertIn("Py Post 2", tag_html)
        self.assertNotIn("Other Post", tag_html)

    # 6. Static assets are copied byte-for-byte.
    def test_static_assets_copied_byte_for_byte(self):
        self.static_dir.mkdir(parents=True, exist_ok=True)
        css_dir = self.static_dir / "css"
        css_dir.mkdir()
        original_bytes = b"body { color: #123456; }\n\x00\x01binary-ish-bytes"
        (css_dir / "style.css").write_bytes(original_bytes)
        write_content(self.content_dir, "post", "p.md", make_post("P", "2026-01-01", [], "body"))
        self.build()
        copied = self.output_dir / "css" / "style.css"
        self.assertTrue(copied.is_file())
        self.assertEqual(copied.read_bytes(), original_bytes)

    # 7. RSS feed excludes drafts and includes correct post metadata.
    def test_rss_feed_excludes_drafts_and_has_correct_metadata(self):
        write_content(self.content_dir, "post", "pub.md",
                      make_post("Published Post", "2026-05-01", ["news"], "content here"))
        write_content(self.content_dir, "post", "hidden.md",
                      make_post("Hidden Post", "2026-06-01", [], "secret", draft=True))
        self.build(site_url="https://example.test")
        feed = (self.output_dir / "feed.xml").read_text()
        self.assertIn("Published Post", feed)
        self.assertIn("https://example.test/posts/pub/", feed)
        self.assertIn("01 May 2026", feed)
        self.assertNotIn("Hidden Post", feed)

    # 8. Building twice with unchanged inputs is byte-identical (determinism).
    def test_build_is_deterministic(self):
        write_content(self.content_dir, "post", "a.md",
                      make_post("A", "2026-01-01", ["x"], "content a"))
        write_content(self.content_dir, "post", "b.md",
                      make_post("B", "2026-02-01", ["y"], "content b"))
        write_content(self.content_dir, "page", "about.md", "---\ntitle: About\n---\n\nAbout.")
        self.static_dir.mkdir(parents=True, exist_ok=True)
        (self.static_dir / "style.css").write_text("body{}")

        output_1 = self.root / "output1"
        output_2 = self.root / "output2"
        build_site(self.content_dir, TEMPLATES_DIR, output_1, static_dir=self.static_dir)
        build_site(self.content_dir, TEMPLATES_DIR, output_2, static_dir=self.static_dir)

        rel_1 = sorted(str(p.relative_to(output_1)) for p in output_1.rglob("*") if p.is_file())
        rel_2 = sorted(str(p.relative_to(output_2)) for p in output_2.rglob("*") if p.is_file())
        self.assertEqual(rel_1, rel_2)

        def hash_tree(d: Path) -> str:
            h = hashlib.sha256()
            for rel in sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()):
                h.update(rel.encode("utf-8"))
                h.update((d / rel).read_bytes())
            return h.hexdigest()

        self.assertEqual(hash_tree(output_1), hash_tree(output_2))

    # 9. Missing required front matter fields -> clear error, not a crash.
    def test_missing_required_front_matter_raises_clear_error(self):
        write_content(self.content_dir, "post", "broken.md",
                      "---\ntags: [x]\n---\n\nNo title or date here.")
        with self.assertRaises(FrontMatterError) as ctx:
            self.build()
        message = str(ctx.exception)
        self.assertIn("title", message)
        self.assertIn("date", message)
        self.assertIn("broken.md", message)

    # 10. A draft post is buildable individually for preview, but absent
    #     from the index, its tag page, and the RSS feed.
    def test_draft_built_individually_but_absent_from_listing_and_feed(self):
        write_content(self.content_dir, "post", "visible.md",
                      make_post("Visible", "2026-01-01", [], "v"))
        write_content(self.content_dir, "post", "secret-draft.md",
                      make_post("Secret Draft", "2026-01-02", ["wip"], "s", draft=True))
        self.build()

        draft_page = self.output_dir / "posts" / "secret-draft" / "index.html"
        self.assertTrue(draft_page.is_file())
        self.assertIn("Secret Draft", draft_page.read_text())

        index_html = (self.output_dir / "index.html").read_text()
        feed_xml = (self.output_dir / "feed.xml").read_text()
        self.assertNotIn("Secret Draft", index_html)
        self.assertNotIn("Secret Draft", feed_xml)
        self.assertFalse((self.output_dir / "tags" / "wip").exists())

    # 11. A file with no front matter block at all fails clearly.
    def test_missing_front_matter_block_raises_error(self):
        d = self.content_dir / "posts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "nofm.md").write_text("# Just a heading\n\nNo front matter at all.")
        with self.assertRaises(FrontMatterError):
            self.build()

    # 12. An invalid date format fails clearly rather than crashing.
    def test_invalid_date_format_raises_clear_error(self):
        write_content(self.content_dir, "post", "baddate.md",
                      "---\ntitle: Bad Date\ndate: not-a-date\n---\n\nBody.")
        with self.assertRaises(FrontMatterError) as ctx:
            self.build()
        self.assertIn("date", str(ctx.exception))

    # 13. slugify produces stable, URL-safe slugs (used for tag pages).
    def test_slugify_produces_url_safe_slugs(self):
        self.assertEqual(slugify("Hello World!"), "hello-world")
        self.assertEqual(slugify("Python & Flask"), "python-flask")


if __name__ == "__main__":
    unittest.main()
