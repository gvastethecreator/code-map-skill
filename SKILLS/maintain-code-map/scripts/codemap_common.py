#!/usr/bin/env python3
"""Shared helpers for code-map scripts. No host- or repository-specific paths."""

from __future__ import annotations

import hashlib
import html
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


ALGORITHM = "sha256-path-content-v2"

LANGUAGE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cjs",
    ".cpp",
    ".cs",
    ".csproj",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".svelte",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
    ".xaml",
}

TEXT_EXTENSIONS = LANGUAGE_EXTENSIONS | {
    ".css",
    ".html",
    ".json",
    ".md",
    ".scss",
    ".toml",
    ".webmanifest",
    ".yaml",
    ".yml",
}

MANIFEST_FILENAMES = {
    "cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}

BINARY_EXTENSIONS = {
    ".7z",
    ".a",
    ".ai",
    ".bin",
    ".bmp",
    ".bz2",
    ".class",
    ".dat",
    ".db",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".lib",
    ".mp3",
    ".mp4",
    ".mov",
    ".o",
    ".ogg",
    ".otf",
    ".pdb",
    ".pdf",
    ".png",
    ".psd",
    ".pyc",
    ".pyo",
    ".rar",
    ".so",
    ".sqlite",
    ".tar",
    ".tif",
    ".tiff",
    ".ttf",
    ".wasm",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}


class CodemapError(RuntimeError):
    pass


def is_skill_doc(relative: str) -> bool:
    return relative == "SKILL.md" or relative.endswith("/SKILL.md")


def is_analysis_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in LANGUAGE_EXTENSIONS:
        return True
    if name in MANIFEST_FILENAMES:
        return True
    return is_skill_doc(relative)


def select_analysis_files(tracked: list[str]) -> tuple[list[str], bool]:
    selected = [path for path in tracked if is_analysis_path(path)]
    if selected:
        return selected, False
    fallback = [path for path in tracked if PurePosixPath(path).suffix.lower() in TEXT_EXTENSIONS]
    return fallback, True


def is_binary_file(path: Path, relative: str) -> bool:
    suffix = PurePosixPath(relative).suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return True
    if suffix in TEXT_EXTENSIONS or suffix in LANGUAGE_EXTENSIONS:
        return False
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return False


def module_fingerprint(repo: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update(f"{ALGORITHM}\0".encode())
    for relative in sorted(files):
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        path = repo / Path(*PurePosixPath(relative).parts)
        if not path.is_file():
            digest.update(b"MISSING")
        elif is_binary_file(path, relative):
            digest.update(b"BIN")
            digest.update(str(path.stat().st_size).encode())
        else:
            content = path.read_bytes()
            digest.update(str(len(content)).encode())
            digest.update(b"\0")
            digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def default_template_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "codemap-template.html"


def render_html(data: dict[str, Any], repo_name: str, template: Path | None = None) -> str:
    template_path = Path(template) if template else default_template_path()
    if not template_path.is_file():
        raise CodemapError(f"template does not exist: {template_path}")
    compact = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    content = template_path.read_text(encoding="utf-8")
    if "__CODEMAP_DATA__" not in content or "__REPO_NAME__" not in content:
        raise CodemapError("template placeholders are missing")
    return content.replace("__CODEMAP_DATA__", compact).replace("__REPO_NAME__", html.escape(repo_name))


def load_sibling(filename: str, origin: Path | None = None) -> Any:
    source = (origin or Path(__file__)).resolve().with_name(filename)
    name = source.stem
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__file__", None) == str(source):
        return existing
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise CodemapError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
