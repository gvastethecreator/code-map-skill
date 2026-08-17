# Performance review — 2026-08-16

Language: Spanish
Mode: Execution
Quality: keep evidence-backed maps; do not invent edges from wiki text

## Baseline (synthetic, any repo)

The generator used to read every tracked Markdown, HTML, JSON, and YAML file and scan them for links. Fingerprints hashed every scoped file, including binaries. Drive publication spawned six Python processes per repository.

## Shipped

- PERF-01: analyze language files, manifests (`package.json`, `go.mod`, `Cargo.toml`, `pyproject.toml`, `requirements.txt`), and `SKILL.md`. Docs-only repositories fall back to text files.
- PERF-02: `sha256-path-content-v2` — binaries contribute path + size. Unknown extensions with a NUL in the first 8 KiB are treated as binary. Text still hashes bytes.

## Quality checks

- Markdown with a link to source does not create a `reads` edge.
- `package.json` `main` still creates a `reads` edge.
- Named skills in `SKILL.md` still create `reads` edges.
- Same-size binary rewrite does not change the fingerprint; a size change does.
- Source file byte changes still change the fingerprint.

## Residual

- A binary that changes bytes but not size will not mark that module stale.
- HTML/CSS-only apps with no language files use the docs fallback.
