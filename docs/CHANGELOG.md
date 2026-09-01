# Changelog

## Unreleased

- Analyze source language files, manifests, and `SKILL.md` instead of wiki, docs, and HTML dumps.
- Fingerprint binaries by path and size (`sha256-path-content-v2`).
- Add in-process `codemap_tool.py build` and share `render_html()` for maps that are not written under `docs/codemap/`.
- Extracted `maintain-code-map` into a standalone Agent Skills package.
- Required `--root` for drive-wide inventory instead of a machine-local default.
- Wrote JSON, Markdown, HTML, and lock from one validated staging set.
- Added sample infrastructure maps, a `docs/` GitHub Pages landing, and gallery screenshots.

