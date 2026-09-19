#!/usr/bin/env python3
"""Static Site Generator - build.py

A small, Jekyll/Hugo-style build-time site generator.

It parses Markdown content files with YAML front matter, renders each one
through a Jinja2 template, and writes a fully static HTML site to an
output directory. See README.md for the front matter schema, template
variables, and the clean-URL convention.

Usage:
    python build.py --content content/ --templates templates/ --output output/
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

import markdown as markdown_lib
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

FRONT_MATTER_DELIM = "---"
REQUIRED_FIELDS_POST = ("title", "date")
REQUIRED_FIELDS_PAGE = ("title",)


class FrontMatterError(ValueError):
    """Raised when a content file has missing or malformed front matter.

    Carries a clear, human-readable message identifying the offending
    file and field(s) so a build failure never looks like a generic
    Python crash.
    """


@dataclass
class ContentItem:
    """A single piece of content (post or page) parsed from a Markdown file."""

    source_path: Path
    kind: str  # "post" or "page"
    metadata: Dict[str, Any]
    body_html: str

    @property
    def title(self) -> str:
        return self.metadata["title"]

    @property
    def date(self) -> Optional[datetime]:
        return self.metadata.get("date")

    @property
    def tags(self) -> List[str]:
        tags = self.metadata.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return list(tags)

    @property
    def draft(self) -> bool:
        return bool(self.metadata.get("draft", False))

    @property
    def layout(self) -> str:
        default = "post.html" if self.kind == "post" else "page.html"
        return self.metadata.get("layout", default)

    @property
    def slug(self) -> str:
        return self.metadata.get("slug") or self.source_path.stem

    @property
    def output_dir_rel(self) -> Path:
        """Output directory (relative to the output root) for this item."""
        if self.kind == "post":
            return Path("posts") / self.slug
        return Path(self.slug)

    @property
    def url(self) -> str:
        """Clean-URL path, e.g. '/posts/hello-world/'."""
        return "/" + str(self.output_dir_rel).replace("\\", "/") + "/"


def slugify(value: str) -> str:
    """Turn an arbitrary tag/name string into a URL-safe slug."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "tag"


