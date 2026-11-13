import hashlib
import json
import sqlite3
import uuid
from importlib.resources import files
from pathlib import Path, PureWindowsPath

import pytest
from typer.testing import CliRunner

from marsd.cli import app
from marsd.domain import MarsdError, Request, dumps
from marsd.maintenance import cleanup_temporaries, remap_root, restore
from marsd.registry import Registry
from marsd.service import Workbench


def hashes(directory):
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.iterdir()
        if p.is_file()
    }


def test_restore_rehearsal_preserves_sources_and_keeps_undo_snapshot(demo):
    with Workbench(demo) as wb:
        report = wb.execute(
            Request(
                operation="validate",
                path=str(demo / "data/run_041.parquet"),
                contract=str(demo / "contracts/measurement.yaml"),
            )
        )
    registry = Registry(demo)
    try:
        snapshot = registry.backup("known-good.sqlite")
        extra = demo / "data/new.csv"
        extra.write_text("id\n0001\n")
        registry.register(extra)
        before = hashes(demo / "data")
        preview = restore(registry, snapshot)
        assert not preview["applied"] and len(registry.artifacts()) == 6
        result = restore(registry, snapshot, apply=True)
        assert result["applied"] and len(registry.artifacts()) == 5
        assert registry.report(report["report_id"])["outcome"] == "PASS"
        assert hashes(demo / "data") == before
        recovery = result["recovery_snapshot"]
        with sqlite3.connect(recovery) as backup:
            assert backup.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 6
        restore(registry, recovery, apply=True)
        assert len(registry.artifacts()) == 6
    finally:
        registry.close()


def test_restore_rejects_other_project_and_invalid_database(demo, tmp_path):
    from marsd.fixtures import generate

    other = generate(tmp_path / "other")
    other_registry = Registry(other)
    try:
        snapshot = other_registry.backup("other.sqlite")
    finally:
        other_registry.close()
    registry = Registry(demo)
    try:
        with pytest.raises(MarsdError, match="different project"):
            restore(registry, snapshot, apply=True)
        broken = tmp_path / "broken.sqlite"
        broken.write_bytes(b"not a sqlite database")
        with pytest.raises(MarsdError, match="validated"):
            restore(registry, broken, apply=True)
        assert len(registry.artifacts()) == 5
        assert (
            not list((demo / ".marsd/backups").glob("pre-restore-*"))
            if (demo / ".marsd/backups").exists()
            else True
        )
    finally:
        registry.close()


def test_legacy_restore_requires_explicit_ownership_assertion(demo):
    legacy = demo / ".marsd/reports/legacy.sqlite"
    with sqlite3.connect(legacy) as connection:
        connection.executescript(
            files("marsd").joinpath("migrations/001.sql").read_text() + "\nPRAGMA user_version=1;"
        )
    registry = Registry(demo)
    try:
        with pytest.raises(MarsdError, match="ownership"):
            restore(registry, legacy)
        result = restore(registry, legacy, allow_legacy=True, apply=True)
        assert result["project_identity"] == "explicit_legacy_assertion"
        assert registry.db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert registry.artifacts() == []
        assert len(list((demo / "data").iterdir())) == 5
    finally:
        registry.close()


def test_project_relocation_and_reverse_mapping_preserve_identity(demo):
    with Workbench(demo) as wb:
        old = wb.registry.register(demo / "data/run_041.parquet")
        report = wb.execute(Request(operation="profile", path=old["path"]))
    original = demo
    current = demo.parent / "moved-study"
    demo.rename(current)
    before = hashes(current / "data")
    registry = Registry(current)
    try:
        assert all(a["stale"] for a in registry.artifacts())
        assert not remap_root(registry, str(original), str(current))["applied"]
        result = remap_root(registry, str(original), str(current), apply=True)
        assert result["artifact_count"] == 5
        new = registry.register(current / "data/run_041.parquet")
        assert new["id"] == old["id"] and new["version_id"] == old["version_id"]
        evidence = registry.report(report["report_id"])["inputs"][0]
        assert evidence["path"] == old["path"] and evidence["relocated"]
        assert evidence["current_path"] == str(current / "data/run_041.parquet")
    finally:
        registry.close()
    assert hashes(current / "data") == before
    current.rename(original)
    registry = Registry(original)
    try:
        remap_root(registry, str(current), str(original), apply=True)
        assert registry.evidence_path(old["path"]) == original / "data/run_041.parquet"
        assert registry.db.execute("SELECT COUNT(*) FROM maintenance_events").fetchone()[0] == 2
    finally:
        registry.close()


def test_foreign_windows_roots_are_never_resolved_against_posix_cwd(demo):
    registry = Registry(demo)
    foreign = PureWindowsPath("C:/synthetic-study")
    try:
        with registry.db:
            for artifact in registry.artifacts():
                relative = PureWindowsPath(*Path(artifact["path"]).relative_to(demo).parts)
                registry.db.execute(
                    "UPDATE artifacts SET uri=? WHERE id=?",
                    (str(foreign / relative), artifact["id"]),
                )
            registry.db.execute(
                "UPDATE project_settings SET value=? WHERE key=?",
                (dumps([str(foreign)]), "source_roots"),
            )
        registry.load_settings()
        assert all(a["stale"] for a in registry.artifacts())
        remap_root(registry, str(foreign), str(demo), apply=True)
        assert all(not a["stale"] for a in registry.artifacts())
    finally:
        registry.close()


def test_cleanup_is_limited_to_owned_temporaries(demo):
    registry = Registry(demo)
    try:
        path = demo / ".marsd/reports" / f".marsd-tmp-{uuid.uuid4().hex}.tmp"
        path.write_text("abandoned")
        keep = demo / ".marsd/reports/user-note.tmp"
        keep.write_text("keep")
        before = hashes(demo / "data")
        assert cleanup_temporaries(registry)["removed_temporaries"] == 1
        assert keep.read_text() == "keep"
        assert hashes(demo / "data") == before
    finally:
        registry.close()


def test_cli_maintenance_previews_apply_and_busy_errors(demo):
    runner = CliRunner()
    registry = Registry(demo)
    try:
        snapshot = registry.backup("cli-restore.sqlite")
        locked = runner.invoke(app, ["project", "restore", str(snapshot), "--project", str(demo)])
        assert locked.exit_code == 3 and "PROJECT_BUSY" in locked.stderr
    finally:
        registry.close()
    preview = runner.invoke(app, ["project", "restore", str(snapshot), "--project", str(demo)])
    assert preview.exit_code == 0 and not json.loads(preview.stdout)["applied"]
    applied = runner.invoke(
        app, ["project", "restore", str(snapshot), "--project", str(demo), "--apply"]
    )
    assert applied.exit_code == 0 and json.loads(applied.stdout)["applied"]
    assert runner.invoke(app, ["project", "cleanup", "--project", str(demo)]).exit_code == 0


def test_current_schema_never_silently_rebinds_missing_project_identity(demo):
    database = demo / ".marsd/registry.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM project_settings")
    with pytest.raises(MarsdError, match="different projects"):
        Registry(demo)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_settings").fetchone()[0] == 0
