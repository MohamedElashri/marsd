"""Reject developer-machine dylib paths and an overstated macOS deployment minimum."""

import argparse
import json
import re
import subprocess
from pathlib import Path


def version(value):
    parts = tuple(int(v) for v in value.split("."))
    return parts + (0,) * (3 - len(parts))


def check(runtime, minimum):
    files, broken = [], []
    for path in runtime.rglob("*"):
        if path.is_symlink():
            if not path.exists():
                broken.append(str(path.relative_to(runtime)))
            continue
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(4).hex() in ["cffaedfe", "feedfacf", "cafebabe", "bebafeca"]:
                    files.append(path)
    assert files, "No Mach-O runtime files were found"
    dependencies = subprocess.check_output(["otool", "-L", *files], text=True)
    bad = [
        line.strip()
        for line in dependencies.splitlines()
        if line.startswith("\t/") and not line.startswith(("\t/System/Library/", "\t/usr/lib/"))
    ]
    above, observed = [], {}
    for path in files:
        info = subprocess.check_output(["otool", "-l", path], text=True)
        for rpath in re.findall(r"\bpath (.+) \(offset", info):
            if rpath.startswith("/") and not rpath.startswith(("/System/Library/", "/usr/lib/")):
                bad.append(f"{path.relative_to(runtime)}: {rpath}")
        for value in re.findall(r"\bminos\s+([\d.]+)", info) + re.findall(
            r"\bversion\s+([\d.]+)\n\s+sdk", info
        ):
            observed[value] = observed.get(value, 0) + 1
            if version(value) > version(minimum):
                above.append({"file": str(path.relative_to(runtime)), "minimum": value})
    result = {
        "status": "FAIL" if bad or above or broken else "PASS",
        "macho_files": len(files),
        "declared_minimum": minimum,
        "observed_minimums": observed,
        "external_absolute_dependencies": bad,
        "above_minimum": above,
        "broken_symlinks": broken,
    }
    print(json.dumps(result))
    if result["status"] != "PASS":
        raise SystemExit(
            "Bundle is not relocatable at the declared macOS minimum; use a compatible CPython build and native wheels."
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--minimum", default="15.0")
    check(**vars(parser.parse_args()))
