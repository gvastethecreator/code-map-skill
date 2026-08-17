#!/usr/bin/env python3
"""Prove analysis policy, fingerprints, render_html, and in-process build."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import codemap_common
import generate_repository_map as generator
import codemap_tool as tool

TOOL = SCRIPTS / "codemap_tool.py"
GENERATOR = SCRIPTS / "generate_repository_map.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "codemap-test",
    "GIT_AUTHOR_EMAIL": "codemap-test@example.com",
    "GIT_COMMITTER_NAME": "codemap-test",
    "GIT_COMMITTER_EMAIL": "codemap-test@example.com",
}


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
        env={**os.environ, **GIT_ENV},
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def init_repo(root: Path, files: dict[str, str | bytes]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    subprocess.run(
        ["git", "-c", "user.name=codemap-test", "-c", "user.email=codemap-test@example.com", "commit", "-m", "seed"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return root


def generate_model(repo: Path) -> tuple[dict, dict]:
    return generator.generate(repo, "2026-08-16T00:00:00Z")


class CodemapToolTests(unittest.TestCase):
    def test_markdown_derives_and_validate_requires_html(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = init_repo(Path(raw), {"src/app.py": "def main():\n    return 1\n"})
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

    def test_markdown_docs_do_not_create_reads_edges(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = init_repo(
                Path(raw),
                {
                    "README.md": "See [app](src/app.py) and `src/other.py`.\n",
                    "src/app.py": "def main():\n    return 1\n",
                    "src/other.py": "from app import main\n",
                },
            )
            model, summary = generate_model(repo)
            self.assertFalse(summary["used_docs_fallback"])
            evidence = {location["path"] for edge in model["edges"] for location in edge["evidence"]["locations"]}
            self.assertNotIn("README.md", evidence)
            self.assertTrue(all(edge["type"] != "reads" or "README.md" not in json.dumps(edge) for edge in model["edges"]))

    def test_package_json_is_analyzed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = init_repo(
                Path(raw),
                {
                    "package.json": '{"name":"demo","main":"src/index.js"}\n',
                    "src/index.js": "export function boot() { return 1; }\n",
                },
            )
            model, _summary = generate_model(repo)
            evidence = json.dumps(model["edges"])
            self.assertIn("package.json", evidence)
            self.assertTrue(any(edge["type"] == "reads" for edge in model["edges"]))

    def test_skill_docs_still_link_named_skills(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = init_repo(
                Path(raw),
                {
                    "skills/alpha/SKILL.md": "---\nname: alpha\n---\n# Alpha\n",
                    "skills/beta/SKILL.md": "---\nname: beta\n---\nUses alpha for routing.\n",
                },
            )
            model, _summary = generate_model(repo)
            evidence = json.dumps(model)
            self.assertIn("alpha", evidence)
            self.assertTrue(any(edge["type"] == "reads" for edge in model["edges"]))

    def test_binary_fingerprint_uses_size_not_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            relative = "assets/blob.png"
            path = repo / relative
            path.parent.mkdir(parents=True)
            payload = b"\x89PNG\r\n\x1a\n" + b"\0" * 4096
            path.write_bytes(payload)
            first = codemap_common.module_fingerprint(repo, [relative])
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" * 4096)
            second = codemap_common.module_fingerprint(repo, [relative])
            self.assertEqual(first, second)
            path.write_bytes(payload + b"\0")
            third = codemap_common.module_fingerprint(repo, [relative])
            self.assertNotEqual(first, third)
            self.assertTrue(codemap_common.is_binary_file(path, relative))

    def test_source_fingerprint_reads_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            relative = "src/app.py"
            path = repo / relative
            path.parent.mkdir(parents=True)
            path.write_text("def main():\n    return 1\n", encoding="utf-8")
            first = codemap_common.module_fingerprint(repo, [relative])
            path.write_text("def main():\n    return 2\n", encoding="utf-8")
            second = codemap_common.module_fingerprint(repo, [relative])
            self.assertNotEqual(first, second)

    def test_binary_fingerprint_skips_full_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            relative = "assets/huge.bin"
            path = repo / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"\0" * (8 * 1024 * 1024))
            started = time.perf_counter()
            digest = codemap_common.module_fingerprint(repo, [relative])
            elapsed = time.perf_counter() - started
            self.assertTrue(digest)
            self.assertLess(elapsed, 0.5)

    def test_render_html_does_not_require_codemap_output_path(self) -> None:
        data = {
            "generated_at": "2026-08-16T00:00:00Z",
            "generated_from_commit": "0" * 40,
            "scope": ["."],
            "nodes": [],
            "edges": [],
            "flows": [],
        }
        rendered = tool.render_html(data, "example-repo")
        self.assertIn("example-repo", rendered)
        self.assertIn("codemap-data", rendered)
        with tempfile.TemporaryDirectory() as raw:
            outside = Path(raw) / "preview.html"
            outside.write_text(rendered, encoding="utf-8")
            self.assertTrue(outside.is_file())
            self.assertNotIn("docs/codemap", str(outside))

    def test_build_writes_json_md_html_lock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = init_repo(Path(raw), {"src/app.py": "def main():\n    return 1\n"})
            result = tool.build_and_publish(repo, generated_at="2026-08-16T00:00:00Z")
            self.assertTrue(result["ok"])
            root = repo / "docs" / "codemap"
            for name in ("codemap.json", "codemap.md", "codemap.html", "codemap.lock"):
                self.assertTrue((root / name).is_file(), name)
            lock = json.loads((root / "codemap.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["fingerprint_algorithm"], "sha256-path-content-v2")

    def test_docs_only_repo_falls_back_to_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = init_repo(Path(raw), {"README.md": "# Notes\n\nProject overview.\n"})
            _model, summary = generate_model(repo)
            self.assertTrue(summary["used_docs_fallback"])
            self.assertGreaterEqual(summary["nodes"], 1)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
