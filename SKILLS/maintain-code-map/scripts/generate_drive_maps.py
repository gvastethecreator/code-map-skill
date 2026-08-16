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


def render_html_command(repo: Path, tool: Path, relative_staging: str) -> tuple[str, list[str]]:
    return (
        "render",
        [
            sys.executable,
            "-B",
            str(tool),
            "render",
            "--repo",
            str(repo),
            "--json",
            f"{relative_staging}/codemap.json",
            "--output",
            f"{relative_staging}/codemap.html",
        ],
    )


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


def failure_message(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout).strip()
    return message[-2000:] if message else f"exit {result.returncode}"


def rerender_repo(repo: Path, scripts: Path) -> dict[str, Any]:
    codemap_root = repo / "docs" / "codemap"
    staging = codemap_root / f".batch-staging-{os.getpid()}"
    relative_staging = staging.relative_to(repo).as_posix()
    tool = scripts / "codemap_tool.py"
    try:
        model = json.loads((codemap_root / "codemap.json").read_text(encoding="utf-8"))
        generated_at = model["generated_at"]
        staging.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(codemap_root / "codemap.json", staging / "codemap.json")
    except (OSError, KeyError, json.JSONDecodeError) as error:
        cleanup_staging(repo, staging)
        return {"repo": str(repo), "status": "blocked", "stage": "stage-rerender", "error": str(error)}
    commands = [
        (
            "markdown",
            [
                sys.executable,
                "-B",
                str(tool),
                "markdown",
                "--repo",
                str(repo),
                "--json",
                f"{relative_staging}/codemap.json",
                "--output",
                f"{relative_staging}/codemap.md",
            ],
        ),
        render_html_command(repo, tool, relative_staging),
        (
            "lock",
            [
                sys.executable,
                "-B",
                str(tool),
                "lock",
                "--repo",
                str(repo),
                "--scope",
                ".",
                "--generated-at",
                generated_at,
                "--output",
                f"{relative_staging}/codemap.lock",
            ],
        ),
        (
            "validate",
            [sys.executable, "-B", str(tool), "validate", "--repo", str(repo), "--dir", relative_staging],
        ),
        (
            "publish",
            [
                sys.executable,
                "-B",
                str(tool),
                "publish",
                "--repo",
                str(repo),
                "--staging",
                relative_staging,
                "--target",
                "docs/codemap",
            ],
        ),
    ]
    outputs: dict[str, Any] = {}
    for stage, command in commands:
        result = run(command)
        if result.returncode:
            cleanup_staging(repo, staging)
            return {"repo": str(repo), "status": "blocked", "stage": stage, "error": failure_message(result)}
        outputs[stage] = json.loads(result.stdout)
    validation = outputs["validate"]
    return {
        "repo": str(repo),
        "status": "published",
        "nodes": validation.get("nodes"),
        "edges": validation.get("edges"),
        "flows": validation.get("flows"),
        "unknown_edges": validation.get("unknown_edges", []),
        "stale_modules": [],
        "rerendered": True,
    }


def publish_repo(repo: Path, scripts: Path, refresh_stale: bool, rerender_existing: bool) -> dict[str, Any]:
    codemap_root = repo / "docs" / "codemap"
    artifacts = CODEMAP_ARTIFACTS
    tool = scripts / "codemap_tool.py"
    stale_modules: list[str] = []
    if all((codemap_root / name).is_file() for name in artifacts):
        status_result = run(
            [
                sys.executable,
                "-B",
                str(tool),
                "status",
                "--repo",
                str(repo),
                "--lock",
                "docs/codemap/codemap.lock",
                "--scope",
                ".",
            ]
        )
        if status_result.returncode:
            return {
                "repo": str(repo),
                "status": "blocked",
                "stage": "status-existing",
                "error": failure_message(status_result),
            }
        freshness = json.loads(status_result.stdout)
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
            validation_result = run(
                [sys.executable, "-B", str(tool), "validate", "--repo", str(repo), "--dir", "docs/codemap"]
            )
            if validation_result.returncode and not refresh_stale:
                return {
                    "repo": str(repo),
                    "status": "blocked",
                    "stage": "validate-existing",
                    "error": failure_message(validation_result),
                }
            if not validation_result.returncode:
                validation = json.loads(validation_result.stdout)
                if rerender_existing:
                    return rerender_repo(repo, scripts)
                return {
                    "repo": str(repo),
                    "status": "fresh-existing",
                    "nodes": validation.get("nodes"),
                    "edges": validation.get("edges"),
                    "flows": validation.get("flows"),
                    "unknown_edges": validation.get("unknown_edges", []),
                }

    staging = codemap_root / f".batch-staging-{os.getpid()}"
    relative_staging = staging.relative_to(repo).as_posix()
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    generator = scripts / "generate_repository_map.py"
    commands = [
        (
            "analyze",
            [
                sys.executable,
                "-B",
                str(generator),
                "--repo",
                str(repo),
                "--output",
                f"{relative_staging}/codemap.json",
                "--generated-at",
                generated_at,
            ],
        ),
        (
            "markdown",
            [
                sys.executable,
                "-B",
                str(tool),
                "markdown",
                "--repo",
                str(repo),
                "--json",
                f"{relative_staging}/codemap.json",
                "--output",
                f"{relative_staging}/codemap.md",
            ],
        ),
        render_html_command(repo, tool, relative_staging),
        (
            "lock",
            [
                sys.executable,
                "-B",
                str(tool),
                "lock",
                "--repo",
                str(repo),
                "--scope",
                ".",
                "--generated-at",
                generated_at,
                "--output",
                f"{relative_staging}/codemap.lock",
            ],
        ),
        (
            "validate",
            [sys.executable, "-B", str(tool), "validate", "--repo", str(repo), "--dir", relative_staging],
        ),
        (
            "publish",
            [
                sys.executable,
                "-B",
                str(tool),
                "publish",
                "--repo",
                str(repo),
                "--staging",
                relative_staging,
                "--target",
                "docs/codemap",
            ],
        ),
    ]
    outputs: dict[str, Any] = {}
    for stage, command in commands:
        result = run(command)
        if result.returncode:
            cleanup_staging(repo, staging)
            return {"repo": str(repo), "status": "blocked", "stage": stage, "error": failure_message(result)}
        try:
            outputs[stage] = json.loads(result.stdout)
        except json.JSONDecodeError:
            outputs[stage] = result.stdout.strip()
    validation = outputs.get("validate", {})
    return {
        "repo": str(repo),
        "status": "published",
        "nodes": validation.get("nodes"),
        "edges": validation.get("edges"),
        "flows": validation.get("flows"),
        "unknown_edges": validation.get("unknown_edges", []),
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

    scripts = Path(__file__).resolve().parent
    counts = {"published": 0, "fresh-existing": 0, "blocked": 0}
    results: list[dict[str, Any]] = []
    for index, repo in enumerate(repositories, start=1):
        result = publish_repo(repo, scripts, args.refresh_stale, args.rerender_existing)
        results.append(result)
        counts[result["status"]] += 1
        print(json.dumps({"event": "repository", "index": index, "total": len(repositories), **result}), flush=True)
    print(json.dumps({"event": "summary", **counts, "total": len(repositories), "results": results}), flush=True)
    return 1 if counts["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
