"""Verify one pinned portable interpreter per native desktop target before freezing."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from build_desktop import ROOT, TARGETS

VERSION = "3.12.16"
RELEASE = "20261110"
CHECKSUMS = {
    "aarch64-apple-darwin": "2f11b8aa3726bdbd87dd9eb76568412faddf4f47e26913fa45d6a2e424972acd",
    "x86_64-pc-windows-msvc": "355ee61b8207b91574491b1c6dc9b0ebb4408fc56bfafe498f3ef439314b52dc",
    "x86_64-unknown-linux-gnu": "6a9e62dad033790aef722dbd370e0ce76ce17b73627ba3ae679f537049350b39",
}


def prepare(directory):
    target = TARGETS[(platform.system(), platform.machine().lower())]
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/cpython-{VERSION}%2B{RELEASE}-{target}-install_only_stripped.tar.gz"
    directory.mkdir(parents=True, exist_ok=False)
    archive = directory / "cpython.tar.gz"
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as source, archive.open("wb") as output:
        while block := source.read(1024 * 1024):
            output.write(block)
            digest.update(block)
    if digest.hexdigest() != CHECKSUMS[target]:
        raise SystemExit("Portable CPython checksum mismatch; archive will not be executed.")
    with tarfile.open(archive) as source:
        source.extractall(directory, filter="data")
    archive.unlink()
    executable = directory / "python" / ("python.exe" if os.name == "nt" else "bin/python3.12")
    actual = subprocess.check_output(
        [executable, "-I", "-c", "import platform; print(platform.python_version())"], text=True
    ).strip()
    assert actual == VERSION, actual
    manifest = {
        "python": VERSION,
        "build": RELEASE,
        "target": target,
        "url": url,
        "sha256": CHECKSUMS[target],
    }
    resources = ROOT / "desktop/runtime"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "cpython-provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"interpreter={executable}\n")
    print(json.dumps({**manifest, "interpreter": str(executable)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    prepare(parser.parse_args().directory.resolve())
