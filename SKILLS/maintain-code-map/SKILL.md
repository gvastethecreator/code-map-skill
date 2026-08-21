---
name: maintain-code-map
description: "Code map: generate or refresh evidence-backed repository map and lock; preflight, stale checks, architecture."
license: MIT
---

# Maintain Code Map

Create one evidence-backed model of the current repository. Agents read Markdown first; humans open the published HTML.

Read [references/artifact-contract.md](references/artifact-contract.md) before creating or validating artifacts. HTML template changes: also [references/diagram-grammar.md](references/diagram-grammar.md).

## Process

1. Set the boundary.
   - Resolve the repository root and read its instructions and architecture documents.
   - Inspect `git status -sb`, the current commit, tracked files, build manifests, entrypoints, and tests.
   - Exclude vendor, dependency, build, distribution, cache, generated, coverage, `.obsidian`, `.vscode`, and `.agents` directories.
   - Do not modify product code. Write only under `docs/codemap/` in the target repository.
   - Done: scan scope and exclusions are explicit.

2. Measure freshness before analysis.
   - If `docs/codemap/codemap.lock` exists, run `codemap_tool.py status` before regeneration.
   - Record each changed, new, and removed module from the status output. No lock: treat all scanned modules as new.
   - Treat fingerprint or module drift as stale.
   - Report `source_commit` drift separately. A later commit that only adds the map is expected and does not make fingerprints stale.
   - Report dirty-state drift. If module fingerprints still match, do not call the map stale.
   - Done: stale-module list is saved for the final report.

3. Build the evidence model.
   - Evidence: tracked source language files, package manifests, and `SKILL.md`. Do not parse wiki, docs, HTML dumps, or binary assets as the import graph.
   - No source language files: fall back to tracked text files so the generator still runs.
   - Select no more than 20 primary nodes. Group low-level files under the module that owns them. Include major modules, services, databases, queues, interfaces, and external dependencies.
   - Trace calls and data movement through imports, calls, reads, writes, publishes, and subscriptions.
   - Add up to five real end-to-end flows. If the generator cannot name a real trigger, omit flows.
   - Use exact source paths and literal symbols for verified evidence. Cap evidence locations at 3. If source evidence does not prove the relationship, mark the edge `unknown`.
   - Do not infer a relationship from names, folder proximity, or architecture prose alone.
   - Large batches: `codemap_tool.py build` for a conservative baseline in one process. Keep individual generate, markdown, render, lock, validate, and publish commands for debugging.
   - Drive-wide first pass: `generate_drive_maps.py` without `--write` to inspect scope, then repeat with `--write`. The drive command publishes JSON, Markdown, HTML, and lock.
   - Review automatic roles and flows against the repository entrypoints before publication.
   - If the analyzer reports insufficient evidence, leave the repo unchanged and report it as blocked.
   - Done: every node and verified edge has source evidence.

4. Generate the versioned artifacts together.
   - Prefer `codemap_tool.py build`. It writes, validates, and publishes JSON, Markdown, HTML, and lock in one process.
   - Debug individual commands: create `docs/codemap/.staging/`. Write `codemap.json` first per the artifact contract. One UTC generation time and current `HEAD` as `source_commit` / `generated_from_commit`. Then `markdown`, `render`, `lock` (same scan scope, exclusions, generation time), `validate` against staging.
   - Capture a no-index diff from each current artifact to its staged replacement before publishing. Missing current artifact: platform null device as the old file.
   - `publish` only after staging validation passes. Do not hand-edit one published artifact without regenerating json, md, html, and lock.
   - Done: published Markdown, JSON, HTML, and lock describe the same repository state.

5. Smoke-check the HTML after render.
   - Open the file directly, without a server.
   - Run `verify_codemap_browser.cjs` as a smoke check: nodes visible, click selects, no network.
   - If browser detection fails, pass `--browser <executable>` or set `CODEMAP_BROWSER_EXECUTABLE`.
   - Done: static file works with network access disabled.

6. Report the result.
   - List created or modified files; stale modules from the pre-generation status; remaining `unknown` edges; each validation result and any check that did not run.
   - Show the tracked Git diff and the captured no-index diff for untracked artifacts.
   - Do not stage artifacts only to make Git show a diff.
   - Do not claim runtime or browser proof from static validation.
   - Done: report separates verified facts, unknowns, and unrun checks.

Agent read order: `status` against the lock, then `codemap.md`, then `codemap_tool.py impact --module <id-or-path>` or the JSON for `path:symbol` evidence.

## Commands

Set `<skill-root>` to this skill directory.

```powershell
python <skill-root>/scripts/codemap_tool.py status --repo . --lock docs/codemap/codemap.lock

python <skill-root>/scripts/codemap_tool.py build --repo . --generated-at <utc-time>

python <skill-root>/scripts/generate_repository_map.py --repo . --output docs/codemap/.staging/codemap.json --generated-at <utc-time>

python <skill-root>/scripts/codemap_tool.py markdown --repo . --json docs/codemap/.staging/codemap.json --output docs/codemap/.staging/codemap.md

python <skill-root>/scripts/codemap_tool.py render --repo . --json docs/codemap/.staging/codemap.json --output docs/codemap/.staging/codemap.html

python <skill-root>/scripts/generate_drive_maps.py --root <workspace-parent>

python <skill-root>/scripts/generate_drive_maps.py --root <workspace-parent> --write

python <skill-root>/scripts/generate_drive_maps.py --root <workspace-parent> --write --refresh-stale

python <skill-root>/scripts/generate_drive_maps.py --root <workspace-parent> --write --refresh-stale --rerender-existing

python <skill-root>/scripts/codemap_tool.py lock --repo . --scope src --scope tests --exclude docs/codemap --generated-at <utc-time> --output docs/codemap/.staging/codemap.lock

python <skill-root>/scripts/codemap_tool.py validate --repo . --dir docs/codemap/.staging

python <skill-root>/scripts/codemap_tool.py publish --repo . --staging docs/codemap/.staging --target docs/codemap

python <skill-root>/scripts/codemap_tool.py impact --repo . --json docs/codemap/codemap.json --module src/api

python <skill-root>/scripts/codemap_tool.py render --repo . --json docs/codemap/codemap.json --output docs/codemap/codemap.html

python <skill-root>/scripts/codemap_tool.py validate --repo . --dir docs/codemap --html

node <skill-root>/scripts/verify_codemap_browser.cjs docs/codemap/codemap.html

node <skill-root>/scripts/verify_codemap_browser.cjs --browser <chrome-or-chromium> docs/codemap/codemap.html
```

Repeat `--scope` and `--exclude` for the repository. Use `--scope .` only if root-level grouping gives useful module fingerprints.

## Resources

- `references/artifact-contract.md`: JSON, Markdown, HTML, lock, and report contracts.
- `references/diagram-grammar.md`: visual rules for the published HTML view.
- `scripts/codemap_tool.py`: freshness, Markdown, impact, HTML render, in-process build, publishing, and validation.
- `scripts/generate_repository_map.py`: conservative source-file baseline. Prefer `codemap_tool.py build`.
- `scripts/generate_drive_maps.py`: Git-root inventory. Publishes validated json, md, html, and lock across the drive.
- `scripts/verify_codemap_browser.cjs`: optional Chrome DevTools Protocol smoke check after HTML render.
- `assets/codemap-template.html`: self-contained interactive browser template.
