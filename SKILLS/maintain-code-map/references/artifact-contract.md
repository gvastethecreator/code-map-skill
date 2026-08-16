# Artifact Contract

One model, four files. Generate JSON and lock from one analysis. Derive Markdown and HTML from the JSON.

Versioned under `docs/codemap/`:

- `codemap.json` — source of truth
- `codemap.md` — agent-read view
- `codemap.html` — human diagram
- `codemap.lock` — freshness

Publish all four together. Do not leave HTML out.

## `codemap.json`

```json
{
  "generated_at": "2026-08-04T15:04:05Z",
  "generated_from_commit": "<full commit>",
  "scope": ["src", "tests"],
  "nodes": [],
  "edges": [],
  "flows": []
}
```

Use repository-relative POSIX paths. Sort nodes and edges by stable IDs. Keep flow steps in runtime order. At most 20 nodes. At most 5 flows. Flows may be empty.

### Nodes

Required fields: `id`, `path`, `type`, `boundary`, `entrypoints`, `tests`, `callers`, `callees`, `evidence`.

Optional fields: `role`, `constraints`. Omit them when they add no information.

```json
{
  "id": "api",
  "path": "src/api",
  "type": "interface",
  "boundary": "Application",
  "entrypoints": ["src/api/server.ts:createServer"],
  "tests": ["tests/api/server.test.ts"],
  "callers": [{"id": "cli", "type": "calls"}],
  "callees": [{"id": "orders", "type": "calls"}],
  "evidence": {
    "status": "verified",
    "locations": [
      {"path": "src/api/server.ts", "symbol": "createServer"}
    ]
  }
}
```

Node types: `module`, `service`, `database`, `queue`, `interface`, `external`.

`callers` and `callees` are derived from edges. Sort by `id`, then `type`. A node must have verified evidence. Every test path must exist. At most 3 evidence locations.

### Edges

```json
{
  "from": "api",
  "to": "orders",
  "type": "calls",
  "evidence": {
    "status": "verified",
    "locations": [
      {"path": "src/api/routes/orders.ts", "symbol": "createOrder"}
    ]
  }
}
```

Edge types: `imports`, `calls`, `reads`, `writes`, `publishes`, `subscribes`.

Unknown edges use `{"status": "unknown", "locations": []}`. Do not attach invented paths or symbols. At most 3 evidence locations.

### Flows

Optional. Zero to five. Each flow has a trigger, ordered node IDs, and an outcome. Consecutive steps need a directed edge. Do not invent synthetic "tracked reference" paths.

## `codemap.md`

Generate with `codemap_tool.py markdown`. Always derive it from JSON. Keep it under about 200 lines.

Required sections:

- header: repo name, `generated_at`, short commit, scope, counts
- `## Modules` — one bullet per node: id, path, type, boundary, callers, callees, tests, entry
- `## Edges` — `from -> to · type`
- `## Unknown` — unknown edges, or `- none`
- `## Flows` — real flows, or `- none`

No evidence blobs. No generic role text.

The Markdown must name the same node ids, edge pairs, and flow steps as the JSON.

## `codemap.html`

Generate with `codemap_tool.py render` from the same JSON. Publish it with json, md, and lock. Do not hand-edit it.

The renderer embeds the JSON payload in the template. The template is self-contained: no network, package, font, image, script, or stylesheet request. System fonts only.

The rendered diagram must satisfy [diagram-grammar.md](diagram-grammar.md). Interaction stays live: selection, callers, callees, search, and flows when present.

Validate HTML with the other artifacts. `--html` is a leftover flag; HTML is required.

## `codemap.lock`

Generate with `codemap_tool.py lock`.

```json
{
  "source_commit": "<full commit of the analyzed tree>",
  "working_tree_dirty": true,
  "generated_at": "2026-08-04T15:04:05Z",
  "scanned_scope": ["src", "tests"],
  "excluded_directories": ["docs/codemap", "node_modules"],
  "fingerprint_algorithm": "sha256-path-content-v1",
  "modules": [
    {
      "id": "src/api",
      "path": "src/api",
      "file_count": 4,
      "fingerprint": "<sha256>"
    }
  ]
}
```

`source_commit` is `HEAD` at generation time. After the map is committed, it remains an ancestor of the new `HEAD`.

The fingerprint hashes sorted tracked paths and their current bytes. Missing tracked files use a deterministic marker.

`working_tree_dirty` ignores every path under `docs/codemap/`.

JSON `generated_from_commit` must equal lock `source_commit`.

## Validation

Default (`json` + `md` + `lock`):

- `codemap.json` parses.
- Node count is 1 to 20.
- Flow count is 0 to 5.
- Every node path and test path exists.
- Every verified evidence path exists, contains its literal symbol, and has at most 3 locations.
- Every edge endpoint and flow step references a node.
- Every consecutive flow step has a directed edge.
- Edge types use the allowed vocabulary.
- Unproved edges use `status: unknown` with no locations.
- `callers` and `callees` match the edges.
- Markdown names the same node ids, edge pairs, and flow steps.
- Lock `source_commit` is a 40-character commit that exists and is `HEAD` or an ancestor of `HEAD`.
- JSON `generated_from_commit` equals lock `source_commit`.
- Lock dirty state, scope, exclusions, and fingerprints match generation. Dirty state ignores `docs/codemap/`.

HTML:

- The HTML embeds the same nodes, edges, and flows as the JSON.
- The file stays self-contained.

Then open the HTML in a browser. Static validation does not prove interaction.

`verify_codemap_browser.cjs` is a smoke check: nodes visible, click selects, no non-`file:` request, no page errors. It uses Chrome DevTools Protocol through a native browser pipe. It must not require Playwright.

## Final Report

1. Files created or modified.
2. Stale modules before regeneration.
3. Remaining unknown relationships.
4. Static validation, and browser smoke only if HTML was rendered.
5. The complete tracked and untracked artifact diff.

Compare staged replacements with `git diff --no-index`. Use `NUL` on Windows or `/dev/null` on POSIX when a current artifact does not exist.

After publication, use `git diff -- docs/codemap` for tracked artifacts.
