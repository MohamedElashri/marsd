"""Record a successful matrix cell from actual test reports; never invent native results."""

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from xml.etree import ElementTree

from check_wheel import current_wheel
from source_checkpoint import checkpoint


def record(junit: Path, browser: Path):
    root = ElementTree.parse(junit).getroot()
    cases = list(root.iter("testcase"))
    failures = len(list(root.iter("failure"))) + len(list(root.iter("error")))
    skipped = len(list(root.iter("skipped")))
    report = json.loads(browser.read_text())
    stats = report["stats"]
    good = (
        bool(cases)
        and not failures
        and stats.get("unexpected", 0) == 0
        and stats.get("expected", 0) > 0
    )
    if not good:
        raise SystemExit("Failed or missing tests cannot produce passing platform evidence")
    wheel = current_wheel()
    source = wheel.parent / wheel.name.replace("-py3-none-any.whl", ".tar.gz")
    hashes = {}
    for artifact in (wheel, source):
        with artifact.open("rb") as stream:
            hashes[artifact.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "schema_version": 1,
        "source_checkpoint": checkpoint(),
        "distribution_hashes": hashes,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python": ".".join(map(str, sys.version_info[:3])),
        "status": "PASS" if not skipped and not stats.get("skipped", 0) else "PASS_WITH_SKIPS",
        "unit_integration_security": {
            "executed": len(cases),
            "failures": failures,
            "skipped": skipped,
        },
        "browser": stats,
        "scope": "This matrix cell only. Release approval additionally requires all target cells, audit, recovery, package and performance evidence.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--junit", type=Path, required=True)
    p.add_argument("--browser", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--expected-python", help="Reject a mislabeled matrix interpreter, e.g. 3.13")
    a = p.parse_args()
    if a.expected_python and a.expected_python != ".".join(map(str, sys.version_info[:2])):
        raise SystemExit("Actual interpreter does not match the requested Python matrix cell")
    a.output.write_text(json.dumps(record(a.junit, a.browser), indent=2) + "\n")
