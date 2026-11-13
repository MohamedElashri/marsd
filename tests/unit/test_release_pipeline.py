"""Release assembly must reject stale evidence and altered native packages."""

import hashlib
import importlib
import json
import subprocess
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("release")


@pytest.fixture
def artifacts(tmp_path, monkeypatch, pipeline):
    root = tmp_path / "source"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (root / ".sanad.toml").write_text("[native]\nenabled = true\n")
    monkeypatch.setattr(pipeline, "ROOT", root)
    source = {"sha256": "a" * 64, "files": 1}
    monkeypatch.setattr(pipeline, "checkpoint", lambda: source)
    monkeypatch.setattr(pipeline, "git", lambda *args: "b" * 40)
    incoming = tmp_path / "incoming"

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    for runner, system, architecture in (
        ("macos-15", "Darwin", "arm64"),
        ("ubuntu-24.04", "Linux", "x86_64"),
        ("windows-2022", "Windows", "AMD64"),
    ):
        for python in ("3.12", "3.13"):
            directory = incoming / f"validation-{runner}-python-{python}"
            write(
                directory / "platform-evidence.json",
                {
                    "status": "PASS",
                    "source_checkpoint": source,
                    "platform": system,
                    "architecture": architecture,
                    "python": python + ".0",
                    "unit_integration_security": {"executed": 1, "failures": 0, "skipped": 0},
                    "browser": {"expected": 1, "unexpected": 0, "skipped": 0, "flaky": 0},
                },
            )
            for name in ("test-results.xml", "performance-results.xml"):
                (directory / name).write_text('<testsuite><testcase name="verified" /></testsuite>')
    dist = incoming / "validation-ubuntu-24.04-python-3.12/dist"
    dist.mkdir()
    (dist / "marsd-0.1.0-py3-none-any.whl").write_bytes(b"tested wheel")
    with tarfile.open(dist / "marsd-0.1.0.tar.gz", "w:gz") as archive:
        archive.add(Path(__file__), arcname="marsd-0.1.0/tests/test_release_pipeline.py")
    primary_report = dist.parent / "platform-evidence.json"
    primary = json.loads(primary_report.read_text())
    primary["distribution_hashes"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dist.iterdir()
    }
    write(primary_report, primary)
    native_directories = {}
    for target, (_, pattern) in pipeline.TARGETS.items():
        directory = incoming / f"desktop-{target}"
        native_directories[target] = directory
        write(
            directory / "desktop-runtime-evidence.json",
            {
                "status": "PASS",
                "diagnostics": {
                    "status": "PASS",
                    "frozen": True,
                    "version": "0.1.0",
                    "python": pipeline.VERSION,
                },
                "cancel_shutdown_reopen": "PASS",
                "frozen_cli_failed_run_partial_output_and_timeout": "PASS",
                "owner_death_with_open_control_pipe": "PASS",
                "copied_runtime_removal_preserves_project": "PASS",
            },
        )
        write(
            directory / "desktop/runtime/cpython-provenance.json",
            {"target": target, "python": pipeline.VERSION, "sha256": pipeline.CHECKSUMS[target]},
        )
        write(
            directory / "desktop/runtime/native-license-inventory.json",
            {
                "target": target,
                "components": [
                    {"name": "dependency", "license": "MIT", "notice_files": ["LICENSE"]}
                ],
            },
        )
        (directory / "desktop/runtime/native-third-party-licenses.txt").write_text(
            "Upstream notices"
        )
        package = directory / pattern.replace("*", "Marsd_0.1.0")
        package.write_bytes(b"tested native package")
        if "darwin" not in target:
            write(
                directory / "desktop-install-evidence.json",
                {
                    "status": "PASS",
                    "version": "0.1.0",
                    "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                    "installed_native_webview": "PASS",
                    "manual_same_version_replacement_preserves_project": "PASS",
                    "inspect_preview_diff_validate_run_readiness_lineage": "PASS",
                    "close_reopen": "PASS",
                    "source_preservation": "PASS",
                    "native_uninstall_preserves_project": "PASS",
                },
            )
        else:
            write(
                directory / "desktop-package-evidence.json",
                {
                    "status": "PASS",
                    "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                    "diagnostics": {"version": "0.1.0", "frozen": True},
                },
            )
    write(
        incoming / "validation-ubuntu-24.04-python-3.12/dependency-inventory.json",
        {"python": [], "npm": []},
    )
    write(
        incoming / "sanad-report/report.json",
        {"success": True, "command": "check", "verification": "online"},
    )
    return incoming, tmp_path / "output", native_directories


