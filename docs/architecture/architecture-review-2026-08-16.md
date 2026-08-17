# Architecture review — 2026-08-16

Language: Spanish
Mode: Execution
Scope: public `maintain-code-map` skill; any Git repository

## Goal

One process builds the map. HTML render is a function. Tests use temporary Git repos. No machine-local paths.

## Shipped

- ARC-02: `render_html(data, repo_name)` in `codemap_common.py`. CLI `render` still writes under `docs/codemap/` in the target repo. Example gallery calls the function.
- ARC-01: `codemap_tool.py build` runs generate → markdown → HTML → lock → validate → publish in one process. Drive maps call that instead of six Python processes.
- ARC-03: tests cover docs-vs-code edges, `package.json`, `SKILL.md`, binary fingerprints, `build`, and `render_html` outside `docs/codemap/`.

## Verification

- `python scripts/validate_package.py`
- `python -B -m unittest SKILLS/maintain-code-map/scripts/test_codemap_tool.py` — 10 tests, OK
- SKILL.md frontmatter parses: `name`, `description`, `license`

## Residual

- Individual CLI commands remain for debugging.
- Target-repo artifacts stay at `docs/codemap/` (product contract, not a host path).
- Existing locks with `sha256-path-content-v1` need a rebuild.
