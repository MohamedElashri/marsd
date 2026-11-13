import hashlib
import json
import sqlite3
import time

import pytest
from typer.testing import CliRunner

from marsd.cli import app
from marsd.domain import MarsdError, Request
from marsd.registry import Registry
from marsd.service import Workbench


def wait(wb, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = wb.jobs.get(job_id)
        if value["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return value
        time.sleep(0.05)
    pytest.fail("worker did not reach a terminal state")


def test_worker_roundtrip_preserves_source_identity_and_reports(demo):
    path = demo / "data/run_042.h5"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with Workbench(demo) as wb:
        assert all(not a["stale"] for a in wb.registry.artifacts())
        first = wb.registry.register(path)
        metadata = wb.execute(Request(operation="inspect", path=str(path)))
        preview = wb.execute(
            Request(
                operation="preview",
                path=str(path),
                node="/measurement/transmission",
                selection="0:4,0:4",
            )
        )
        assert (
            metadata["inputs"][0]["version_id"]
            == first["version_id"]
            == preview["inputs"][0]["version_id"]
        )
        assert preview["coverage"]["visited"] == 16
        result = wb.execute(
            Request(
                operation="validate",
                path=str(demo / "data/run_042.parquet"),
                contract=str(demo / "contracts/measurement.yaml"),
            )
        )
        assert result["outcome"] == "FAIL"
        report_id = result["report_id"]
        exported = wb.export(report_id, format="html")
        assert "Marsd validate report" in exported.read_text()
        assert wb.registry.report(report_id)["contract"]["revision"] == "1"
        assert len(wb.registry.reports()) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with Workbench(demo) as reopened:
        assert reopened.registry.register(path)["version_id"] == first["version_id"]
        assert reopened.registry.report(report_id)["inputs"][0]["stale"] is False


def test_owner_lock_backup_integrity_and_recovery(demo):
    registry = Registry(demo)
    try:
        with pytest.raises(MarsdError, match="owns this project"):
            Registry(demo)
        registry.job_create("abandoned", "inspect", {"path": "unavailable"})
        registry.job_update("abandoned", "RUNNING")
        backup = registry.backup("snapshot.sqlite")
        with sqlite3.connect(backup) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 5
    finally:
        registry.close()
    with Workbench(demo) as wb:
        assert wb.registry.job("abandoned")["state"] == "INTERRUPTED"


def test_cancel_queued_and_running_worker_then_reuse(demo):
    with Workbench(demo) as wb:
        source = str(demo / "data/run_042.h5")
        first = wb.submit(Request(operation="inspect", path=source))
        second = wb.submit(Request(operation="inspect", path=source))
        wb.jobs.cancel(second)
        assert wait(wb, second)["state"] == "CANCELLED"
        start = time.monotonic()
        wb.jobs.cancel(first)
        assert wait(wb, first)["state"] == "CANCELLED"
        assert time.monotonic() - start < 5
        assert wb.execute(Request(operation="inspect", path=source))["format"] == "HDF5"


def test_worker_crash_does_not_commit_result_and_queue_recovers(demo):
    with Workbench(demo) as wb:
        source = str(demo / "data/run_042.h5")
        job = wb.submit(Request(operation="inspect", path=source))
        deadline = time.monotonic() + 5
        while wb.jobs.active is None and time.monotonic() < deadline:
            time.sleep(0.01)
        with wb.jobs.lock:
            assert wb.jobs.active is not None
            wb.jobs.active.process.terminate()
        value = wait(wb, job)
        assert value["state"] == "FAILED"
        assert value["result"] is None
        assert wb.execute(Request(operation="inspect", path=source))["format"] == "HDF5"


def test_mutation_invalidates_registered_version_and_exported_report(demo):
    path = demo / "data/identifiers.csv"
    with Workbench(demo) as wb:
        result = wb.execute(Request(operation="profile", path=str(path)))
        old = result["inputs"][0]["version_id"]
        path.write_text("sample_id,value\n0001,changed\n")
        assert next(a for a in wb.registry.artifacts() if a["path"] == str(path))["stale"]
        assert wb.registry.report(result["report_id"])["inputs"][0]["stale"]
        assert wb.registry.register(path)["version_id"] != old


def test_cli_json_and_scientific_exit_codes(demo):
    runner = CliRunner()
    common = ["--project", str(demo), "--json"]
    good = runner.invoke(
        app,
        [
            "validate",
            str(demo / "data/run_041.parquet"),
            "--contract",
            str(demo / "contracts/measurement.yaml"),
            *common,
        ],
    )
    assert good.exit_code == 0, good.output
    assert json.loads(good.stdout)["outcome"] == "PASS"
    bad = runner.invoke(
        app,
        [
            "validate",
            str(demo / "data/run_042.parquet"),
            "--contract",
            str(demo / "contracts/measurement.yaml"),
            *common,
        ],
    )
    assert bad.exit_code == 1, bad.output
    assert json.loads(bad.stdout)["outcome"] == "FAIL"
    partial = runner.invoke(
        app,
        [
            "validate",
            str(demo / "data/run_041.parquet"),
            "--contract",
            str(demo / "contracts/measurement.yaml"),
            "--max-rows",
            "1",
            *common,
        ],
    )
    assert partial.exit_code == 4, partial.output
    assert json.loads(partial.stdout)["outcome"] == "INCONCLUSIVE"


def slow_worker(connection, pending, cancel_event):
    time.sleep(10)


def test_timeout_is_terminal_and_worker_cleanup_is_bounded(demo, monkeypatch):
    import marsd.jobs

    with Workbench(demo) as wb:
        monkeypatch.setattr(marsd.jobs, "worker_entry", slow_worker)
        start = time.monotonic()
        job = wb.submit(Request(operation="inspect", path=str(demo / "data/run_042.h5"), timeout=1))
        value = wait(wb, job)
        assert value["state"] == "TIMED_OUT"
        assert time.monotonic() - start < 5
        assert wb.jobs.active is None


def test_contract_revisions_are_immutable_and_digest_ignores_audit_times(demo):
    with Workbench(demo) as wb:
        request = Request(
            operation="validate",
            path=str(demo / "data/run_041.parquet"),
            contract=str(demo / "contracts/measurement.yaml"),
        )
        first = wb.execute(request)
        second = wb.execute(request)
        assert first["result_digest"] == second["result_digest"]
        assert first["created_at"] != second["created_at"]
        with wb.registry.guard:
            assert wb.registry.db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 1
        assert "_contract_document" not in wb.registry.report(first["report_id"])


def test_cli_creation_registration_preview_diff_profile_and_exports(tmp_path):
    runner = CliRunner()
    root = tmp_path / "cli-demo"
    created = runner.invoke(app, ["fixtures", "generate", "--output", str(root)])
    assert created.exit_code == 0, created.output
    common = ["--project", str(root)]
    registered = runner.invoke(
        app, ["project", "register", str(root / "data/identifiers.csv"), *common, "--sha256"]
    )
    assert registered.exit_code == 0, registered.output
    assert json.loads(registered.stdout)["identity_strength"] == "sha256"
    preview = runner.invoke(app, ["preview", str(root / "data/identifiers.csv"), *common, "--json"])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["rows"][0]["sample_id"] == "0001"
    difference = runner.invoke(
        app,
        [
            "diff",
            str(root / "data/run_041.h5"),
            str(root / "data/run_042.h5"),
            *common,
            "--node",
            "/measurement/transmission",
            "--select",
            ":,:",
            "--json",
        ],
    )
    assert difference.exit_code == 1, difference.output
    report_id = json.loads(difference.stdout)["report_id"]
    for command in [
        ["version"],
        ["contract-schema"],
        ["project", "list", *common],
        ["reports", "list", *common],
        ["project", "backup", *common],
        ["reports", "export", report_id, *common, "--format", "html"],
        ["inspect", str(root / "data/run_042.h5"), *common, "--json"],
        ["profile", str(root / "data/identifiers.csv"), *common, "--json"],
    ]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (command, result.output)
    new_project = runner.invoke(
        app, ["project", "create", str(tmp_path / "own-project"), "--name", "Independent study"]
    )
    assert new_project.exit_code == 0, new_project.output


def test_changed_contract_content_requires_a_new_revision(demo):
    import yaml

    path = demo / "contracts/measurement.yaml"
    with Workbench(demo) as wb:
        request = Request(
            operation="validate", path=str(demo / "data/run_041.parquet"), contract=str(path)
        )
        wb.execute(request)
        document = yaml.safe_load(path.read_text())
        document["rules"][0]["field"] = "other_field"
        path.write_text(yaml.safe_dump(document))
        with pytest.raises(MarsdError, match="different content"):
            wb.submit(request)
        document["revision"] = "2"
        path.write_text(yaml.safe_dump(document))
        assert wb.execute(request)["outcome"] == "FAIL"


def test_source_changed_during_compute_is_invalidated(demo, monkeypatch):
    from marsd.adapters import Dataset
    from marsd.jobs import Pending, _work

    path = demo / "data/identifiers.csv"
    registry = Registry(demo)
    try:
        source = registry.register(path)
    finally:
        registry.close()
    original = Dataset.profile

    def mutating_profile(self, request):
        result = original(self, request)
        path.write_text("sample_id,value\n9999,changed\n")
        return result

    monkeypatch.setattr(Dataset, "profile", mutating_profile)
    pending = Pending(
        "mutation", Request(operation="profile", path=str(path)).model_dump(), [source], [str(demo)]
    )
    with pytest.raises(MarsdError, match="changed during"):
        _work(pending)


def test_v1_migration_backs_up_and_preserves_existing_registry(tmp_path):
    from importlib.resources import files

    from marsd.registry import create_project

    root = create_project(tmp_path / "legacy")
    database = root / ".marsd/registry.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            files("marsd").joinpath("migrations/001.sql").read_text() + "\nPRAGMA user_version=1;"
        )
        connection.execute(
            "INSERT INTO artifacts VALUES(?,?,?,?)",
            ("preserved-id", str(root / "source.csv"), "CSV", "2026-11-13T00:00:00+00:00"),
        )
    registry = Registry(root)
    try:
        assert registry.db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert registry.db.execute("SELECT id FROM artifacts").fetchone()[0] == "preserved-id"
        backups = list((root / ".marsd/backups").glob("*.sqlite"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        registry.close()


def test_newer_database_refused_without_downgrade(demo):
    with sqlite3.connect(demo / ".marsd/registry.sqlite") as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(MarsdError, match="newer"):
        Registry(demo)
    with sqlite3.connect(demo / ".marsd/registry.sqlite") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_cli_registry_reads_are_available_while_ui_owns_project(demo):
    runner = CliRunner()
    with Workbench(demo):
        listing = runner.invoke(app, ["project", "list", "--project", str(demo)])
        assert listing.exit_code == 0, listing.output
        assert len(json.loads(listing.stdout)) == 5
        reports = runner.invoke(app, ["reports", "list", "--project", str(demo)])
        assert reports.exit_code == 0, reports.output
        writing = runner.invoke(
            app, ["project", "register", str(demo / "data/run_042.h5"), "--project", str(demo)]
        )
        assert writing.exit_code == 3 and "PROJECT_BUSY" in writing.stderr


def test_owner_crash_terminates_owned_scan_worker(demo, tmp_path):
    import subprocess
    import sys

    import psutil

    source = demo / "data/slow-profile.csv"
    source.write_text("value\n" + "1\n" * 200000)
    script = tmp_path / "crash_owner.py"
    ready = tmp_path / "worker-ready"
    script.write_text(
        'import sys,time\nfrom pathlib import Path\nimport marsd.jobs as jobs\nfrom marsd.service import Workbench\nfrom marsd.domain import Request\n\ndef blocked_work(*args):\n    Path(sys.argv[3]).write_text("ready")\n    time.sleep(60)\n\ndef tracked_worker(connection,pending,event):\n    jobs._work = blocked_work\n    jobs.worker_entry(connection,pending,event)\n\nif __name__ == "__main__":\n    jobs.worker_entry = tracked_worker\n    with Workbench(sys.argv[1]) as wb:\n        wb.submit(Request(operation="profile",path=sys.argv[2],max_rows=1000000))\n        deadline=time.monotonic()+8\n        while wb.jobs.active is None or not Path(sys.argv[3]).exists():\n            if time.monotonic()>deadline:raise RuntimeError("worker did not start")\n            time.sleep(.01)\n        print(wb.jobs.active.process.pid,flush=True)\n        time.sleep(60)\n'
    )
    process = subprocess.Popen(
        [sys.executable, str(script), str(demo), str(source), str(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child = None
    try:
        line = process.stdout.readline().strip()
        assert line, process.stderr.read()
        child = psutil.Process(int(line))
        process.kill()
        process.wait(timeout=5)
        child.wait(timeout=5)
        with Workbench(demo) as wb:
            assert wb.registry.recent_jobs()[0]["state"] == "INTERRUPTED"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child is not None and child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
            child.kill()
        process.stdout.close()
        process.stderr.close()


def test_persistence_failure_stops_worker_and_returns_terminal_error(demo, monkeypatch):
    with Workbench(demo) as wb:
        original = wb.registry.job_update

        def unavailable(job_id, state, **kwargs):
            if state in {"RUNNING", "SUCCEEDED", "FAILED"}:
                raise sqlite3.OperationalError("simulated unavailable storage")
            return original(job_id, state, **kwargs)

        monkeypatch.setattr(wb.registry, "job_update", unavailable)
        with pytest.raises(MarsdError, match="could not be committed"):
            wb.execute(
                Request(
                    operation="preview",
                    path=str(demo / "data/run_042.h5"),
                    node="/measurement/transmission",
                )
            )
        assert wb.jobs.active is None
        assert wb.registry.reports() == []
