#!/usr/bin/env python3
"""Prove Markdown derivation and HTML-required validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
TOOL = SCRIPTS / "codemap_tool.py"
GENERATOR = SCRIPTS / "generate_repository_map.py"


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


class CodemapToolTests(unittest.TestCase):
    def test_markdown_derives_and_validate_requires_html(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "GIT_AUTHOR_NAME": "codemap-test",
                    "GIT_AUTHOR_EMAIL": "codemap-test@example.com",
                    "GIT_COMMITTER_NAME": "codemap-test",
                    "GIT_COMMITTER_EMAIL": "codemap-test@example.com",
                }
            )
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            subprocess.run(["git", "add", "src/app.py"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            subprocess.run(
                ["git", "-c", "user.name=codemap-test", "-c", "user.email=codemap-test@example.com", "commit", "-m", "seed"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            staging = repo / "docs" / "codemap" / ".staging"
            staging.mkdir(parents=True)
            generated_at = "2026-08-16T00:00:00Z"
            run(
                [
                    sys.executable,
                    "-B",
                    str(GENERATOR),
                    "--repo",
                    str(repo),
                    "--output",
                    "docs/codemap/.staging/codemap.json",
                    "--generated-at",
                    generated_at,
                ],
                repo,
            )
            run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL),
                    "markdown",
                    "--repo",
                    str(repo),
                    "--json",
                    "docs/codemap/.staging/codemap.json",
                    "--output",
                    "docs/codemap/.staging/codemap.md",
                ],
                repo,
            )
            run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL),
                    "lock",
                    "--repo",
                    str(repo),
                    "--scope",
                    ".",
                    "--generated-at",
                    generated_at,
                    "--output",
                    "docs/codemap/.staging/codemap.lock",
                ],
                repo,
            )
            run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL),
                    "render",
                    "--repo",
                    str(repo),
                    "--json",
                    "docs/codemap/.staging/codemap.json",
                    "--output",
                    "docs/codemap/.staging/codemap.html",
                ],
                repo,
            )
            validation = json.loads(
                run(
                    [
                        sys.executable,
                        "-B",
                        str(TOOL),
                        "validate",
                        "--repo",
                        str(repo),
                        "--dir",
                        "docs/codemap/.staging",
                    ],
                    repo,
                )
            )
            self.assertTrue(validation["ok"])
            self.assertTrue(validation["html"])
            self.assertTrue((staging / "codemap.html").exists())
            markdown = (staging / "codemap.md").read_text(encoding="utf-8")
            model = json.loads((staging / "codemap.json").read_text(encoding="utf-8"))
            self.assertIn("## Modules", markdown)
            self.assertIn("## Edges", markdown)
            for node in model["nodes"]:
                self.assertIn(f"`{node['id']}`", markdown)
                self.assertIn("callers", node)
                self.assertIn("callees", node)
                self.assertNotIn("Owns the tracked files", json.dumps(node))
            self.assertLessEqual(len(model["flows"]), 5)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
