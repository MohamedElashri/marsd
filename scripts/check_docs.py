"""Check local documentation links and keep the private plan out of Git."""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)|(?:src|href)="([^"]+)"')


def anchors(text: str) -> set[str]:
    result: set[str] = set()
    fence = ""
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1].startswith(fence):
                fence = ""
            continue
        if fence:
            continue
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            slug = re.sub(r"[^\w -]", "", heading[1].lower()).replace(" ", "-")
            anchor, suffix = slug, 0
            while anchor in result:
                suffix += 1
                anchor = f"{slug}-{suffix}"
            result.add(anchor)
        result.update(re.findall(r'(?:id|name)="([^"]+)"', line))
    return result


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
            elif (
                link.fragment
                and target.suffix == ".md"
                and unquote(link.fragment) not in anchors(target.read_text(encoding="utf-8"))
            ):
                errors.append(f"{document.relative_to(ROOT)}: missing heading {match.group(0)}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(
        f"Documentation checked: {len(documents)} Markdown files; local files and headings resolve."
    )


if __name__ == "__main__":
    check()
