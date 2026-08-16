---
name: maintain-code-map
description: "Generate or refresh an evidence-backed repository code map and lock. Use for codemap creation, code-change preflight, stale-map checks, or architecture and data-flow changes."
license: MIT
---

# Maintain Code Map

Create one evidence-backed model of the current repository. Agents read Markdown first. Humans open the published HTML.

Read [references/artifact-contract.md](references/artifact-contract.md) before you create or validate artifacts. Read [references/diagram-grammar.md](references/diagram-grammar.md) only when changing the HTML template.

## Process

1. Set the boundary.
   - Resolve the repository root and read its instructions and architecture documents.
   - Inspect `git status -sb`, the current commit, tracked files, build manifests, entrypoints, and tests.
   - Exclude vendor, dependency, build, distribution, cache, generated, coverage, `.obsidian`, `.vscode`, and `.agents` directories.
   - Do not modify product code during this process.
   - Write only under `docs/codemap/` in the target repository.
   - Done when the scan scope and exclusions are explicit.

2. Measure freshness before analysis.
   - If `docs/codemap/codemap.lock` exists, run `codemap_tool.py status` before regeneration.
   - Record each changed, new, and removed module from the status output.
   - Treat all scanned modules as new when the lock does not exist.
   - Treat fingerprint or module drift as stale.
   - Report `source_commit` drift separately. A later commit that only adds the map is expected and does not make fingerprints stale.
   - Report dirty-state drift, but do not call the map stale when module fingerprints still match.
   - Done when the stale-module list is saved for the final report.

3. Build the evidence model.
   - Use tracked source, configuration, migrations, schemas, and tests as evidence.
   - Select no more than 20 primary nodes.
   - Group low-level files under the module that owns them.
   - Include major modules, services, databases, queues, interfaces, and external dependencies.
   - Trace calls and data movement through imports, calls, reads, writes, publishes, and subscriptions.
   - Add up to five real end-to-end flows. Omit flows when the generator cannot name a real trigger.
   - Use exact source paths and literal symbols for verified evidence. Cap evidence locations at 3.
   - Mark an edge `unknown` when source evidence does not prove the relationship.
   - Do not infer a relationship from names, folder proximity, or architecture prose alone.
   - For large batches, run `generate_repository_map.py` to create a conservative baseline.
   - For a drive-wide first pass, run `generate_drive_maps.py` without `--write` to inspect scope, then repeat with `--write`.
   - The drive command publishes JSON, Markdown, HTML, and lock.
   - Review automatic roles and flows against the repository entrypoints before publication.
   - If the analyzer reports insufficient evidence, leave the repo unchanged and report it as blocked.
   - Done when every node and verified edge has source evidence.

4. Generate the versioned artifacts together.
   - Create `docs/codemap/.staging/` for the new artifact set.
   - Write `codemap.json` first, according to the artifact contract.
   - Use one UTC generation time and the current `HEAD` as `source_commit` / `generated_from_commit`.
   - Run `codemap_tool.py markdown` to derive `codemap.md` from the JSON.
   - Run `codemap_tool.py render` to derive `codemap.html` from the JSON.
   - Run `codemap_tool.py lock` with the same scan scope, exclusions, and generation time.
   - Run `codemap_tool.py validate` against the staging directory.
   - Capture a no-index diff from each current artifact to its staged replacement before publishing.
   - Use the platform null device as the old file when a current artifact does not exist.
   - Run `codemap_tool.py publish` only after staging validation passes.
   - Do not hand-edit one published artifact without regenerating json, md, html, and lock.
   - Done when the published Markdown, JSON, HTML, and lock describe the same repository state.

5. Smoke-check the HTML after render.
   - Open the file directly, without a server.
   - Run `verify_codemap_browser.cjs` as a smoke check: nodes visible, click selects, no network.
   - If browser detection fails, pass `--browser <executable>` or set `CODEMAP_BROWSER_EXECUTABLE`.
   - Done when the static file works with network access disabled.

6. Report the result.
   - List created or modified files.
   - List stale modules from the pre-generation status.
   - List all remaining `unknown` edges.
   - List each validation result and any check that did not run.
   - Show the tracked Git diff and the captured no-index diff for untracked artifacts.
   - Do not stage artifacts only to make Git show a diff.
   - Do not claim runtime or browser proof from static validation.
   - Done when the report separates verified facts, unknowns, and unrun checks.

Agent read order: `status` against the lock, then `codemap.md`, then `codemap_tool.py impact --module <id-or-path>` or the JSON for `path:symbol` evidence.

## Commands

Set `<skill-root>` to this skill directory.

```powershell
python <skill-root>/scripts/codemap_tool.py status --repo . --lock docs/codemap/codemap.lock

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

Repeat `--scope` and `--exclude` for the repository. Use `--scope .` only when root-level grouping gives useful module fingerprints.

## Resources

- `references/artifact-contract.md`: JSON, Markdown, HTML, lock, and report contracts.
- `references/diagram-grammar.md`: visual rules for the published HTML view.
- `scripts/codemap_tool.py`: freshness, Markdown, impact, HTML render, publishing, and validation.
- `scripts/generate_repository_map.py`: conservative tracked-source baseline for large repository batches.
- `scripts/generate_drive_maps.py`: Git-root inventory and validated drive-wide publication of json, md, html, and lock.
- `scripts/verify_codemap_browser.cjs`: optional Chrome DevTools Protocol smoke check after HTML render.
- `assets/codemap-template.html`: self-contained interactive browser template.
