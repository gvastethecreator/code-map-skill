#!/usr/bin/env python3
"""Validate the standalone skill package layout."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILLS" / "maintain-code-map"
REQUIRED = [
    SKILL / "SKILL.md",
    SKILL / "agents" / "openai.yaml",
    SKILL / "assets" / "codemap-template.html",
    SKILL / "references" / "artifact-contract.md",
    SKILL / "references" / "diagram-grammar.md",
    SKILL / "scripts" / "codemap_tool.py",
    SKILL / "scripts" / "generate_repository_map.py",
    SKILL / "scripts" / "generate_drive_maps.py",
    SKILL / "scripts" / "test_codemap_tool.py",
    SKILL / "scripts" / "verify_codemap_browser.cjs",
    ROOT / "LICENSE",
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "skills.sh.json",
    ROOT / "evals" / "cases.json",
]
MACHINE_LOCAL = re.compile(
    r"(?i)(?:\b[A-Z]:\\(?:Users|DEV|agents-matrix|skills)\\|X:/Users/)"
)
FRONTMATTER = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)")


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    for path in REQUIRED:
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")

    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        fail("SKILL.md is missing YAML frontmatter")
    fields = {
        line.split(":", 1)[0].strip()
        for line in match.group(1).splitlines()
        if ":" in line
    }
    for field in ("name", "description", "license"):
        if field not in fields:
            fail(f"SKILL.md frontmatter missing {field}")
    if "name: maintain-code-map" not in match.group(1):
        fail("SKILL.md name must be maintain-code-map")
    if "license: MIT" not in match.group(1):
        fail("SKILL.md license must be MIT")

    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    if cases.get("version") != 1 or not cases.get("cases"):
        fail("evals/cases.json must have version 1 and at least one case")

    for path in SKILL.rglob("*"):
        if not path.is_file() or path.suffix in {".pyc"}:
            continue
        if path.name.endswith(".woff2"):
            fail(f"unexpected binary font {path.relative_to(ROOT)}")
        sample = path.read_text(encoding="utf-8", errors="ignore")
        if MACHINE_LOCAL.search(sample):
            fail(f"machine-local path in {path.relative_to(ROOT)}")

    print("package validation ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
