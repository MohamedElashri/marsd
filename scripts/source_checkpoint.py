"""Fingerprint source and configuration for platform verification reports."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def checkpoint():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, encoding="utf-8")
    files = [ROOT / name for name in names.split("\0") if name]
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
