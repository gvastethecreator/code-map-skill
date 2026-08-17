#!/usr/bin/env python3
"""Create fingerprints, render views, and validate synchronized code-map artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from codemap_common import (
    ALGORITHM,
    CodemapError,
    load_sibling,
    module_fingerprint,
    render_html,
)

ARTIFACTS = ("codemap.json", "codemap.md", "codemap.html", "codemap.lock")
EDGE_TYPES = {"imports", "calls", "reads", "writes", "publishes", "subscribes"}
NODE_TYPES = {"module", "service", "database", "queue", "interface", "external"}
MAX_EVIDENCE_LOCATIONS = 3
DEFAULT_EXCLUDES = (
    ".agents",
    ".cache",
    ".git",
    ".hg",
    ".next",
    ".nuxt",
    ".obsidian",
    ".svn",
    ".turbo",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "docs/codemap",
    "generated",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise CodemapError(message or f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", errors="surrogateescape")


def repo_root(value: str) -> Path:
    candidate = Path(value).resolve()
    root = run_git(candidate, "rev-parse", "--show-toplevel").strip()
    return Path(root).resolve()


def normalize_rel(repo: Path, value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    if not cleaned or cleaned == ".":
        return "."
    pure = PurePosixPath(cleaned)
    if pure.is_absolute() or ".." in pure.parts:
        raise CodemapError(f"path must be repository-relative: {value}")
    resolved = (repo / Path(*pure.parts)).resolve()
    try:
        relative = resolved.relative_to(repo)
    except ValueError as error:
        raise CodemapError(f"path escapes repository: {value}") from error
    return relative.as_posix() or "."


def resolve_inside(repo: Path, value: str) -> Path:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
    try:
        resolved.relative_to(repo)
    except ValueError as error:
        raise CodemapError(f"path escapes repository: {value}") from error
    return resolved


def resolve_output(repo: Path, value: str) -> Path:
    resolved = resolve_inside(repo, value)
    codemap_root = (repo / "docs" / "codemap").resolve()
    try:
        resolved.relative_to(codemap_root)
    except ValueError as error:
        raise CodemapError(f"output must stay under docs/codemap: {value}") from error
    return resolved


def unique_paths(repo: Path, values: list[str] | None, defaults: tuple[str, ...] = ()) -> list[str]:
    normalized = {normalize_rel(repo, value) for value in (*defaults, *(values or []))}
    return sorted(normalized)


def tracked_files(repo: Path) -> list[str]:
    output = run_git(repo, "-c", "core.quotepath=false", "ls-files", "-z")
    return sorted(path for path in output.split("\0") if path)


def is_under(path: str, root: str) -> bool:
    return root == "." or path == root or path.startswith(f"{root}/")


def is_excluded(path: str, exclusions: list[str]) -> bool:
    parts = PurePosixPath(path).parts
    for exclusion in exclusions:
        exclusion_parts = PurePosixPath(exclusion).parts
        if len(exclusion_parts) == 1 and exclusion in parts:
            return True
        if is_under(path, exclusion):
            return True
    return False


def selected_scope(path: str, scopes: list[str]) -> str | None:
    matches = [scope for scope in scopes if is_under(path, scope)]
    if not matches:
        return None
    return max(matches, key=lambda item: len(PurePosixPath(item).parts))


def module_id(path: str, scope: str) -> str:
    path_parts = PurePosixPath(path).parts
    scope_parts = () if scope == "." else PurePosixPath(scope).parts
    remainder = path_parts[len(scope_parts) :]
    if len(remainder) <= 1:
        return scope
    first_child = remainder[0]
    return first_child if scope == "." else f"{scope}/{first_child}"


def snapshot_modules(repo: Path, scopes: list[str], exclusions: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for relative in tracked_files(repo):
        if is_excluded(relative, exclusions):
            continue
        scope = selected_scope(relative, scopes)
        if scope is None:
            continue
        grouped.setdefault(module_id(relative, scope), []).append(relative)
    return [
        {
            "id": identifier,
            "path": identifier,
            "file_count": len(files),
            "fingerprint": module_fingerprint(repo, files),
        }
        for identifier, files in sorted(grouped.items())
    ]


CODEMAP_ARTIFACTS = {
    "docs/codemap/codemap.html",
    "docs/codemap/codemap.json",
    "docs/codemap/codemap.md",
    "docs/codemap/codemap.lock",
}


def current_commit(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD").strip()


def porcelain_path(line: str) -> str:
    payload = line[3:] if len(line) > 3 else ""
    if " -> " in payload:
        payload = payload.split(" -> ", 1)[1]
    return payload.strip().strip('"').replace("\\", "/")


def is_codemap_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized in CODEMAP_ARTIFACTS or normalized.startswith("docs/codemap/")


def working_tree_dirty(repo: Path) -> bool:
    status = run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    for line in status.splitlines():
        path = porcelain_path(line)
        if not path or is_codemap_artifact(path):
            continue
        return True
    return False


def is_full_commit(repo: Path, value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        return False
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-t", value],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0 and result.stdout.decode("utf-8", errors="replace").strip() == "commit"


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CodemapError(f"cannot parse {path}: {error}") from error


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(path, f"{json.dumps(value, indent=2, ensure_ascii=False)}\n")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def verify_source_path(repo: Path, value: Any, label: str, errors: list[str], *, file_only: bool = False) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty path")
        return None
    try:
        resolved = resolve_inside(repo, value)
    except CodemapError as error:
        errors.append(f"{label}: {error}")
        return None
    exists = resolved.is_file() if file_only else resolved.exists()
    require(exists, f"{label} does not exist: {value}", errors)
    return resolved if exists else None


def verify_evidence(
    repo: Path,
    evidence: Any,
    label: str,
    errors: list[str],
    *,
    allow_unknown: bool,
) -> str | None:
    if not isinstance(evidence, dict):
        errors.append(f"{label} evidence must be an object")
        return None
    status = evidence.get("status")
    locations = evidence.get("locations")
    require(status in {"verified", "unknown"}, f"{label} has invalid evidence status", errors)
    require(isinstance(locations, list), f"{label} evidence locations must be a list", errors)
    if not isinstance(locations, list):
        return status if isinstance(status, str) else None
    if status == "unknown":
        require(allow_unknown, f"{label} cannot use unknown evidence", errors)
        require(not locations, f"{label} unknown evidence must not contain locations", errors)
        return status
    require(bool(locations), f"{label} verified evidence needs a location", errors)
    require(len(locations) <= MAX_EVIDENCE_LOCATIONS, f"{label} has more than {MAX_EVIDENCE_LOCATIONS} evidence locations", errors)
    for index, location in enumerate(locations):
        location_label = f"{label} evidence[{index}]"
        if not isinstance(location, dict):
            errors.append(f"{location_label} must be an object")
            continue
        source = verify_source_path(repo, location.get("path"), location_label, errors, file_only=True)
        symbol = location.get("symbol")
        require(isinstance(symbol, str) and bool(symbol), f"{location_label} needs a literal symbol", errors)
        if source and isinstance(symbol, str) and symbol:
            content = source.read_text(encoding="utf-8", errors="replace")
            require(symbol in content, f"{location_label} symbol not found: {symbol}", errors)
    return status if isinstance(status, str) else None


def relation_items(value: Any, label: str, errors: list[str]) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    items: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be an object")
            continue
        identifier = item.get("id")
        edge_type = item.get("type")
        require(isinstance(identifier, str) and bool(identifier), f"{item_label} id must be a non-empty string", errors)
        require(edge_type in EDGE_TYPES, f"{item_label} has invalid type: {edge_type}", errors)
        if isinstance(identifier, str) and edge_type in EDGE_TYPES:
            key = (identifier, edge_type)
            require(key not in seen, f"{item_label} duplicates {identifier} {edge_type}", errors)
            seen.add(key)
            items.append(key)
    return items


def expected_relations(edges: list[dict[str, Any]]) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[tuple[str, str]]]]:
    callers: dict[str, list[tuple[str, str]]] = {}
    callees: dict[str, list[tuple[str, str]]] = {}
    for edge in edges:
        source = edge.get("from")
        target = edge.get("to")
        edge_type = edge.get("type")
        if not isinstance(source, str) or not isinstance(target, str) or edge_type not in EDGE_TYPES:
            continue
        callees.setdefault(source, []).append((target, edge_type))
        callers.setdefault(target, []).append((source, edge_type))
    for mapping in (callers, callees):
        for identifier, items in mapping.items():
            mapping[identifier] = sorted(set(items))
    return callers, callees


def relation_text(items: list[dict[str, Any]] | list[tuple[str, str]]) -> str:
    if not items:
        return "(none)"
    parts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            parts.append(f"{item['id']} ({item['type']})")
        else:
            parts.append(f"{item[0]} ({item[1]})")
    return ", ".join(parts)


def render_markdown(data: dict[str, Any], repo_name: str) -> str:
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    flows = data.get("flows") or []
    unknown = [
        edge
        for edge in edges
        if isinstance(edge, dict) and isinstance(edge.get("evidence"), dict) and edge["evidence"].get("status") == "unknown"
    ]
    commit = str(data.get("generated_from_commit") or "")
    lines = [
        f"# Code map · {repo_name}",
        "",
        f"generated: {data.get('generated_at') or ''}",
        f"commit: {commit[:12]}",
        f"scope: {', '.join(data.get('scope') or [])}",
        "",
        f"counts: {len(nodes)} nodes · {len(edges)} edges · {len(flows)} flows · {len(unknown)} unknown",
        "",
        "## Modules",
        "",
    ]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        tests = ", ".join(node.get("tests") or []) or "(none)"
        entry = ", ".join(node.get("entrypoints") or []) or "(none)"
        lines.append(f"- `{node.get('id')}` · `{node.get('path')}` · {node.get('type')} · {node.get('boundary')}")
        lines.append(f"  callers: {relation_text(node.get('callers') or [])}")
        lines.append(f"  callees: {relation_text(node.get('callees') or [])}")
        lines.append(f"  tests: {tests}")
        lines.append(f"  entry: {entry}")
        lines.append("")
    lines.extend(["## Edges", ""])
    if edges:
        for edge in edges:
            if isinstance(edge, dict):
                lines.append(f"- {edge.get('from')} -> {edge.get('to')} · {edge.get('type')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Unknown", ""])
    if unknown:
        for edge in unknown:
            lines.append(f"- {edge.get('from')} -> {edge.get('to')} · {edge.get('type')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Flows", ""])
    if flows:
        for flow in flows:
            if not isinstance(flow, dict):
                continue
            steps = " -> ".join(flow.get("steps") or [])
            lines.append(f"- {flow.get('trigger')}")
            lines.append(f"  {steps}")
            lines.append(f"  {flow.get('outcome')}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def markdown_parity(content: str) -> dict[str, set[Any]]:
    node_ids = set(re.findall(r"^- `([^`]+)` ·", content, flags=re.M))
    edge_pairs = set()
    in_edges = False
    in_flows = False
    flow_steps: set[str] = set()
    for line in content.splitlines():
        if line.startswith("## "):
            in_edges = line == "## Edges"
            in_flows = line == "## Flows"
            continue
        if in_edges:
            match = re.match(r"^- (\S+) -> (\S+) · (\S+)$", line)
            if match and match.group(1) != "none":
                edge_pairs.add((match.group(1), match.group(2), match.group(3)))
        if in_flows and line.startswith("  ") and " -> " in line:
            flow_steps.update(part.strip() for part in line.strip().split(" -> ") if part.strip())
    return {"nodes": node_ids, "edges": edge_pairs, "flow_steps": flow_steps}


def embedded_html_data(content: str) -> Any:
    match = re.search(
        r'<script\s+id=["\']codemap-data["\']\s+type=["\']application/json["\']>(.*?)</script>',
        content,
        flags=re.DOTALL,
    )
    if not match:
        raise CodemapError("codemap.html does not contain the codemap-data payload")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise CodemapError(f"codemap.html contains invalid embedded JSON: {error}") from error


def find_node(nodes: list[dict[str, Any]], module: str) -> dict[str, Any]:
    exact_id = [node for node in nodes if node.get("id") == module]
    if len(exact_id) == 1:
        return exact_id[0]
    exact_path = [node for node in nodes if node.get("path") == module]
    if len(exact_path) == 1:
        return exact_path[0]
    prefixed = [node for node in nodes if isinstance(node.get("path"), str) and (node["path"].startswith(f"{module}/") or module.startswith(f"{node['path']}/"))]
    if len(prefixed) == 1:
        return prefixed[0]
    contains = [node for node in nodes if module in str(node.get("id", "")) or module in str(node.get("path", ""))]
    if len(contains) == 1:
        return contains[0]
    raise CodemapError(f"module not found: {module}")


def validate_html(paths: dict[str, Path], data: dict[str, Any], errors: list[str], *, required: bool) -> None:
    html_path = paths["codemap.html"]
    if not html_path.is_file():
        if required:
            errors.append("missing codemap.html")
        return
    html_content = html_path.read_text(encoding="utf-8")
    try:
        html_data = embedded_html_data(html_content)
    except CodemapError as error:
        errors.append(str(error))
        return
    for key in ("nodes", "edges", "flows"):
        require(html_data.get(key) == data.get(key), f"HTML and JSON differ for {key}", errors)
    for marker in ("repo-name", "generated-at", "generated-commit", "search", "map-svg", "details"):
        require(f'id="{marker}"' in html_content, f"HTML is missing #{marker}", errors)


def validate_directory(repo: Path, directory: Path, *, require_html: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    paths = {name: directory / name for name in ARTIFACTS}
    for name in ARTIFACTS:
        require(paths[name].is_file(), f"missing {name}", errors)
    if errors:
        raise CodemapError("; ".join(errors))

    data = load_json(paths["codemap.json"])
    lock = load_json(paths["codemap.lock"])
    markdown = paths["codemap.md"].read_text(encoding="utf-8")
    require(isinstance(data, dict), "codemap.json must contain an object", errors)
    require(isinstance(lock, dict), "codemap.lock must contain an object", errors)
    if not isinstance(data, dict) or not isinstance(lock, dict):
        raise CodemapError("; ".join(errors))

    required_top = {"generated_at", "generated_from_commit", "scope", "nodes", "edges", "flows"}
    require(required_top.issubset(data), "codemap.json is missing required top-level fields", errors)
    nodes = data.get("nodes")
    edges = data.get("edges")
    flows = data.get("flows")
    require(isinstance(nodes, list), "nodes must be a list", errors)
    require(isinstance(edges, list), "edges must be a list", errors)
    require(isinstance(flows, list), "flows must be a list", errors)
    if not all(isinstance(value, list) for value in (nodes, edges, flows)):
        raise CodemapError("; ".join(errors))

    require(0 < len(nodes) <= 20, "node count must be between 1 and 20", errors)
    require(0 <= len(flows) <= 5, "flow count must be between 0 and 5", errors)
    node_ids: set[str] = set()
    typed_nodes: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        label = f"node[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{label} must be an object")
            continue
        typed_nodes.append(node)
        required = {"id", "path", "type", "boundary", "entrypoints", "tests", "callers", "callees", "evidence"}
        require(required.issubset(node), f"{label} is missing required fields", errors)
        identifier = node.get("id")
        require(isinstance(identifier, str) and bool(identifier), f"{label} id must be a non-empty string", errors)
        if isinstance(identifier, str):
            require(identifier not in node_ids, f"duplicate node id: {identifier}", errors)
            node_ids.add(identifier)
        verify_source_path(repo, node.get("path"), f"{label} path", errors)
        require(node.get("type") in NODE_TYPES, f"{label} has invalid type", errors)
        require(isinstance(node.get("boundary"), str) and bool(node.get("boundary")), f"{label} boundary is required", errors)
        if "role" in node:
            require(isinstance(node.get("role"), str) and bool(node.get("role")), f"{label} role must be a non-empty string when present", errors)
        if "constraints" in node:
            require(isinstance(node.get("constraints"), list), f"{label} constraints must be a list when present", errors)
        for field in ("entrypoints", "tests"):
            require(isinstance(node.get(field), list), f"{label} {field} must be a list", errors)
        if isinstance(node.get("tests"), list):
            for test_index, test_path in enumerate(node["tests"]):
                verify_source_path(repo, test_path, f"{label} tests[{test_index}]", errors, file_only=True)
        verify_evidence(repo, node.get("evidence"), label, errors, allow_unknown=False)

    directed_edges: set[tuple[str, str]] = set()
    typed_edges: list[tuple[str, str, str]] = []
    unknown_edges: list[str] = []
    for index, edge in enumerate(edges):
        label = f"edge[{index}]"
        if not isinstance(edge, dict):
            errors.append(f"{label} must be an object")
            continue
        required = {"from", "to", "type", "evidence"}
        require(required.issubset(edge), f"{label} is missing required fields", errors)
        source_id = edge.get("from")
        target_id = edge.get("to")
        require(source_id in node_ids, f"{label} references missing source node: {source_id}", errors)
        require(target_id in node_ids, f"{label} references missing target node: {target_id}", errors)
        require(edge.get("type") in EDGE_TYPES, f"{label} has invalid type: {edge.get('type')}", errors)
        if isinstance(source_id, str) and isinstance(target_id, str):
            directed_edges.add((source_id, target_id))
            if edge.get("type") in EDGE_TYPES:
                typed_edges.append((source_id, target_id, edge["type"]))
        status = verify_evidence(repo, edge.get("evidence"), label, errors, allow_unknown=True)
        if status == "unknown":
            unknown_edges.append(f"{source_id} -> {target_id}")

    expected_callers, expected_callees = expected_relations([edge for edge in edges if isinstance(edge, dict)])
    for node in typed_nodes:
        identifier = node.get("id")
        if not isinstance(identifier, str):
            continue
        callers = relation_items(node.get("callers"), f"node {identifier} callers", errors)
        callees = relation_items(node.get("callees"), f"node {identifier} callees", errors)
        require(callers == expected_callers.get(identifier, []), f"node {identifier} callers do not match edges", errors)
        require(callees == expected_callees.get(identifier, []), f"node {identifier} callees do not match edges", errors)

    flow_steps: set[str] = set()
    for index, flow in enumerate(flows):
        label = f"flow[{index}]"
        if not isinstance(flow, dict):
            errors.append(f"{label} must be an object")
            continue
        require({"trigger", "steps", "outcome"}.issubset(flow), f"{label} is missing required fields", errors)
        require(isinstance(flow.get("trigger"), str) and bool(flow.get("trigger")), f"{label} trigger is required", errors)
        require(isinstance(flow.get("outcome"), str) and bool(flow.get("outcome")), f"{label} outcome is required", errors)
        steps = flow.get("steps")
        require(isinstance(steps, list) and len(steps) >= 2, f"{label} needs at least two steps", errors)
        if isinstance(steps, list):
            for step in steps:
                require(step in node_ids, f"{label} references missing node: {step}", errors)
                if isinstance(step, str):
                    flow_steps.add(step)
            for source_id, target_id in zip(steps, steps[1:]):
                require((source_id, target_id) in directed_edges, f"{label} has no edge for {source_id} -> {target_id}", errors)

    parsed = markdown_parity(markdown)
    require(parsed["nodes"] == node_ids, "Markdown node ids do not match JSON", errors)
    require(parsed["edges"] == set(typed_edges), "Markdown edges do not match JSON", errors)
    require(parsed["flow_steps"] == flow_steps, "Markdown flow steps do not match JSON", errors)

    validate_html(paths, data, errors, required=require_html)

    required_lock = {
        "source_commit",
        "working_tree_dirty",
        "generated_at",
        "scanned_scope",
        "excluded_directories",
        "fingerprint_algorithm",
        "modules",
    }
    require(required_lock.issubset(lock), "codemap.lock is missing required fields", errors)
    scopes = lock.get("scanned_scope")
    exclusions = lock.get("excluded_directories")
    require(isinstance(scopes, list) and bool(scopes), "lock scanned_scope must be a non-empty list", errors)
    require(isinstance(exclusions, list), "lock excluded_directories must be a list", errors)
    require(lock.get("fingerprint_algorithm") == ALGORITHM, "lock fingerprint algorithm does not match", errors)
    head = current_commit(repo)
    source = lock.get("source_commit")
    require(is_full_commit(repo, source), "lock source_commit must be a 40-character commit that exists in this repository", errors)
    require(
        isinstance(source, str) and is_ancestor(repo, source, head),
        "lock source_commit must be HEAD or an ancestor of HEAD; it records the analyzed tree, not the commit that later contains the map",
        errors,
    )
    require(
        data.get("generated_from_commit") == source,
        "JSON generated_from_commit must equal lock source_commit",
        errors,
    )
    require(lock.get("generated_at") == data.get("generated_at"), "JSON and lock generation times differ", errors)
    require(lock.get("scanned_scope") == data.get("scope"), "JSON and lock scopes differ", errors)
    require(
        lock.get("working_tree_dirty") == working_tree_dirty(repo),
        "lock dirty state does not match the working tree (code-map artifacts under docs/codemap/ are ignored)",
        errors,
    )
    if isinstance(scopes, list) and isinstance(exclusions, list):
        expected_modules = snapshot_modules(repo, scopes, exclusions)
        require(lock.get("modules") == expected_modules, "lock module fingerprints do not match", errors)

    if errors:
        raise CodemapError("\n".join(f"- {error}" for error in errors))
    return {
        "ok": True,
        "nodes": len(nodes),
        "edges": len(edges),
        "flows": len(flows),
        "unknown_edges": unknown_edges,
        "source_commit": source,
        "head": head,
        "working_tree_dirty": lock["working_tree_dirty"],
        "html": paths["codemap.html"].is_file(),
    }


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    return status_report(args.repo, args.lock, args.scope, args.exclude)


def status_report(
    repo: str | Path,
    lock: str = "docs/codemap/codemap.lock",
    scopes: list[str] | None = None,
    exclusions: list[str] | None = None,
) -> dict[str, Any]:
    repo = repo_root(str(repo))
    lock_path = resolve_inside(repo, lock)
    if not lock_path.is_file():
        scopes = unique_paths(repo, scopes or ["."])
        exclusions = unique_paths(repo, exclusions, DEFAULT_EXCLUDES)
        modules = snapshot_modules(repo, scopes, exclusions)
        identifiers = [module["id"] for module in modules]
        return {
            "lock_found": False,
            "stale": True,
            "changed_modules": [],
            "new_modules": identifiers,
            "removed_modules": [],
            "stale_modules": identifiers,
            "commit_changed": True,
            "dirty_state_changed": True,
        }
    lock = load_json(lock_path)
    if not isinstance(lock, dict):
        raise CodemapError("codemap.lock must contain an object")
    scopes = lock.get("scanned_scope")
    exclusions = lock.get("excluded_directories")
    if not isinstance(scopes, list) or not scopes or not isinstance(exclusions, list):
        raise CodemapError("codemap.lock has invalid scope or exclusions")
    current = snapshot_modules(repo, scopes, exclusions)
    previous = lock.get("modules")
    if not isinstance(previous, list):
        raise CodemapError("codemap.lock modules must be a list")
    current_by_id = {module["id"]: module for module in current}
    previous_by_id = {module.get("id"): module for module in previous if isinstance(module, dict)}
    shared = current_by_id.keys() & previous_by_id.keys()
    changed = sorted(
        identifier
        for identifier in shared
        if current_by_id[identifier].get("fingerprint") != previous_by_id[identifier].get("fingerprint")
    )
    new = sorted(current_by_id.keys() - previous_by_id.keys())
    removed = sorted(previous_by_id.keys() - current_by_id.keys())
    source_changed = lock.get("source_commit") != current_commit(repo)
    dirty_changed = lock.get("working_tree_dirty") != working_tree_dirty(repo)
    stale_modules = sorted({*changed, *new, *removed})
    return {
        "lock_found": True,
        "stale": bool(stale_modules),
        "changed_modules": changed,
        "new_modules": new,
        "removed_modules": removed,
        "stale_modules": stale_modules,
        "source_commit_changed": source_changed,
        "commit_changed": source_changed,
        "dirty_state_changed": dirty_changed,
    }


def build_lock(
    repo: Path,
    generated_at: str,
    scopes: list[str],
    exclusions: list[str],
) -> dict[str, Any]:
    return {
        "source_commit": current_commit(repo),
        "working_tree_dirty": working_tree_dirty(repo),
        "generated_at": generated_at,
        "scanned_scope": scopes,
        "excluded_directories": exclusions,
        "fingerprint_algorithm": ALGORITHM,
        "modules": snapshot_modules(repo, scopes, exclusions),
    }


def command_lock(args: argparse.Namespace) -> dict[str, Any]:
    repo = repo_root(args.repo)
    scopes = unique_paths(repo, args.scope or ["."])
    exclusions = unique_paths(repo, args.exclude, DEFAULT_EXCLUDES)
    value = build_lock(repo, args.generated_at, scopes, exclusions)
    output = resolve_output(repo, args.output)
    write_json_atomic(output, value)
    return {"ok": True, "output": output.relative_to(repo).as_posix(), "modules": len(value["modules"])}


def command_markdown(args: argparse.Namespace) -> dict[str, Any]:
    repo = repo_root(args.repo)
    json_path = resolve_inside(repo, args.json)
    output = resolve_output(repo, args.output)
    data = load_json(json_path)
    if not isinstance(data, dict):
        raise CodemapError("codemap.json must contain an object")
    write_text_atomic(output, render_markdown(data, repo.name))
    return {"ok": True, "output": output.relative_to(repo).as_posix()}


def command_impact(args: argparse.Namespace) -> dict[str, Any]:
    repo = repo_root(args.repo)
    json_path = resolve_inside(repo, args.json)
    data = load_json(json_path)
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise CodemapError("codemap.json must contain nodes")
    nodes = [node for node in data["nodes"] if isinstance(node, dict)]
    node = find_node(nodes, args.module)
    identifier = node["id"]
    flows = [
        flow
        for flow in data.get("flows") or []
        if isinstance(flow, dict) and identifier in (flow.get("steps") or [])
    ]
    return {
        "ok": True,
        "node": node,
        "callers": node.get("callers") or [],
        "callees": node.get("callees") or [],
        "tests": node.get("tests") or [],
        "flows": flows,
        "evidence": node.get("evidence"),
    }


def command_render(args: argparse.Namespace) -> dict[str, Any]:
    repo = repo_root(args.repo)
    json_path = resolve_inside(repo, args.json)
    output = resolve_output(repo, args.output)
    template = Path(args.template).resolve() if args.template else None
    data = load_json(json_path)
    if not isinstance(data, dict):
        raise CodemapError("codemap.json must contain an object")
    write_text_atomic(output, render_html(data, repo.name, template))
    return {"ok": True, "output": output.relative_to(repo).as_posix()}


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    repo = repo_root(args.repo)
    directory = resolve_inside(repo, args.dir)
    return validate_directory(repo, directory, require_html=True)


def publish_staging(repo: Path, staging: Path, target: Path) -> dict[str, Any]:
    if staging == target:
        raise CodemapError("staging and target must differ")
    result = validate_directory(repo, staging, require_html=True)
    extras = sorted(path.name for path in staging.iterdir() if path.name not in ARTIFACTS)
    if extras:
        raise CodemapError(f"staging contains unexpected files: {', '.join(extras)}")
    target.mkdir(parents=True, exist_ok=True)
    prepared: list[tuple[Path, Path]] = []
    for name in ARTIFACTS:
        destination = target / name
        temporary = target / f".{name}.publish"
        temporary.write_bytes((staging / name).read_bytes())
        prepared.append((temporary, destination))
    for temporary, destination in prepared:
        os.replace(temporary, destination)
    for name in ARTIFACTS:
        (staging / name).unlink()
    staging.rmdir()
    final_result = validate_directory(repo, target, require_html=True)
    final_result["published"] = [str((target / name).relative_to(repo).as_posix()) for name in ARTIFACTS]
    final_result["staging_validation"] = result["ok"]
    return final_result


def command_publish(args: argparse.Namespace) -> dict[str, Any]:
    repo = repo_root(args.repo)
    return publish_staging(repo, resolve_output(repo, args.staging), resolve_output(repo, args.target))


def write_views(repo: Path, staging: Path, model: dict[str, Any], generated_at: str) -> None:
    if not isinstance(model, dict):
        raise CodemapError("codemap.json must contain an object")
    write_json_atomic(staging / "codemap.json", model)
    write_text_atomic(staging / "codemap.md", render_markdown(model, repo.name))
    write_text_atomic(staging / "codemap.html", render_html(model, repo.name))
    scopes = unique_paths(repo, model.get("scope") if isinstance(model.get("scope"), list) else ["."])
    exclusions = unique_paths(repo, None, DEFAULT_EXCLUDES)
    write_json_atomic(staging / "codemap.lock", build_lock(repo, generated_at, scopes, exclusions))


def build_and_publish(
    repo: str | Path,
    *,
    generated_at: str | None = None,
    staging: str = "docs/codemap/.staging",
    target: str = "docs/codemap",
    model: dict[str, Any] | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    repo_path = repo_root(str(repo))
    stamp = generated_at or utc_now()
    staging_dir = resolve_output(repo_path, staging)
    staging_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any]
    if model is None:
        generator = load_sibling("generate_repository_map.py", Path(__file__))
        try:
            model, summary = generator.generate(repo_path, stamp)
        except Exception as error:
            if error.__class__.__name__ == "AnalysisError":
                raise CodemapError(str(error)) from error
            raise
    else:
        summary = {
            "nodes": len(model.get("nodes") or []),
            "edges": len(model.get("edges") or []),
            "flows": len(model.get("flows") or []),
        }
        stamp = str(model.get("generated_at") or stamp)
    write_views(repo_path, staging_dir, model, stamp)
    validation = validate_directory(repo_path, staging_dir, require_html=True)
    result: dict[str, Any] = {**validation, "summary": summary, "generated_at": stamp}
    if not publish:
        result["staging"] = staging_dir.relative_to(repo_path).as_posix()
        return result
    published = publish_staging(repo_path, staging_dir, resolve_output(repo_path, target))
    published["summary"] = summary
    return published


def command_build(args: argparse.Namespace) -> dict[str, Any]:
    return build_and_publish(
        args.repo,
        generated_at=args.generated_at,
        staging=args.staging,
        target=args.target,
        publish=not args.no_publish,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="compare a lock with the current repository")
    status.add_argument("--repo", default=".")
    status.add_argument("--lock", default="docs/codemap/codemap.lock")
    status.add_argument("--scope", action="append")
    status.add_argument("--exclude", action="append")
    status.set_defaults(handler=command_status)

    lock = subparsers.add_parser("lock", help="write deterministic module fingerprints")
    lock.add_argument("--repo", default=".")
    lock.add_argument("--scope", action="append")
    lock.add_argument("--exclude", action="append")
    lock.add_argument("--generated-at", required=True)
    lock.add_argument("--output", required=True)
    lock.set_defaults(handler=command_lock)

    markdown = subparsers.add_parser("markdown", help="render the agent-read Markdown view")
    markdown.add_argument("--repo", default=".")
    markdown.add_argument("--json", required=True)
    markdown.add_argument("--output", required=True)
    markdown.set_defaults(handler=command_markdown)

    impact = subparsers.add_parser("impact", help="print callers, callees, tests, and flows for one module")
    impact.add_argument("--repo", default=".")
    impact.add_argument("--json", default="docs/codemap/codemap.json")
    impact.add_argument("--module", required=True)
    impact.set_defaults(handler=command_impact)

    render = subparsers.add_parser("render", help="render the HTML view from JSON")
    render.add_argument("--repo", default=".")
    render.add_argument("--json", required=True)
    render.add_argument("--output", required=True)
    render.add_argument("--template")
    render.set_defaults(handler=command_render)

    validate = subparsers.add_parser("validate", help="validate an artifact directory")
    validate.add_argument("--repo", default=".")
    validate.add_argument("--dir", required=True)
    validate.add_argument("--html", action="store_true", help="legacy flag; HTML is always required")
    validate.set_defaults(handler=command_validate)

    publish = subparsers.add_parser("publish", help="validate and publish json, markdown, html, and lock")
    publish.add_argument("--repo", default=".")
    publish.add_argument("--staging", required=True)
    publish.add_argument("--target", default="docs/codemap")
    publish.set_defaults(handler=command_publish)

    build = subparsers.add_parser("build", help="analyze, render views, lock, validate, and optionally publish")
    build.add_argument("--repo", default=".")
    build.add_argument("--generated-at")
    build.add_argument("--staging", default="docs/codemap/.staging")
    build.add_argument("--target", default="docs/codemap")
    build.add_argument("--no-publish", action="store_true", help="leave validated artifacts in staging")
    build.set_defaults(handler=command_build)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = args.handler(args)
    except CodemapError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
