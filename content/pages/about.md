---
title: About
layout: page.html
---

## About This Site

This site is built with **Static Site Generator**, a small Python
build-time tool that turns Markdown files with YAML front matter into
static HTML, using Jinja2 templates for layout.

There is no server and no database involved in serving this site —
every page here was generated once, ahead of time, by running:

```
python build.py --content content/ --templates templates/ --output output/
```

The result in `output/` is plain HTML/CSS that can be hosted anywhere
static files are served (GitHub Pages, S3, nginx, etc.).
