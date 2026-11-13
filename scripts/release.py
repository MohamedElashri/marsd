"""Validate release tags and collect the exact artifacts verified in the same run."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path
from xml.etree import ElementTree

from prepare_desktop_python import CHECKSUMS, VERSION
from source_checkpoint import checkpoint

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "aarch64-apple-darwin": ("macos-aarch64.dmg", "*.dmg"),
    "x86_64-pc-windows-msvc": ("windows-x86_64-setup.exe", "*-setup.exe"),
    "x86_64-unknown-linux-gnu": ("linux-x86_64.deb", "*.deb"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def check_tag(tag, product_version):
    require(
        bool(re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag)),
        "Use a complete version tag such as v0.1.0",
    )
    require(tag == f"v{product_version}", "Tag does not match the configured product version")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def validate(tag, commit, ref_type, default_branch):
    check_tag(tag, version())
    require(ref_type == "tag", "Dispatch this workflow at a version tag, not a branch")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "Invalid triggering commit")
    commit = git("rev-parse", f"{commit}^{{commit}}")
    require(git("rev-parse", "HEAD") == commit, "Checkout does not match the triggering commit")
    require(
        git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}") == commit,
        "Tag moved or does not match this run",
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, f"refs/remotes/origin/{default_branch}"],
        cwd=ROOT,
        check=True,
    )
    print(f"Validated {tag} at {commit} on {default_branch}")
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"commit={commit}\n")


def read_json(path):
    require(path.stat().st_size <= 8 * 1024**2, f"Oversized evidence file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024**2):
            digest.update(block)
    return digest.hexdigest()


def one(directory, pattern):
    matches = [p for p in directory.rglob(pattern) if p.is_file() and not p.is_symlink()]
    require(
        len(matches) == 1, f"Expected exactly one {pattern} in {directory}; found {len(matches)}"
    )
    return matches[0]


def draft(output, tag, commit, repository):
    check_tag(tag, version())
    checksums = {}
    for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        require(
            bool(re.fullmatch(r"[a-zA-Z0-9_.-]+", name))
            and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
            and name not in checksums,
            "Invalid checksum manifest",
        )
        checksums[name] = digest
    require(
        set(checksums) == {p.name for p in output.iterdir()} - {"SHA256SUMS"},
        "Release asset set changed after verification",
    )
    require(
        all(sha256(output / name) == digest for name, digest in checksums.items()),
        "Release asset changed after verification",
    )
    manifest = read_json(output / f"marsd-{version()}-manifest.json")
    require(
        manifest["commit"] == commit and manifest["tag"] == tag,
        "Manifest does not match this release",
    )

    def api(path):
        return json.loads(
            subprocess.check_output(["gh", "api", f"repos/{repository}/{path}"], text=True)
        )

    reference = api(f"git/ref/tags/{tag}")["object"]
    for _ in range(8):
        if reference["type"] != "tag":
            break
        reference = api(f"git/tags/{reference['sha']}")["object"]
    require(
        reference["type"] == "commit" and reference["sha"] == commit,
        "Remote tag changed during the build",
    )
    releases = json.loads(
        subprocess.check_output(
            ["gh", "release", "list", "--repo", repository, "--limit", "1000", "--json", "tagName"],
            text=True,
        )
    )
    require(
        len(releases) < 1000,
        "Release inventory limit reached; review before creating another release",
    )
    require(
        not any(item["tagName"] == tag for item in releases),
        "A draft or published release already exists; it will not be overwritten",
    )
    subprocess.run(
        [
            "gh",
            "release",
            "create",
            tag,
            *[str(p) for p in sorted(output.iterdir())],
            "--repo",
            repository,
            "--verify-tag",
            "--draft",
            "--prerelease",
            "--title",
            f"Marsd {tag} (experimental)",
            "--notes-file",
            str(output / "RELEASE_NOTES.txt"),
        ],
        check=True,
    )


def check_junit(path):
    root = ElementTree.parse(path).getroot()
    require(bool(list(root.iter("testcase"))), f"No executed tests in {path}")
    require(
        not any(list(root.iter(kind)) for kind in ("failure", "error", "skipped")),
        f"Failed or skipped tests in {path}",
    )
    return len(list(root.iter("testcase")))


def collect_core(incoming, expected_checkpoint):
    reports = {}
    for runner, system, architecture in [
        ("macos-15", "Darwin", "arm64"),
        ("ubuntu-24.04", "Linux", "x86_64"),
        ("windows-2022", "Windows", "AMD64"),
    ]:
        for python in ("3.12", "3.13"):
            name = f"validation-{runner}-python-{python}"
            directory = incoming / name
            report = read_json(directory / "platform-evidence.json")
            require(report["status"] == "PASS", f"Unsuccessful matrix cell: {name}")
            require(report["source_checkpoint"] == expected_checkpoint, f"Source mismatch: {name}")
            require(
                report["platform"] == system and report["architecture"] == architecture,
                f"Wrong platform: {name}",
            )
            require(report["python"].startswith(python + "."), f"Wrong interpreter: {name}")
            correctness = check_junit(directory / "test-results.xml")
            require(
                report["unit_integration_security"]
                == {"executed": correctness, "failures": 0, "skipped": 0},
                f"Inconsistent test report: {name}",
            )
            browser = report["browser"]
            require(
                browser["expected"] > 0
                and all(browser.get(k, 0) == 0 for k in ("unexpected", "skipped", "flaky")),
                f"Unsuccessful browser evidence: {name}",
            )
            reports[name] = {
                **report,
                "performance_cases": check_junit(directory / "performance-results.xml"),
            }
    return reports


def assemble(incoming, output, tag, commit):
    product_version = version()
    check_tag(tag, product_version)
    require(
        git("rev-parse", "HEAD") == commit, "Release assembly checkout is not the tagged source"
    )
    source = checkpoint()
    core = collect_core(incoming, source)
    native_locking = tomllib.loads((ROOT / ".sanad.toml").read_text())["native"]["enabled"]
    actions = read_json(incoming / "sanad-report/report.json")
    require(actions["success"], "Sanad dependency review failed")
    require(
        native_locking and actions["command"] == "check" and actions["verification"] == "online",
        "Native locking requires an online Sanad check",
    )
    output.mkdir(parents=True, exist_ok=False)
    prefix = f"marsd-{product_version}"

    def copy(path, name):
        require(path.is_file() and not path.is_symlink(), f"Missing or unsafe asset: {path}")
        shutil.copyfile(path, output / name)

    primary = incoming / "validation-ubuntu-24.04-python-3.12"
    for name in (f"{prefix}-py3-none-any.whl", f"{prefix}.tar.gz"):
        require(
            sha256(primary / "dist" / name) == core[primary.name]["distribution_hashes"][name],
            f"Distribution changed after verification: {name}",
        )
        copy(primary / "dist" / name, name)
    with tarfile.open(output / f"{prefix}.tar.gz") as archive:
        require(
            not any(Path(name).name.lower() == "plan.md" for name in archive.getnames()),
            "Private plan found in source distribution",
        )

    runtimes, installs, dependencies = {}, {}, {}
    for target, (suffix, pattern) in TARGETS.items():
        directory = incoming / f"desktop-{target}"
        runtime = read_json(directory / "desktop-runtime-evidence.json")
        diagnostic = runtime["diagnostics"]
        require(
            runtime["status"] == diagnostic["status"] == "PASS" and diagnostic["frozen"],
            f"Unsuccessful frozen runtime: {target}",
        )
        require(
            diagnostic["version"] == product_version and diagnostic["python"] == VERSION,
            f"Wrong frozen runtime version: {target}",
        )
        for key in (
            "cancel_shutdown_reopen",
            "frozen_cli_failed_run_partial_output_and_timeout",
            "owner_death_with_open_control_pipe",
            "copied_runtime_removal_preserves_project",
        ):
            require(runtime[key] == "PASS", f"Missing runtime verification {key}: {target}")
        provenance = read_json(directory / "desktop/runtime/cpython-provenance.json")
        require(
            provenance["target"] == target
            and provenance["python"] == VERSION
            and provenance["sha256"] == CHECKSUMS[target],
            f"Wrong CPython provenance: {target}",
        )
        native = read_json(directory / "desktop/runtime/native-license-inventory.json")
        require(
            native["target"] == target
            and native["components"]
            and all(p["license"] and p["notice_files"] for p in native["components"]),
            f"Incomplete native notices: {target}",
        )
        package = one(directory, pattern)
        if target != "aarch64-apple-darwin":
            install = read_json(directory / "desktop-install-evidence.json")
            require(
                install["status"] == "PASS"
                and install["version"] == product_version
                and install["package_sha256"] == sha256(package),
                f"Installer evidence does not match package: {target}",
            )
            for key in (
                "installed_native_webview",
                "manual_same_version_replacement_preserves_project",
                "inspect_preview_diff_validate_run_readiness_lineage",
                "close_reopen",
                "source_preservation",
                "native_uninstall_preserves_project",
            ):
                require(install[key] == "PASS", f"Missing installer verification {key}: {target}")
            installs[target] = install
        else:
            install = read_json(directory / "desktop-package-evidence.json")
            require(
                install["status"] == "PASS" and install["package_sha256"] == sha256(package),
                "Disk-image evidence does not match package",
            )
            require(
                install["diagnostics"]["version"] == product_version
                and install["diagnostics"]["frozen"],
                "Wrong disk-image runtime",
            )
            installs[target] = install
        copy(package, f"{prefix}-{suffix}")
        copy(
            directory / "desktop/runtime/native-third-party-licenses.txt",
            f"{prefix}-{target}-NOTICES.txt",
        )
        runtimes[target] = {"runtime": runtime, "cpython": provenance}
        dependencies[target] = native

    evidence = {
        "core": core,
        "desktop": runtimes,
        "installed_desktop": installs,
        "actions": actions,
    }
    inventory = {
        "python_and_npm": read_json(primary / "dependency-inventory.json"),
        "rust": dependencies,
        "scope": "Declared package/target inventories, including development/build dependencies. Not a complete native-library SBOM or legal certification. Bundled upstream license texts remain inside installers.",
    }
    for suffix, data in (("verification.json", evidence), ("dependencies.json", inventory)):
        (output / f"{prefix}-{suffix}").write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
    (output / "RELEASE_NOTES.txt").write_text(
        f"Marsd {product_version} — experimental\n\n"
        "Local HDF5/Parquet/CSV investigation, contracts, trusted Python run evidence and bounded lineage.\n\n"
        "Desktop installers are unsigned/ad-hoc experimental builds. Signing/notarization, minimum-OS/offline prerequisite and native-dialog qualification remain open. Windows Server/X11 runner checks do not certify Windows 11, Wayland or every user machine.\n\n"
        "Installers bundle Python; the Python wheel requires Python 3.12 or 3.13. No PyPI publication or automatic updater is included. See README and docs/usage.md at this tag.\n\n"
        "Actions dependency protection: online-verified native locking.\n\n"
        "SHA256SUMS covers every attached payload except itself. Review the manifest, dependency inventory and verification report before publishing.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "version": product_version,
        "tag": tag,
        "commit": commit,
        "source_checkpoint": source,
        "workflow_run": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'MohamedElashri/marsd')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', 'local')}",
        "distribution": "experimental; unsigned/ad-hoc desktop builds",
        "build_attestations_requested": os.environ.get("MARSD_BUILD_ATTESTATIONS") == "true",
        "native_actions_locking": native_locking,
        "artifacts": {
            p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
            for p in sorted(output.iterdir())
        },
    }
    (output / f"{prefix}-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output / "SHA256SUMS").write_text(
        "".join(f"{sha256(p)}  {p.name}\n" for p in sorted(output.iterdir())), encoding="utf-8"
    )
    print(f"Prepared {len(list(output.iterdir()))} verified release assets in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    collect = commands.add_parser("assemble")
    collect.add_argument("--incoming", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser("draft")
    publish.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    tag, commit = os.environ["RELEASE_TAG"], os.environ["RELEASE_COMMIT"]
    if options.command == "validate":
        validate(tag, commit, os.environ["RELEASE_REF_TYPE"], os.environ["DEFAULT_BRANCH"])
    elif options.command == "assemble":
        assemble(options.incoming, options.output, tag, commit)
    else:
        draft(options.output, tag, commit, os.environ["GITHUB_REPOSITORY"])
