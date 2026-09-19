---
title: Writing in Markdown
date: 2026-02-10
tags: [python, tutorial]
layout: post.html
---

Content files are plain Markdown, so all the usual formatting works:

- **Bold** and *italic* text
- [Links](https://www.markdownguide.org/)
- Inline `code` and fenced code blocks:

```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

- Tables (via the `tables` extension):

| Feature      | Supported |
| ------------ | --------- |
| Front matter | Yes       |
| Tags         | Yes       |
| RSS          | Yes       |

No build step touches the source files themselves — everything is
regenerated fresh into `output/` each time.