def test_release_checksums_cover_the_complete_output(pipeline, artifacts):
    incoming, output, _ = artifacts
    pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)
    lines = (output / "SHA256SUMS").read_text().splitlines()
    checksums = dict(line.split("  ", 1)[::-1] for line in lines)
    assert set(checksums) == {p.name for p in output.iterdir()} - {"SHA256SUMS"}
    for name, digest in checksums.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    manifest = json.loads((output / "marsd-0.1.0-manifest.json").read_text())
    assert manifest["commit"] == "b" * 40
    assert manifest["native_actions_locking"] is True


def test_release_rejects_wrong_source_revision(pipeline, artifacts):
    incoming, output, _ = artifacts
    report = incoming / "validation-windows-2022-python-3.13/platform-evidence.json"
    value = json.loads(report.read_text())
    value["source_checkpoint"]["sha256"] = "c" * 64
    report.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Source mismatch"):
        pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)


def test_release_rejects_modified_installer(pipeline, artifacts):
    incoming, output, native = artifacts
    next(native["x86_64-pc-windows-msvc"].glob("*.exe")).write_bytes(b"altered installer")
    with pytest.raises(ValueError, match="Installer evidence does not match"):
        pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)


def test_draft_rechecks_remote_tag_before_upload(pipeline, artifacts, monkeypatch):
    incoming, output, _ = artifacts
    pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)
    monkeypatch.setattr(
        pipeline.subprocess,
        "check_output",
        lambda *args, **kwargs: json.dumps({"object": {"type": "commit", "sha": "c" * 40}}),
    )
    monkeypatch.setattr(
        pipeline.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected release upload")
    )
    with pytest.raises(ValueError, match="Remote tag changed"):
        pipeline.draft(output, "v0.1.0", "b" * 40, "owner/repository")


def test_release_upload_is_always_an_experimental_draft(pipeline, artifacts, monkeypatch):
    incoming, output, _ = artifacts
    pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)

    def api(args, **kwargs):
        if "list" in args:
            return "[]"
        return json.dumps({"object": {"type": "commit", "sha": "b" * 40}})

    monkeypatch.setattr(pipeline.subprocess, "check_output", api)
    calls = []
    monkeypatch.setattr(pipeline.subprocess, "run", lambda args, **kwargs: calls.append(args))
    pipeline.draft(output, "v0.1.0", "b" * 40, "owner/repository")
    assert len(calls) == 1
    assert all(flag in calls[0] for flag in ("--draft", "--prerelease", "--verify-tag"))
    assert "--clobber" not in calls[0]


def test_release_refuses_to_overwrite_existing_draft(pipeline, artifacts, monkeypatch):
    incoming, output, _ = artifacts
    pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)
    monkeypatch.setattr(
        pipeline.subprocess,
        "check_output",
        lambda args, **kwargs: (
            json.dumps([{"tagName": "v0.1.0"}])
            if "list" in args
            else json.dumps({"object": {"type": "commit", "sha": "b" * 40}})
        ),
    )
    monkeypatch.setattr(
        pipeline.subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected release upload")
    )
    with pytest.raises(ValueError, match="already exists"):
        pipeline.draft(output, "v0.1.0", "b" * 40, "owner/repository")


