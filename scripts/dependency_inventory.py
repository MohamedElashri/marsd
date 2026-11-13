"""Record installed Python and locked npm license declarations for release review."""

import argparse
import hashlib
import json
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory():
    python = []
    for distribution in distributions():
        metadata = distribution.metadata
        notices = []
        for entry in distribution.files or []:
            if ".dist-info/licenses/" in str(entry) or entry.name.upper() in {
                "LICENSE",
                "LICENSE.TXT",
                "COPYING",
                "NOTICE",
            }:
                path = distribution.locate_file(entry)
                if path.is_file():
                    notices.append(
                        {
                            "file": str(entry),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                    )
        declarations = [
            v.removeprefix("License :: ")
            for v in metadata.get_all("Classifier", [])
            if v.startswith("License :: ")
        ]
        expression = (
            metadata.get("License-Expression") or metadata.get("License") or "; ".join(declarations)
        )
        python.append(
            {
                "name": metadata["Name"],
                "version": distribution.version,
                "license_declaration": expression.splitlines()[0][:256] if expression else None,
                "license_classifiers": declarations,
                "notices": notices,
            }
        )
    lock = json.loads((ROOT / "web/package-lock.json").read_text())
    npm = [
        {
            "name": name.split("node_modules/")[-1],
            "version": data["version"],
            "license_declaration": data.get("license"),
            "development": data.get("dev", False),
        }
        for name, data in lock["packages"].items()
        if name and "version" in data
    ]
    return {
        "schema_version": 1,
        "python": sorted(python, key=lambda x: x["name"].lower()),
        "npm": sorted(npm, key=lambda x: x["name"]),
        "interpretation": "Declarations and notice locations, not legal certification. Native wheelhouses retain upstream license files. The app bundles React/ReactDOM/scheduler/Tauri API notices. Frozen Python/CPython notices and the native target inventory are collected by the desktop build.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "python_components": len(result["python"]),
                "npm_components": len(result["npm"]),
                "missing_python_declarations": [
                    p["name"] for p in result["python"] if not p["license_declaration"]
                ],
                "missing_npm_declarations": [
                    p["name"] for p in result["npm"] if not p["license_declaration"]
                ],
            }
        )
    )