def parse_front_matter(text: str, source_name: str = "<unknown>"):
    """Split a content file's text into (metadata dict, markdown body).

    Raises FrontMatterError with a clear message if the front matter
    block is missing, unterminated, or not a YAML mapping.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIM:
        raise FrontMatterError(
            f"{source_name}: missing front matter "
            f"(file must start with a '{FRONT_MATTER_DELIM}' line)"
        )
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONT_MATTER_DELIM:
            end_idx = i
            break
    if end_idx is None:
        raise FrontMatterError(
            f"{source_name}: front matter block not closed with '{FRONT_MATTER_DELIM}'"
        )
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        metadata = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise FrontMatterError(f"{source_name}: invalid YAML front matter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise FrontMatterError(f"{source_name}: front matter must be a YAML mapping")
    return metadata, body


def validate_metadata(metadata: Dict[str, Any], kind: str, source_name: str) -> None:
    """Validate required fields and normalize the 'date' field in place.

    Raises FrontMatterError with a clear, actionable message on failure.
    """
    required = REQUIRED_FIELDS_POST if kind == "post" else REQUIRED_FIELDS_PAGE
    missing = [f for f in required if metadata.get(f) in (None, "")]
    if missing:
        raise FrontMatterError(
            f"{source_name}: missing required front matter field(s): "
            f"{', '.join(missing)}"
        )
    if "date" in metadata:
        d = metadata["date"]
        if isinstance(d, datetime):
            pass
        elif isinstance(d, date):
            metadata["date"] = datetime(d.year, d.month, d.day)
        elif isinstance(d, str):
            try:
                metadata["date"] = datetime.strptime(d, "%Y-%m-%d")
            except ValueError as exc:
                raise FrontMatterError(
                    f"{source_name}: invalid 'date' value '{d}', expected format YYYY-MM-DD"
                ) from exc
        else:
            raise FrontMatterError(
                f"{source_name}: 'date' field has unsupported type {type(d).__name__}"
            )


def load_content_item(path: Path, kind: str) -> ContentItem:
    text = path.read_text(encoding="utf-8")
    metadata, body_md = parse_front_matter(text, source_name=str(path))
    validate_metadata(metadata, kind, source_name=str(path))
    body_html = markdown_lib.markdown(
        body_md, extensions=["fenced_code", "tables", "extra", "sane_lists"]
    )
    return ContentItem(source_path=path, kind=kind, metadata=metadata, body_html=body_html)


def load_all_content(content_dir: Path) -> List[ContentItem]:
    """Load every post and page under content_dir, in deterministic order."""
    items: List[ContentItem] = []
    posts_dir = content_dir / "posts"
    pages_dir = content_dir / "pages"
    if posts_dir.is_dir():
        for p in sorted(posts_dir.glob("*.md")):
            items.append(load_content_item(p, "post"))
    if pages_dir.is_dir():
        for p in sorted(pages_dir.glob("*.md")):
            items.append(load_content_item(p, "page"))
    return items


def published_posts(posts: List[ContentItem]) -> List[ContentItem]:
    """Non-draft posts, sorted by date descending (newest first)."""
    pubs = [p for p in posts if not p.draft]
    return sorted(pubs, key=lambda p: p.date, reverse=True)


def collect_tags(posts: List[ContentItem]) -> Dict[str, List[ContentItem]]:
    """Map tag name -> posts with that tag, each list sorted by date descending."""
    tag_map: Dict[str, List[ContentItem]] = {}
    for post in posts:
        for tag in post.tags:
            tag_map.setdefault(tag, []).append(post)
    for tag, tag_posts in tag_map.items():
        tag_map[tag] = sorted(tag_posts, key=lambda p: p.date, reverse=True)
    return tag_map


def make_env(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_item(env: Environment, item: ContentItem, published: List[ContentItem]) -> str:
    template = env.get_template(item.layout)
    context = {
        "title": item.title,
        "date": item.date,
        "tags": item.tags,
        "draft": item.draft,
        "content": Markup(item.body_html),
        "metadata": item.metadata,
        "url": item.url,
        "posts": published,
    }
    return template.render(**context)


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_rss(
    posts: List[ContentItem],
    site_url: str = "https://example.com",
    site_title: str = "Static Site Generator Demo",
) -> str:
    """Build a minimal RSS 2.0 feed from published (non-draft) posts.

    No "generated at" timestamp is included, so the output is a pure
    function of the posts themselves (needed for deterministic builds).
    """
    site_url = site_url.rstrip("/")
    items_xml = []
    for post in posts:
        link = f"{site_url}{post.url}"
        pub_date = post.date.strftime("%a, %d %b %Y 00:00:00 +0000")
        items_xml.append(
            "  <item>\n"
            f"    <title>{escape(post.title)}</title>\n"
            f"    <link>{escape(link)}</link>\n"
            f"    <guid>{escape(link)}</guid>\n"
            f"    <pubDate>{pub_date}</pubDate>\n"
            f"    <description><![CDATA[{post.body_html}]]></description>\n"
            "  </item>"
        )
    items_block = "\n".join(items_xml)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "<channel>\n"
        f"  <title>{escape(site_title)}</title>\n"
        f"  <link>{escape(site_url)}</link>\n"
        "  <description>Latest posts</description>\n"
        f"{items_block}\n"
        "</channel>\n"
        "</rss>\n"
    )


def copy_static(static_dir: Path, output_dir: Path) -> None:
    """Copy static/ into output/ unchanged, preserving directory structure."""
    if not static_dir.is_dir():
        return
    for src in sorted(static_dir.rglob("*")):
        rel = src.relative_to(static_dir)
        dst = output_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def build_site(
    content_dir: Path,
    templates_dir: Path,
    output_dir: Path,
    static_dir: Optional[Path] = None,
    site_url: str = "https://example.com",
    clean: bool = True,
) -> None:
    """Build the full site. Deterministic and idempotent: running this
    twice with unchanged inputs produces byte-identical output, because
    the output directory is always rebuilt from a clean slate and no
    wall-clock timestamps leak into the generated files.
    """
    content_dir = Path(content_dir)
    templates_dir = Path(templates_dir)
    output_dir = Path(output_dir)
    static_dir = Path(static_dir) if static_dir is not None else content_dir.parent / "static"

    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    env = make_env(templates_dir)
    items = load_all_content(content_dir)
    posts = [i for i in items if i.kind == "post"]
    pub_posts = published_posts(posts)

    # Render every item, including drafts, so a draft can still be
    # previewed individually even though it's excluded from listings.
    for item in items:
        html = render_item(env, item, pub_posts)
        write_file(output_dir / item.output_dir_rel / "index.html", html)

    index_template = env.get_template("index.html")
    write_file(
        output_dir / "index.html",
        index_template.render(posts=pub_posts, title="Home"),
    )

    tag_map = collect_tags(pub_posts)
    tag_template = env.get_template("tag.html")
    for tag, tag_posts in sorted(tag_map.items()):
        tag_slug = slugify(tag)
        write_file(
            output_dir / "tags" / tag_slug / "index.html",
            tag_template.render(tag=tag, posts=tag_posts, title=f"Tag: {tag}"),
        )

    write_file(output_dir / "feed.xml", generate_rss(pub_posts, site_url=site_url))

    copy_static(static_dir, output_dir)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a static site from Markdown content + Jinja2 templates."
    )
    parser.add_argument("--content", default="content", help="Path to content directory")
    parser.add_argument("--templates", default="templates", help="Path to templates directory")
    parser.add_argument("--output", default="output", help="Path to output directory")
    parser.add_argument(
        "--static",
        default=None,
        help="Path to static assets directory (default: sibling 'static/' of --content)",
    )
    parser.add_argument(
        "--site-url",
        default="https://example.com",
        help="Base site URL, used to build absolute links in the RSS feed",
    )
    args = parser.parse_args(argv)

    try:
        build_site(
            content_dir=Path(args.content),
            templates_dir=Path(args.templates),
            output_dir=Path(args.output),
            static_dir=Path(args.static) if args.static else None,
            site_url=args.site_url,
        )
    except FrontMatterError as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    print(f"Site built successfully -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
