"""Check local documentation links and keep the private plan out of Git."""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)|(?:src|href)="([^"]+)"')


def check() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", "plan.md"],
        cwd=ROOT,
        capture_output=True,
    )
    if tracked.returncode == 0:
        raise SystemExit("The local plan must remain untracked.")
    documents = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    errors = []
    for document in documents:
        for match in LINK.finditer(document.read_text(encoding="utf-8")):
            link = urlsplit(next(value for value in match.groups() if value))
            if link.scheme or link.netloc:
                continue
            target = (document.parent / unquote(link.path)).resolve() if link.path else document
            if not target.is_relative_to(ROOT) or not target.exists() or target == ROOT / "plan.md":
                errors.append(f"{document.relative_to(ROOT)}: invalid local link {match.group(0)}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Documentation checked: {len(documents)} Markdown files; local links resolve.")


if __name__ == "__main__":
    check()
