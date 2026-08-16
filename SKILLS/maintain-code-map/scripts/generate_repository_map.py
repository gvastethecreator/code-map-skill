#!/usr/bin/env python3
"""Generate a conservative code-map model from tracked source references."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys
from typing import Any, Iterable


EXCLUDED_PARTS = {
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
    "generated",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
}
MAX_EVIDENCE_LOCATIONS = 3
TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".csproj",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".mjs",
    ".cjs",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sh",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".webmanifest",
    ".xaml",
    ".yaml",
    ".yml",
}
CODE_EXTENSIONS = TEXT_EXTENSIONS - {".md", ".json", ".toml", ".yaml", ".yml"}
CONTAINER_DIRS = {
    "app",
    "apps",
    "cmd",
    "core",
    "crates",
    "engine",
    "internal",
    "lib",
    "libs",
    "mcp",
    "packages",
    "rulesets",
    "scripts",
    "server",
    "skills",
    "src",
    "tests",
}
ENTRY_NAMES = {
    "__init__",
    "app",
    "cli",
    "index",
    "lib",
    "main",
    "mod",
    "server",
    "skill",
}
TEST_MARKERS = {"test", "tests", "spec", "specs", "__tests__"}
AUXILIARY_ROOTS = {".circleci", ".github", ".vscode", "docs", "examples", "test", "tests"}
MANIFEST_NAMES = {
    "cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}
RESOLVE_EXTENSIONS = (
    "",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".rs",
    ".go",
    ".cs",
    ".vue",
    ".svelte",
    ".json",
    ".md",
)


class AnalysisError(RuntimeError):
    pass


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise AnalysisError(message or f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", errors="surrogateescape")


def repository_root(value: str) -> Path:
    candidate = Path(value).resolve()
    return Path(run_git(candidate, "rev-parse", "--show-toplevel").strip()).resolve()


def tracked_files(repo: Path) -> list[str]:
    output = run_git(repo, "-c", "core.quotepath=false", "ls-files", "-z")
    return sorted(path for path in output.split("\0") if path)


def is_excluded(relative: str) -> bool:
    parts = set(PurePosixPath(relative).parts)
    return bool(parts & EXCLUDED_PARTS) or relative.startswith("docs/codemap/")


def is_test_path(relative: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    stem = PurePosixPath(relative).stem.lower()
    return bool(set(parts) & TEST_MARKERS) or stem.endswith((".test", ".spec", "_test")) or stem.startswith("test_")


def read_text(repo: Path, relative: str, cache: dict[str, str]) -> str:
    if relative in cache:
        return cache[relative]
    path = repo / Path(*PurePosixPath(relative).parts)
    try:
        if path.stat().st_size > 2_000_000:
            content = ""
        else:
            content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        content = ""
    cache[relative] = content
    return content


def module_key(relative: str) -> str:
    path = PurePosixPath(relative)
    parts = path.parts
    if len(parts) == 1:
        if path.suffix.lower() in CODE_EXTENSIONS:
            return path.with_suffix("").as_posix()
        return "repository"
    first = parts[0]
    if len(parts) == 2 and first.lower().endswith("prototype") and path.suffix.lower() in CODE_EXTENSIONS:
        return path.with_suffix("").as_posix()
    if first.lower() == "native" and len(parts) >= 3:
        depth = 3 if len(parts) >= 4 else 2
        return "/".join(parts[:depth])
    if len(parts) >= 3 and parts[1].lower() in {"css", "js", "scripts", "src"}:
        return f"{first}/{parts[1]}"
    if first in CONTAINER_DIRS and len(parts) >= 3:
        path = PurePosixPath(relative)
        if path.suffix.lower() in {".py", ".rs"}:
            if path.stem.lower() in {"__init__", "mod"}:
                return path.parent.as_posix()
            return path.with_suffix("").as_posix()
        return f"{first}/{parts[1]}"
    return first


def is_primary_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    if is_test_path(relative) or path.parts[0].lower() in AUXILIARY_ROOTS:
        return False
    return path.suffix.lower() in CODE_EXTENSIONS or path.name.lower() in MANIFEST_NAMES


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "module"


def extract_symbol(relative: str, content: str) -> str:
    suffix = PurePosixPath(relative).suffix.lower()
    patterns = [
        r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
        r"(?m)^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)",
        r"(?m)^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)",
        r"(?m)^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)",
        r"(?m)^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)",
        r"(?m)^\s*class\s+([A-Za-z_]\w*)",
        r"(?m)^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)",
        r"(?m)^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)",
        r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)",
        r"(?m)^\s*(?:public\s+|internal\s+)?(?:class|interface|record|struct)\s+([A-Za-z_]\w*)",
        r"(?mi)^\s*function\s+([A-Za-z_]\w*)",
    ]
    if suffix == ".md":
        patterns.insert(0, r"(?m)^#{1,3}\s+(.+?)\s*$")
    if suffix == ".html":
        patterns.insert(0, r"(?is)<title>\s*(.+?)\s*</title>")
    if suffix == ".json":
        patterns.insert(0, r'^\s*"([^"\r\n]+)"\s*:')
    if suffix == ".toml":
        patterns.insert(0, r"(?m)^\s*\[([^\]\r\n]+)\]")
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            symbol = match.group(1).strip()
            if symbol and symbol in content:
                return symbol[:160]
    for line in content.splitlines():
        symbol = line.strip()
        if symbol:
            return symbol[:160]
    return PurePosixPath(relative).name


def entry_score(relative: str) -> tuple[int, int, str]:
    path = PurePosixPath(relative)
    stem = path.stem.lower()
    score = 0
    if stem in ENTRY_NAMES:
        score += 20
    if path.name.lower() in {"readme.md", "package.json", "cargo.toml", "pyproject.toml", "go.mod"}:
        score += 12
    if path.suffix.lower() in CODE_EXTENSIONS:
        score += 6
    if is_test_path(relative):
        score -= 20
    return (-score, len(path.parts), relative)


def normalize_candidate(value: str) -> str:
    return posixpath.normpath(value.replace("\\", "/")).lstrip("./")


def resolve_path(source: str, specifier: str, files: set[str]) -> str | None:
    clean = specifier.split("?", 1)[0].split("#", 1)[0]
    direct = normalize_candidate(clean)
    if direct in files:
        return direct
    relative_candidate = normalize_candidate(posixpath.join(posixpath.dirname(source), clean))
    if relative_candidate in files:
        return relative_candidate
    if clean.startswith("@/"):
        base = f"src/{clean[2:]}"
    elif clean.startswith("~/"):
        base = f"src/{clean[2:]}"
    elif clean.startswith("."):
        base = normalize_candidate(posixpath.join(posixpath.dirname(source), clean))
    else:
        return None
    candidates: list[str] = []
    suffix = PurePosixPath(base).suffix.lower()
    if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        base_without_suffix = str(PurePosixPath(base).with_suffix(""))
        candidates.extend(f"{base_without_suffix}{extension}" for extension in RESOLVE_EXTENSIONS)
    candidates.extend(f"{base}{extension}" for extension in RESOLVE_EXTENSIONS)
    candidates.extend(f"{base}/index{extension}" for extension in RESOLVE_EXTENSIONS if extension)
    candidates.append(f"{base}/SKILL.md")
    for candidate in candidates:
        normalized = normalize_candidate(candidate)
        if normalized in files:
            return normalized
    return None


def package_name(specifier: str) -> str:
    if specifier.startswith("@"):
        return "/".join(specifier.split("/")[:2])
    return specifier.split("/", 1)[0]


def add_reference(
    references: list[dict[str, str]],
    source: str,
    target: str | None,
    symbol: str,
    edge_type: str,
    external: str | None = None,
) -> None:
    references.append(
        {"source": source, "target": target or "", "symbol": symbol, "type": edge_type, "external": external or ""}
    )


def parse_javascript(relative: str, content: str, files: set[str], references: list[dict[str, str]]) -> None:
    patterns = [
        r"(?:import|export)\s+(?:[\s\S]*?\s+from\s+)?[\"']([^\"']+)[\"']",
        r"require\(\s*[\"']([^\"']+)[\"']\s*\)",
        r"import\(\s*[\"']([^\"']+)[\"']\s*\)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, content):
            specifier = match.group(1)
            target = resolve_path(relative, specifier, files)
            if target:
                add_reference(references, relative, target, specifier, "imports")
            elif not specifier.startswith((".", "@/", "~/", "node:", "bun:")):
                add_reference(references, relative, None, specifier, "imports", package_name(specifier))
    for match in re.finditer(r"(?:serviceWorker\.register|new\s+(?:Shared)?Worker)\(\s*[\"']([^\"']+)[\"']", content):
        specifier = match.group(1)
        target = resolve_path(relative, specifier, files)
        if target:
            add_reference(references, relative, target, specifier, "calls")


def resolve_python(relative: str, module: str, files: set[str]) -> str | None:
    leading = len(module) - len(module.lstrip("."))
    name = module.lstrip(".")
    if leading:
        directory = posixpath.dirname(relative)
        for _ in range(max(0, leading - 1)):
            directory = posixpath.dirname(directory)
        base = posixpath.join(directory, name.replace(".", "/"))
    else:
        base = name.replace(".", "/")
    candidates = [f"{base}.py", f"{base}/__init__.py", f"src/{base}.py", f"src/{base}/__init__.py"]
    return next((normalize_candidate(candidate) for candidate in candidates if normalize_candidate(candidate) in files), None)


def parse_python(relative: str, content: str, files: set[str], references: list[dict[str, str]]) -> None:
    for match in re.finditer(r"(?m)^\s*from\s+([.A-Za-z_]\w*(?:\.\w+)*|\.+)\s+import\s+([^\r\n]+)", content):
        module = match.group(1)
        target = resolve_python(relative, module, files)
        if not target and module.strip(".") == "":
            for imported in re.findall(r"[A-Za-z_]\w*", match.group(2).split("#", 1)[0]):
                target = resolve_python(relative, f"{module}{imported}", files)
                if target:
                    add_reference(references, relative, target, imported, "imports")
            continue
        if target:
            add_reference(references, relative, target, module, "imports")
        elif not module.startswith("."):
            add_reference(references, relative, None, module, "imports", module.split(".", 1)[0])
    for match in re.finditer(r"(?m)^\s*import\s+([A-Za-z_]\w*(?:\.\w+)*)", content):
        module = match.group(1)
        target = resolve_python(relative, module, files)
        if target:
            add_reference(references, relative, target, module, "imports")
        else:
            add_reference(references, relative, None, module, "imports", module.split(".", 1)[0])


def resolve_rust(name: str, files: set[str]) -> str | None:
    candidates = [f"src/{name}.rs", f"src/{name}/mod.rs", f"{name}.rs", f"{name}/mod.rs"]
    return next((candidate for candidate in candidates if candidate in files), None)


def parse_rust(relative: str, content: str, files: set[str], references: list[dict[str, str]]) -> None:
    for match in re.finditer(r"(?m)^\s*(?:pub\s+)?mod\s+([A-Za-z_]\w*)\s*;", content):
        name = match.group(1)
        local_candidates = [
            normalize_candidate(posixpath.join(posixpath.dirname(relative), f"{name}.rs")),
            normalize_candidate(posixpath.join(posixpath.dirname(relative), name, "mod.rs")),
        ]
        target = next((candidate for candidate in local_candidates if candidate in files), None)
        if target:
            add_reference(references, relative, target, name, "imports")
    for match in re.finditer(r"(?m)^\s*use\s+([A-Za-z_]\w*)(?:::[^;]+)?;", content):
        root = match.group(1)
        if root == "crate":
            crate_match = re.match(r"crate::([A-Za-z_]\w*)", match.group(0).split("use", 1)[1].strip())
            if crate_match:
                target = resolve_rust(crate_match.group(1), files)
                if target:
                    add_reference(references, relative, target, crate_match.group(1), "imports")
        elif root not in {"self", "super", "std", "core", "alloc"}:
            add_reference(references, relative, None, root, "imports", root)


def go_module_name(repo: Path, files: set[str], cache: dict[str, str]) -> str:
    if "go.mod" not in files:
        return ""
    match = re.search(r"(?m)^module\s+(\S+)", read_text(repo, "go.mod", cache))
    return match.group(1) if match else ""


def parse_go(
    relative: str,
    content: str,
    files: set[str],
    references: list[dict[str, str]],
    module_name: str,
) -> None:
    specifiers = re.findall(r'(?m)^\s*import\s+\"([^\"]+)\"', content)
    for block in re.finditer(r"(?ms)^\s*import\s*\((.*?)\)", content):
        specifiers.extend(re.findall(r'\"([^\"]+)\"', block.group(1)))
    for specifier in specifiers:
        if "/" not in specifier and specifier in {"fmt", "os", "io", "sync", "time", "errors", "context", "strings"}:
            continue
        if module_name and specifier.startswith(f"{module_name}/"):
            directory = specifier[len(module_name) + 1 :]
            target = next((path for path in files if path.startswith(f"{directory}/") and path.endswith(".go")), None)
            if target:
                add_reference(references, relative, target, specifier, "imports")
        elif "/" in specifier:
            add_reference(references, relative, None, specifier, "imports", specifier.split("/", 1)[0])


def parse_markdown(relative: str, content: str, files: set[str], references: list[dict[str, str]]) -> None:
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", content):
        specifier = match.group(1).strip().split(" ", 1)[0].strip("<>")
        if not specifier or specifier.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = resolve_path(relative, specifier, files)
        if target:
            add_reference(references, relative, target, specifier, "reads")
    for match in re.finditer(r"(?<!`)`([^`\r\n]{1,200})`(?!`)", content):
        specifier = match.group(1).strip().rstrip(".,:;)")
        if not specifier or any(character.isspace() for character in specifier):
            continue
        target = resolve_path(relative, specifier, files)
        if target:
            add_reference(references, relative, target, match.group(1).strip(), "reads")


def parse_html(relative: str, content: str, files: set[str], references: list[dict[str, str]]) -> None:
    for match in re.finditer(r"(?i)(?:src|href)=[\"']([^\"']+)[\"']", content):
        specifier = match.group(1)
        if specifier.startswith(("http://", "https://", "//")):
            add_reference(references, relative, None, specifier, "imports", specifier.split("/", 3)[2] if "//" in specifier else specifier)
            continue
        target = resolve_path(relative, specifier, files)
        if target:
            add_reference(references, relative, target, specifier, "imports")


def manifest_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from manifest_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from manifest_strings(item)


def parse_package_json(relative: str, content: str, files: set[str], references: list[dict[str, str]]) -> None:
    try:
        package = json.loads(content)
    except json.JSONDecodeError:
        return
    if not isinstance(package, dict):
        return
    for field in ("main", "module", "types", "typings", "bin", "exports"):
        for specifier in manifest_strings(package.get(field)):
            target = resolve_path(relative, f"./{specifier.lstrip('./')}", files)
            if target:
                add_reference(references, relative, target, specifier, "reads")
    scripts = package.get("scripts")
    if isinstance(scripts, dict):
        for command in scripts.values():
            if not isinstance(command, str):
                continue
            for specifier in re.findall(r"(?:^|\s)([./A-Za-z0-9_-]+\.(?:[cm]?[jt]s|py|ps1|sh))(?:\s|$)", command):
                target = resolve_path(relative, specifier, files)
                if target:
                    add_reference(references, relative, target, specifier, "calls")


def parse_csharp_references(
    repo: Path,
    files: list[str],
    cache: dict[str, str],
    references: list[dict[str, str]],
) -> None:
    symbols: dict[str, list[str]] = defaultdict(list)
    for relative in files:
        if PurePosixPath(relative).suffix.lower() != ".cs":
            continue
        content = read_text(repo, relative, cache)
        for match in re.finditer(
            r"(?m)^\s*(?:(?:public|internal|private|protected|static|sealed|abstract|partial)\s+)*(?:class|record|struct)\s+([A-Za-z_]\w*)",
            content,
        ):
            symbols[match.group(1)].append(relative)
    unique_symbols = {symbol: paths[0] for symbol, paths in symbols.items() if len(paths) == 1}
    if not unique_symbols:
        return
    usage_pattern = re.compile(r"\bnew\s+(?:global::)?(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\s*(?:\(|\{|\[)")
    static_pattern = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\.[A-Za-z_]\w*\s*\(")
    for relative in files:
        if PurePosixPath(relative).suffix.lower() != ".cs":
            continue
        content = read_text(repo, relative, cache)
        used = [match.group(1) for match in usage_pattern.finditer(content)]
        used.extend(match.group(1) for match in static_pattern.finditer(content))
        for symbol in dict.fromkeys(used):
            target = unique_symbols.get(symbol)
            if target and target != relative:
                add_reference(references, relative, target, symbol, "calls")


def parse_skill_references(
    repo: Path,
    files: list[str],
    cache: dict[str, str],
    references: list[dict[str, str]],
) -> None:
    skill_names: dict[str, str] = {}
    for relative in files:
        if not relative.endswith("/SKILL.md") and relative != "SKILL.md":
            continue
        content = read_text(repo, relative, cache)
        match = re.search(r"(?m)^name:\s*[\"']?([^\"'\r\n]+?)[\"']?\s*$", content)
        if match:
            skill_names[match.group(1).strip()] = relative
    for relative in files:
        if PurePosixPath(relative).suffix.lower() != ".md":
            continue
        content = read_text(repo, relative, cache)
        for skill_name, target in skill_names.items():
            if target == relative:
                continue
            if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(skill_name)}(?![A-Za-z0-9_-])", content):
                add_reference(references, relative, target, skill_name, "reads")


def parse_project_references(relative: str, content: str, files: set[str], references: list[dict[str, str]]) -> None:
    for match in re.finditer(r"(?i)<ProjectReference\s+Include=[\"']([^\"']+)[\"']", content):
        specifier = match.group(1)
        target = resolve_path(relative, specifier, files)
        if target:
            add_reference(references, relative, target, specifier, "imports")


def collect_references(repo: Path, files: list[str], cache: dict[str, str]) -> list[dict[str, str]]:
    file_set = set(files)
    references: list[dict[str, str]] = []
    module_name = go_module_name(repo, file_set, cache)
    for relative in files:
        suffix = PurePosixPath(relative).suffix.lower()
        content = read_text(repo, relative, cache)
        if not content:
            continue
        if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"}:
            parse_javascript(relative, content, file_set, references)
        elif suffix == ".py":
            parse_python(relative, content, file_set, references)
        elif suffix == ".rs":
            parse_rust(relative, content, file_set, references)
        elif suffix == ".go":
            parse_go(relative, content, file_set, references, module_name)
        elif suffix == ".md":
            parse_markdown(relative, content, file_set, references)
        elif suffix in {".html", ".htm"}:
            parse_html(relative, content, file_set, references)
        elif suffix == ".csproj":
            parse_project_references(relative, content, file_set, references)
        if PurePosixPath(relative).name == "package.json":
            parse_package_json(relative, content, file_set, references)
    parse_csharp_references(repo, files, cache, references)
    parse_skill_references(repo, files, cache, references)
    return references


def node_type(identifier: str) -> str:
    value = identifier.lower()
    if any(token in value for token in ("db", "database", "store", "storage", "migration", "schema")):
        return "database"
    if any(token in value for token in ("queue", "event", "worker", "message", "job")):
        return "queue"
    if any(token in value for token in ("api", "route", "page", "ui", "component", "web", "cli")):
        return "interface"
    if any(token in value for token in ("service", "server", "engine", "core", "runtime", "script")):
        return "service"
    return "module"


def boundary_name(identifier: str) -> str:
    first = identifier.split("/", 1)[0]
    if first == "repository":
        return "Repository"
    return first.replace("-", " ").title()


def group_tests(group: str, tests: list[str]) -> list[str]:
    tokens = {token for token in re.split(r"[^a-z0-9]+", group.lower()) if len(token) > 2}
    matched = [test for test in tests if tokens & set(re.split(r"[^a-z0-9]+", test.lower()))]
    return matched[:5]


def make_nodes(
    repo: Path,
    groups: dict[str, list[str]],
    selected: list[str],
    external_reference: dict[str, str] | None,
    test_coverage: dict[str, list[str]],
    cache: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    nodes: list[dict[str, Any]] = []
    identifiers: dict[str, str] = {}
    used_ids: set[str] = set()
    for group in selected:
        identifier = slug(group)
        suffix = 2
        base = identifier
        while identifier in used_ids:
            identifier = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(identifier)
        identifiers[group] = identifier
        representatives = sorted(groups[group], key=entry_score)
        representative = representatives[0]
        content = read_text(repo, representative, cache)
        symbol = extract_symbol(representative, content)
        group_path = repo / Path(*PurePosixPath(group).parts)
        path_value = group if group != "repository" and group_path.exists() else representative
        module_type = node_type(group)
        nodes.append(
            {
                "id": identifier,
                "path": path_value,
                "type": module_type,
                "boundary": boundary_name(group),
                "entrypoints": [f"{representative}:{symbol}"],
                "tests": sorted(test_coverage.get(group, []))[:5],
                "callers": [],
                "callees": [],
                "evidence": {"status": "verified", "locations": [{"path": representative, "symbol": symbol}]},
            }
        )
    if external_reference:
        nodes.append(
            {
                "id": "external-dependencies",
                "path": external_reference["source"],
                "type": "external",
                "boundary": "External",
                "entrypoints": [f"{external_reference['source']}:{external_reference['symbol']}"],
                "tests": [],
                "callers": [],
                "callees": [],
                "evidence": {
                    "status": "verified",
                    "locations": [{"path": external_reference["source"], "symbol": external_reference["symbol"]}],
                },
            }
        )
    return sorted(nodes, key=lambda node: node["id"]), identifiers


def aggregate_edges(
    references: list[dict[str, str]],
    file_groups: dict[str, str],
    identifiers: dict[str, str],
    fallback_group: str | None,
) -> list[dict[str, Any]]:
    aggregated: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for reference in references:
        source_group = file_groups.get(reference["source"])
        if not source_group:
            continue
        mapped_source = source_group if source_group in identifiers else fallback_group
        if not mapped_source or mapped_source not in identifiers:
            continue
        if reference["external"]:
            target_id = "external-dependencies"
        else:
            target_group = file_groups.get(reference["target"])
            if not target_group:
                continue
            mapped_target = target_group if target_group in identifiers else fallback_group
            if not mapped_target or mapped_target not in identifiers:
                continue
            target_id = identifiers[mapped_target]
        source_id = identifiers[mapped_source]
        if source_id == target_id:
            continue
        key = (source_id, target_id, reference["type"])
        location = {"path": reference["source"], "symbol": reference["symbol"]}
        if location not in aggregated[key] and len(aggregated[key]) < MAX_EVIDENCE_LOCATIONS:
            aggregated[key].append(location)
    return [
        {
            "from": source,
            "to": target,
            "type": edge_type,
            "evidence": {"status": "verified", "locations": locations},
        }
        for (source, target, edge_type), locations in sorted(aggregated.items())
    ]


def attach_relations(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    callers: dict[str, list[dict[str, str]]] = {node["id"]: [] for node in nodes}
    callees: dict[str, list[dict[str, str]]] = {node["id"]: [] for node in nodes}
    seen_callers: dict[str, set[tuple[str, str]]] = {node["id"]: set() for node in nodes}
    seen_callees: dict[str, set[tuple[str, str]]] = {node["id"]: set() for node in nodes}
    for edge in edges:
        source = edge["from"]
        target = edge["to"]
        edge_type = edge["type"]
        if source in callees and (target, edge_type) not in seen_callees[source]:
            callees[source].append({"id": target, "type": edge_type})
            seen_callees[source].add((target, edge_type))
        if target in callers and (source, edge_type) not in seen_callers[target]:
            callers[target].append({"id": source, "type": edge_type})
            seen_callers[target].add((source, edge_type))
    for node in nodes:
        node["callers"] = sorted(callers[node["id"]], key=lambda item: (item["id"], item["type"]))
        node["callees"] = sorted(callees[node["id"]], key=lambda item: (item["id"], item["type"]))


def make_flows(_nodes: list[dict[str, Any]], _edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return []


def generate(repo: Path, generated_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    all_files = [path for path in tracked_files(repo) if not is_excluded(path)]
    text_files = [path for path in all_files if PurePosixPath(path).suffix.lower() in TEXT_EXTENSIONS]
    if not text_files:
        raise AnalysisError("no tracked text source files")
    cache: dict[str, str] = {}
    evidence_files = [path for path in text_files if read_text(repo, path, cache).strip()]
    if not evidence_files:
        raise AnalysisError("no non-empty tracked text source files")
    groups: dict[str, list[str]] = defaultdict(list)
    file_groups: dict[str, str] = {}
    for relative in evidence_files:
        group = module_key(relative)
        groups[group].append(relative)
        file_groups[relative] = group
    references = collect_references(repo, evidence_files, cache)
    primary_files = [path for path in evidence_files if is_primary_path(path)]
    if not primary_files:
        primary_files = [path for path in evidence_files if not is_test_path(path)]
    primary_file_set = set(primary_files)
    primary_groups = {file_groups[path] for path in primary_files}
    external_references = [
        reference
        for reference in references
        if reference["external"] and reference["source"] in primary_file_set
    ]

    scores: dict[str, int] = {group: len(groups[group]) for group in primary_groups}
    for reference in references:
        source_group = file_groups.get(reference["source"])
        target_group = file_groups.get(reference["target"])
        if source_group in scores:
            scores[source_group] += 4
        if target_group in scores:
            scores[target_group] += 4
    for group in primary_groups:
        scores[group] += sum(8 for path in groups[group] if PurePosixPath(path).stem.lower() in ENTRY_NAMES)

    node_limit = 19 if external_references else 20
    ranked_groups = sorted(primary_groups, key=lambda group: (-scores[group], group))
    selected = ranked_groups[:node_limit]
    if len(ranked_groups) > node_limit:
        selected = ranked_groups[: node_limit - 1]
        collapsed_groups = set(ranked_groups[node_limit - 1 :])
        groups["other-modules"] = sorted(
            path for group in collapsed_groups for path in groups[group]
        )
        for relative in groups["other-modules"]:
            file_groups[relative] = "other-modules"
        selected.append("other-modules")
    test_coverage: dict[str, list[str]] = defaultdict(list)
    for reference in references:
        if not is_test_path(reference["source"]) or not reference["target"]:
            continue
        target_group = file_groups.get(reference["target"])
        if target_group in selected and reference["source"] not in test_coverage[target_group]:
            test_coverage[target_group].append(reference["source"])
    nodes, identifiers = make_nodes(
        repo,
        groups,
        selected,
        external_references[0] if external_references else None,
        test_coverage,
        cache,
    )
    edges = aggregate_edges(references, file_groups, identifiers, None)
    attach_relations(nodes, edges)
    flows = make_flows(nodes, edges)
    model = {
        "generated_at": generated_at,
        "generated_from_commit": run_git(repo, "rev-parse", "HEAD").strip(),
        "scope": ["."],
        "nodes": nodes,
        "edges": edges,
        "flows": flows,
    }
    summary = {
        "repo": str(repo),
        "tracked_files": len(all_files),
        "text_files": len(text_files),
        "evidence_files": len(evidence_files),
        "groups": len(groups),
        "nodes": len(nodes),
        "edges": len(edges),
        "flows": len(flows),
        "external_references": len(external_references),
    }
    return model, summary


def output_path(repo: Path, value: str) -> Path:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
    codemap_root = (repo / "docs" / "codemap").resolve()
    try:
        resolved.relative_to(codemap_root)
    except ValueError as error:
        raise AnalysisError(f"output must stay under docs/codemap: {value}") from error
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--generated-at", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    args = parser.parse_args()
    try:
        repo = repository_root(args.repo)
        model, summary = generate(repo, args.generated_at)
        target = output_path(repo, args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, target)
    except AnalysisError as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **summary, "output": target.relative_to(repo).as_posix()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
