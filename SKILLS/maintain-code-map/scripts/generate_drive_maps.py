#!/usr/bin/env python3
"""Discover Git repositories below a drive root and publish validated code maps."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from codemap_common import CodemapError, load_sibling


PRUNE_DIRS = {
    ".cache",
    ".next",
    ".nuxt",
    ".turbo",
    ".venv",
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
REFERENCE_PARTS = {".reference", "reference", "references"}
CODEMAP_ARTIFACTS = ("codemap.json", "codemap.md", "codemap.html", "codemap.lock")


def tool_module() -> Any:
    return load_sibling("codemap_tool.py", Path(__file__))


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def git_root(candidate: Path) -> Path | None:
    result = run(["git", "-C", str(candidate), "rev-parse", "--show-toplevel"])
    if result.returncode:
        return None
    return Path(result.stdout.strip()).resolve()


def has_head(repo: Path) -> bool:
    return run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"]).returncode == 0


def metadata_only(repo: Path) -> bool:
    result = run(["git", "-C", str(repo), "ls-files", "-z"])
    if result.returncode:
        return True
    metadata_names = {
        ".gitattributes",
        ".gitignore",
        "license",
        "license.md",
        "license.txt",
        "readme",
        "readme.md",
        "readme.txt",
    }
    files = [path for path in result.stdout.split("\0") if path]
    return not any("/" in path or path.lower() not in metadata_names for path in files)


def excluded_repo(root: Path, repo: Path, explicit: set[Path]) -> str | None:
    if repo in explicit:
        return "explicit"
    try:
        relative = repo.relative_to(root)
    except ValueError:
        return "outside-root"
    lowered = {part.lower() for part in relative.parts}
    if any(part.startswith(".") for part in relative.parts):
        return "hidden-path"
    if lowered & REFERENCE_PARTS:
        return "reference-clone"
    if not has_head(repo):
        return "no-head"
    if metadata_only(repo):
        return "metadata-only"
    return None


def discover(root: Path, explicit: set[Path]) -> tuple[list[Path], list[dict[str, str]]]:
    found: set[Path] = set()
    excluded: list[dict[str, str]] = []
    for current, directories, files in os.walk(root, topdown=True):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory.lower() not in PRUNE_DIRS and not directory.startswith("$RECYCLE")
        )
        if ".git" not in directories and ".git" not in files:
            continue
        candidate = git_root(Path(current))
        directories[:] = []
        if candidate is None or candidate in found:
            continue
        reason = excluded_repo(root, candidate, explicit)
        if reason:
            excluded.append({"repo": str(candidate), "reason": reason})
            continue
        found.add(candidate)
    repositories = sorted(found, key=lambda path: str(path).lower())
    rejected = sorted(excluded, key=lambda item: item["repo"].lower())
    return repositories, rejected


def cleanup_staging(repo: Path, staging: Path) -> None:
    codemap_root = (repo / "docs" / "codemap").resolve()
    resolved = staging.resolve()
    if resolved.parent != codemap_root or not resolved.name.startswith(".batch-staging-"):
        raise RuntimeError(f"refusing to clean unexpected staging path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    try:
        codemap_root.rmdir()
        codemap_root.parent.rmdir()
    except OSError:
        pass


def failure_message(error: BaseException | str) -> str:
    message = str(error).strip()
    return message[-2000:] if message else "unknown error"


def rerender_repo(repo: Path) -> dict[str, Any]:
    tool = tool_module()
    staging = f"docs/codemap/.batch-staging-{os.getpid()}"
    staging_path = repo / Path(*staging.split("/"))
    try:
        model = json.loads((repo / "docs" / "codemap" / "codemap.json").read_text(encoding="utf-8"))
        result = tool.build_and_publish(repo, model=model, staging=staging, publish=True)
    except (OSError, KeyError, json.JSONDecodeError, CodemapError) as error:
        cleanup_staging(repo, staging_path)
        return {"repo": str(repo), "status": "blocked", "stage": "rerender", "error": failure_message(error)}
    return {
        "repo": str(repo),
        "status": "published",
        "nodes": result.get("nodes"),
        "edges": result.get("edges"),
        "flows": result.get("flows"),
        "unknown_edges": result.get("unknown_edges", []),
        "stale_modules": [],
        "rerendered": True,
    }


def publish_repo(repo: Path, refresh_stale: bool, rerender_existing: bool) -> dict[str, Any]:
    tool = tool_module()
    codemap_root = repo / "docs" / "codemap"
    artifacts = CODEMAP_ARTIFACTS
    stale_modules: list[str] = []
    if all((codemap_root / name).is_file() for name in artifacts):
        try:
            freshness = tool.status_report(repo)
        except CodemapError as error:
            return {
                "repo": str(repo),
                "status": "blocked",
                "stage": "status-existing",
                "error": failure_message(error),
            }
        if freshness.get("stale"):
            stale_modules = freshness.get("stale_modules", [])
            if not refresh_stale:
                return {
                    "repo": str(repo),
                    "status": "blocked",
                    "stage": "stale-existing",
                    "stale_modules": freshness.get("stale_modules", []),
                }
        else:
            try:
                validation = tool.validate_directory(repo, codemap_root, require_html=True)
            except CodemapError as error:
                if not refresh_stale:
                    return {
                        "repo": str(repo),
                        "status": "blocked",
                        "stage": "validate-existing",
                        "error": failure_message(error),
                    }
            else:
                if rerender_existing:
                    return rerender_repo(repo)
                return {
                    "repo": str(repo),
                    "status": "fresh-existing",
                    "nodes": validation.get("nodes"),
                    "edges": validation.get("edges"),
                    "flows": validation.get("flows"),
                    "unknown_edges": validation.get("unknown_edges", []),
                }

    staging = f"docs/codemap/.batch-staging-{os.getpid()}"
    staging_path = repo / Path(*staging.split("/"))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        result = tool.build_and_publish(repo, generated_at=generated_at, staging=staging, publish=True)
    except CodemapError as error:
        cleanup_staging(repo, staging_path)
        return {"repo": str(repo), "status": "blocked", "stage": "build", "error": failure_message(error)}
    return {
        "repo": str(repo),
        "status": "published",
        "nodes": result.get("nodes"),
        "edges": result.get("edges"),
        "flows": result.get("flows"),
        "unknown_edges": result.get("unknown_edges", []),
        "stale_modules": stale_modules,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        required=True,
        help="Workspace parent or drive root to inventory",
    )
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--write", action="store_true", help="publish validated artifacts; otherwise only inventory")
    parser.add_argument("--refresh-stale", action="store_true", help="regenerate existing maps that are stale or invalid")
    parser.add_argument("--rerender-existing", action="store_true", help="republish fresh models with the current Markdown and HTML views")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    explicit = {Path(value).resolve() for value in args.exclude}
    repositories, excluded = discover(root, explicit)
    if args.limit is not None:
        repositories = repositories[: max(0, args.limit)]
    print(
        json.dumps(
            {
                "event": "inventory",
                "root": str(root),
                "eligible": len(repositories),
                "repositories": [str(repo) for repo in repositories],
                "excluded": excluded,
            }
        ),
        flush=True,
    )
    if not args.write:
        return 0

    counts = {"published": 0, "fresh-existing": 0, "blocked": 0}
    results: list[dict[str, Any]] = []
    for index, repo in enumerate(repositories, start=1):
        result = publish_repo(repo, args.refresh_stale, args.rerender_existing)
        results.append(result)
        counts[result["status"]] += 1
        print(json.dumps({"event": "repository", "index": index, "total": len(repositories), **result}), flush=True)
    print(json.dumps({"event": "summary", **counts, "total": len(repositories), "results": results}), flush=True)
    return 1 if counts["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