def test_draft_rejects_assets_changed_after_assembly(pipeline, artifacts):
    incoming, output, _ = artifacts
    pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)
    (output / "RELEASE_NOTES.txt").write_text("Changed after verification")
    with pytest.raises(ValueError, match="Release asset changed"):
        pipeline.draft(output, "v0.1.0", "b" * 40, "owner/repository")


def test_release_requires_all_three_desktop_targets(pipeline, artifacts):
    incoming, output, native = artifacts
    (native["aarch64-apple-darwin"] / "desktop-runtime-evidence.json").unlink()
    with pytest.raises(FileNotFoundError):
        pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)


def test_release_rejects_wheel_changed_after_testing(pipeline, artifacts):
    incoming, output, _ = artifacts
    (
        incoming / "validation-ubuntu-24.04-python-3.12/dist/marsd-0.1.0-py3-none-any.whl"
    ).write_bytes(b"changed wheel")
    with pytest.raises(ValueError, match="Distribution changed after verification"):
        pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)


def test_release_refuses_skipped_checks(pipeline, artifacts):
    incoming, output, _ = artifacts
    (incoming / "validation-ubuntu-24.04-python-3.12/performance-results.xml").write_text(
        "<testsuite><testcase><skipped /></testcase></testsuite>"
    )
    with pytest.raises(ValueError, match="Failed or skipped"):
        pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)


def test_native_locking_requires_online_verification(pipeline, artifacts):
    incoming, output, _ = artifacts
    (incoming / "sanad-report/report.json").write_text(
        json.dumps({"success": True, "command": "status", "verification": "none"})
    )
    with pytest.raises(ValueError, match="online Sanad check"):
        pipeline.assemble(incoming, output, "v0.1.0", "b" * 40)


def test_release_refuses_branch_dispatch(pipeline, monkeypatch):
    monkeypatch.setattr(pipeline, "version", lambda: "0.1.0")
    with pytest.raises(ValueError, match="not a branch"):
        pipeline.validate("v0.1.0", "b" * 40, "branch", "main")


@pytest.fixture
def tagged_repository(tmp_path, monkeypatch, pipeline):
    root = tmp_path / "repository"
    root.mkdir()
    monkeypatch.setattr(pipeline, "ROOT", root)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))

    def run(*args):
        return subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Release test",
                "-c",
                "user.email=release@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "tag.gpgsign=false",
                "-c",
                f"core.hooksPath={tmp_path / 'no-hooks'}",
                *args,
            ],
            text=True,
        ).strip()

    run("init", "--quiet", "--initial-branch=main")
    (root / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    run("add", "pyproject.toml")
    run("commit", "--quiet", "-m", "Source")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    run("tag", "-a", "v0.1.0", "-m", "Version 0.1.0")
    return run, tmp_path / "outputs"


@pytest.mark.parametrize("reference", ["HEAD", "refs/tags/v0.1.0"])
def test_release_accepts_matching_commit_or_annotated_tag(pipeline, tagged_repository, reference):
    run, output = tagged_repository
    commit = run("rev-parse", "HEAD")
    pipeline.validate("v0.1.0", run("rev-parse", reference), "tag", "main")
    assert output.read_text() == f"commit={commit}\n"


def test_release_rejects_tag_not_on_default_branch(pipeline, tagged_repository):
    run, _ = tagged_repository
    run("switch", "--quiet", "-c", "unmerged")
    run("commit", "--quiet", "--allow-empty", "-m", "Unmerged source")
    run("tag", "-fa", "v0.1.0", "-m", "Unmerged tag")
    with pytest.raises(subprocess.CalledProcessError):
        pipeline.validate("v0.1.0", run("rev-parse", "HEAD"), "tag", "main")


@pytest.mark.parametrize("tag", ["main", "v0.2.0", "v0.1.0;echo unsafe", "v0.1", "v0.1.0-rc.1"])
def test_release_refuses_nonmatching_tags(pipeline, tag):
    with pytest.raises(ValueError):
        pipeline.check_tag(tag, "0.1.0")
