"""Fingerprint source and configuration for platform verification reports."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {
    "node_modules",
    "target",
    "__pycache__",
    "test-results",
    "playwright-report",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def checkpoint():
    files = []
    for directory in [".github", "src", "scripts", "tests", "web", "desktop", "docs"]:
        for path in (ROOT / directory).rglob("*"):
            if (
                path.is_file()
                and not path.is_relative_to(ROOT / "desktop/runtime")
                and not path.is_relative_to(ROOT / "desktop/build")
                and not path.is_relative_to(ROOT / "desktop/src-tauri/gen")
                and not any(part in SKIP for part in path.relative_to(ROOT).parts)
            ):
                files.append(path)
    files.extend(
        ROOT / name
        for name in [
            ".gitignore",
            ".gitattributes",
            ".python-version",
            "LICENSE",
            "README.md",
            "pyproject.toml",
            "uv.lock",
        ]
    )
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(ROOT).as_posix()):
        digest.update(
            path.relative_to(ROOT).as_posix().encode()
            + b"\0"
            + hashlib.sha256(path.read_bytes()).digest()
        )
    return {"sha256": digest.hexdigest(), "files": len(files)}


if __name__ == "__main__":
    print(json.dumps(checkpoint(), sort_keys=True))
