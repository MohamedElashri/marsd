"""Check tracked and untracked authored Markdown without requiring a POSIX shell."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
}


def authored_files():
    pending = [ROOT]
    while pending:
        for path in pending.pop().iterdir():
            if path.is_symlink():
                continue
            if path.is_dir():
                if path.name not in EXCLUDED and path.relative_to(ROOT).as_posix() not in {
                    "desktop/runtime",
                    "desktop/build",
                    "desktop/src-tauri/target",
                }:
                    pending.append(path)
            elif path.suffix.lower() in {".md", ".markdown"}:
                yield path.relative_to(ROOT).as_posix()


if __name__ == "__main__":
    found = set(authored_files())
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if result.returncode == 0:
        found.update(
            name
            for name in result.stdout.decode().split("\0")
            if Path(name).suffix.lower() in {".md", ".markdown"}
        )
    forbidden = found - {"plan.md"}
    if forbidden:
        raise SystemExit("Forbidden project Markdown: " + ", ".join(sorted(forbidden)))
    print("Markdown policy passed: plan.md is the only authored Markdown file.")
