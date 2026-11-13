"""Collect locked native dependency declarations and available upstream notices."""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def collect(target):
    metadata = json.loads(
        subprocess.check_output(
            ["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", target],
            cwd=ROOT / "desktop/src-tauri",
        )
    )
    selected = {node["id"] for node in metadata["resolve"]["nodes"]}
    supplemental_path = ROOT / "desktop/licenses/supplemental.json"
    supplemental = json.loads(supplemental_path.read_text()) if supplemental_path.exists() else {}
    inventory, notices = [], []
    for package in sorted(metadata["packages"], key=lambda p: (p["name"], p["version"])):
        if package["id"] not in selected or package["source"] is None:
            continue
        base = Path(package["manifest_path"]).parent
        vcs_file = base / ".cargo_vcs_info.json"
        vcs = json.loads(vcs_file.read_text()) if vcs_file.exists() else {}
        paths = [
            p
            for p in base.rglob("*")
            if p.is_file()
            and p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "COPYRIGHT"))
        ]
        texts = []
        for path in sorted(paths):
            try:
                texts.append(f"{path.relative_to(base)}\n{path.read_text()}")
            except (UnicodeError, OSError):
                continue
        extra = supplemental.get(f"{package['name']}@{package['version']}", {})
        for notice in extra.get("notices", []):
            texts.append(f"{notice['url']}\n{notice['text']}")
        if extra.get("source_offer"):
            texts.append(f"Upstream source for this unmodified component: {extra['source_offer']}")
        inventory.append(
            {
                "name": package["name"],
                "version": package["version"],
                "license": package["license"],
                "notice_files": [str(p.relative_to(base)) for p in paths]
                + [n["url"] for n in extra.get("notices", [])],
                "source_repository": package["repository"] or extra.get("repository"),
                "source_revision": extra.get("revision") or vcs.get("git", {}).get("sha1"),
                "source_offer": extra.get("source_offer"),
            }
        )
        notices.append(
            f"{package['name']} {package['version']}\nDeclared license: {package['license']}\n"
            + "\n".join(texts)
        )
    output = ROOT / "desktop/runtime"
    output.mkdir(parents=True, exist_ok=True)
    (output / "native-license-inventory.json").write_text(
        json.dumps(
            {
                "target": target,
                "components": inventory,
                "scope": "Locked target graph including build dependencies. Declarations and available notices, not legal certification.",
            },
            indent=2,
        )
        + "\n"
    )
    (output / "native-third-party-licenses.txt").write_text("\n\n".join(notices))
    print(
        json.dumps(
            {
                "components": len(inventory),
                "missing_declarations": [p["name"] for p in inventory if not p["license"]],
                "missing_notice_files": [p["name"] for p in inventory if not p["notice_files"]],
            }
        )
    )

    if any(not item["license"] or not item["notice_files"] for item in inventory):
        raise SystemExit(
            "Native dependency notices are incomplete; add exact-revision upstream notices before packaging."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    collect(parser.parse_args().target)
