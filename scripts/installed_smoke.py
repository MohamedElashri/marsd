"""Run with a clean installed-wheel Python from outside the repository."""

import argparse
import json
import tempfile
from pathlib import Path

import marsd
from marsd.desktop_diagnostics import exercise as diagnose


def exercise(root):
    report = diagnose(root)
    static = Path(marsd.__file__).parent / "static"
    assert (static / "index.html").is_file()
    print(
        json.dumps(
            {**report, "module": marsd.__file__, "offline_smoke": "PASS", "ui_assets": "present"}
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path)
    args = parser.parse_args()
    if args.project:
        exercise(args.project)
    else:
        with tempfile.TemporaryDirectory(prefix="marsd-wheel-") as directory:
            exercise(Path(directory) / "synthetic")


if __name__ == "__main__":
    main()
