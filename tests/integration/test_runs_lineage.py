import hashlib
import json
import subprocess
import sys
import threading
import time

import psutil
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from marsd.api import create_app
from marsd.cli import app
from marsd.domain import MarsdError, Request
from marsd.lineage import Declaration, Graph
from marsd.registry import ReadOnlyRegistry, Registry, create_project
from marsd.runs import Recorder, RunSpec, RunStore, artifact_version, record
from marsd.sdk import capture, input_file, instrument, output_file
from marsd.service import Workbench


@pytest.fixture
def study(tmp_path):
    root = create_project(tmp_path / "study")
    (root / "input.csv").write_text("id,value\n0001,42\n")
    (root / "pipeline.py").write_text("pass\n")
    return root


def script(study, text):
    path = study / "pipeline.py"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_recorded_pipeline_has_real_shared_versions_and_labeled_edges(study, monkeypatch):
    monkeypatch.setenv("DO_NOT_RECORD_THIS_SECRET", "private-unrelated-value")
    code = script(
        study,
        """from pathlib import Path
from marsd.sdk import instrument,input_file,output_file
p=Path(__file__).parent
@instrument
def transform():
 with input_file(p/'input.csv') as f: data=f.read()
 with output_file(p/'output.csv') as f: f.write(data)
transform()
""",
    )
    registry = Registry(study)
    try:
        result = record(
            registry,
            RunSpec(
                script=code,
                inputs=[str(study / "input.csv")],
                outputs=[str(study / "output.csv")],
                strong_inputs=True,
            ),
        )
        assert result["state"] == "SUCCEEDED" and len(result["events"]) == 4
        assert "private-unrelated-value" not in json.dumps(result)
        incoming = [r for r in result["references"] if r["role"] == "input"]
        assert {r["evidence"] for r in incoming} == {"declared", "observed"}
        vid = incoming[0]["version"]["version_id"]
        assert incoming[1]["version"]["version_id"] == vid
        assert registry.register(study / "input.csv", strong=True)["version_id"] == vid
        assert artifact_version(registry, vid)["version_id"] == vid
        assert RunStore(registry).list(version_id=vid)[0]["id"] == result["id"]
        graph = Graph(registry).query(vid, depth=4)
        assert {e["relation"] for e in graph["edges"]} == {"used", "generated"}
        assert RunStore(registry).readiness(result["id"])["status"] == "READY_WITH_LIMITATIONS"
        assert not RunStore(registry).get(result["id"])["capture"]
    finally:
        registry.close()
    with Workbench(study) as wb:
        preview = wb.execute(Request(operation="preview", path=str(study / "input.csv")))
        # Same stat observation remains available alongside an explicit strong observation.
        assert preview["inputs"][0]["id"] == incoming[0]["version"]["id"]
        assert preview["inputs"][0]["version_id"] in {
            a["version_id"] for a in wb.registry.artifacts()
        }


def test_failure_keeps_partial_outputs_without_exception_text(study):
    code = script(
        study,
        "from pathlib import Path\np=Path(__file__).parent\n(p/'partial.csv').write_text('id\\n0001\\n')\nraise RuntimeError('private exception payload')\n",
    )
    registry = Registry(study)
    try:
        result = record(registry, RunSpec(script=code, outputs=[str(study / "partial.csv")]))
        assert result["state"] == "FAILED" and result["exit_code"] != 0
        assert result["references"][0]["partial"]
        assert result["references"][0]["evidence"] == "declared"
        assert "private exception payload" not in json.dumps(result)
    finally:
        registry.close()


def test_default_argument_and_text_omission_and_opt_in_redaction(study):
    code = script(study, "print('token=private-value')\nprint('x'*100000)\n")
    registry = Registry(study)
    try:
        hidden = record(registry, RunSpec(script=code, arguments=["--password", "private-value"]))
        assert hidden["arguments"] is None and hidden["capture"] is None
        visible = record(
            registry,
            RunSpec(
                script=code,
                arguments=["--password", "private-value"],
                capture_text=True,
                capture_arguments=True,
            ),
        )
        assert visible["arguments"] == ["--password", "[REDACTED]"]
        assert "private-value" not in json.dumps(visible)
        assert visible["capture"]["stdout"]["truncated"]
        assert len(visible["capture"]["stdout"]["excerpt"]) <= 65536 + 32
    finally:
        registry.close()


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_cancel_timeout_reap_descendant_processes(study, mode):
    code = script(
        study,
        "import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath(__file__).with_name('child.pid').write_text(str(p.pid))\ntime.sleep(60)\n",
    )
    cancel = threading.Event()

    def wait_and_cancel():
        deadline = time.monotonic() + 5
        while not (study / "child.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancel.set()

    thread = threading.Thread(target=wait_and_cancel)
    if mode == "cancel":
        thread.start()
    registry = Registry(study)
    try:
        result = record(
            registry, RunSpec(script=code, timeout=1 if mode == "timeout" else 10), cancel=cancel
        )
        assert result["state"] == ("CANCELLED" if mode == "cancel" else "TIMED_OUT")
        pid = int((study / "child.pid").read_text())
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        assert result["cleanup_seconds"] < 5
    finally:
        registry.close()
        if mode == "cancel":
            thread.join(timeout=1)


def test_readiness_has_no_target_process_import_install_or_write(study, monkeypatch):
    registry = Registry(study)
    source = study / "input.csv"
    try:
        result = record(registry, RunSpec(script=str(study / "pipeline.py"), inputs=[str(source)]))
        marker = study / "executed.marker"
        script(study, f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")

        def forbidden(*args, **kwargs):
            raise AssertionError("readiness must not spawn")

        monkeypatch.setattr(subprocess, "Popen", forbidden)
        before = hashlib.sha256((registry.home / "registry.sqlite").read_bytes()).hexdigest()
        ready = RunStore(registry).readiness(result["id"])
        assert ready["status"] == "NOT_READY" and not ready["executed"] and not marker.exists()
        assert (
            before == hashlib.sha256((registry.home / "registry.sqlite").read_bytes()).hexdigest()
        )
        source.unlink()
        assert "input_missing_or_changed" in RunStore(registry).readiness(result["id"])["blockers"]
    finally:
        registry.close()


def test_readiness_weak_inputs_and_compare_do_not_claim_causes(study):
    registry = Registry(study)
    try:
        a = record(
            registry, RunSpec(script=str(study / "pipeline.py"), inputs=[str(study / "input.csv")])
        )
        assert RunStore(registry).readiness(a["id"])["status"] == "UNKNOWN"
        code = script(study, "raise ValueError('not retained')\n")
        b = record(registry, RunSpec(script=code))
        comparison = RunStore(registry).compare(a["id"], b["id"])
        assert "state" in {x["field"] for x in comparison["changes"]}
        assert "not causal" in comparison["limitation"]
    finally:
        registry.close()


def test_standalone_sdk_capture_lock_errors_and_no_false_output_event(study):
    with capture(study, script=str(study / "pipeline.py")) as current:

        @instrument
        def transform():
            with input_file(study / "input.csv") as source:
                value = source.read()
            with output_file(study / "sdk.csv") as target:
                target.write(value)

        transform()
        with pytest.raises(FileExistsError):
            with output_file(study / "input.csv"):
                pass
        with pytest.raises(MarsdError, match="existing capture"):
            with capture(study, script=str(study / "pipeline.py")):
                pass
        with pytest.raises(MarsdError, match="owns"):
            Registry(study)
    reader = ReadOnlyRegistry(study)
    try:
        run = RunStore(reader).get(current.id)
        assert run["state"] == "SUCCEEDED"
        assert len(run["events"]) == 4
        assert {r["version"]["name"] for r in run["references"] if r["role"] == "output"} == {
            "sdk.csv"
        }
    finally:
        reader.close()


def test_event_schema_caps_paths_and_idempotency(study):
    registry = Registry(study)
    try:
        recorder = Recorder(registry, RunSpec(script=str(study / "pipeline.py")))
        event = {"event_id": "event-1", "kind": "stage_start", "name": "bounded"}
        assert recorder.event(event) and recorder.event(event)
        assert not recorder.event({**event, "name": "conflicting"})
        assert not recorder.event(
            {"event_id": "escape", "kind": "read", "path": str(study.parent / "outside.csv")}
        )
        assert not recorder.event({"event_id": "unknown", "kind": "execute", "command": "bad"})
        recorder.count = 512
        assert not recorder.event({"event_id": "over-cap", "kind": "stage_finish"})
        result = recorder.finish("SUCCEEDED", exit_code=0)
        assert len(result["events"]) == 1 and result["rejected_events"] >= 3
    finally:
        registry.close()


def test_interrupted_run_and_schema_backup_restore(study):
    registry = Registry(study)
    recorder = Recorder(registry, RunSpec(script=str(study / "pipeline.py")))
    registry.close()
    registry = Registry(study)
    try:
        assert RunStore(registry).get(recorder.id)["state"] == "INTERRUPTED"
        snapshot = registry.backup("runs.sqlite")
        from marsd.maintenance import restore

        restore(registry, snapshot, apply=True)
        assert RunStore(registry).get(recorder.id)["state"] == "INTERRUPTED"
    finally:
        registry.close()


def test_cli_real_run_readiness_and_readers_share_owner(study):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "--project",
            str(study),
            "--input",
            str(study / "input.csv"),
            "--",
            sys.executable,
            str(study / "pipeline.py"),
        ],
    )
    assert result.exit_code == 0, result.output
    run = json.loads(result.stdout)
    registry = Registry(study)
    try:
        for command in [
            ["runs", "list"],
            ["runs", "show", run["id"]],
            ["runs", "diff", run["id"], run["id"]],
            ["lineage", run["id"]],
            ["impact", run["id"]],
        ]:
            result = runner.invoke(app, [*command, "--project", str(study)])
            assert result.exit_code == 0, result.output
        result = runner.invoke(
            app, ["runs", "replay", run["id"], "--project", str(study), "--dry-run"]
        )
        assert result.exit_code == 4 and json.loads(result.stdout)["executed"] is False
    finally:
        registry.close()


def test_lineage_declarations_are_atomic_idempotent_typed_and_labeled(study):
    registry = Registry(study)
    try:
        a = registry.register(study / "input.csv")["version_id"]
        graph = Graph(registry)
        body = Declaration.model_validate(
            {
                "importer_id": "synthetic",
                "event_id": "one",
                "nodes": [{"id": "cal:01", "kind": "entity", "label": "Synthetic calibration"}],
                "edges": [{"source": "cal:01", "target": a, "relation": "references"}],
            }
        )
        assert graph.import_declaration(body)["imported"]
        assert graph.import_declaration(body)["idempotent"]
        assert graph.query("cal:01", depth=1)["edges"][0]["evidence"] == "declared"
        with pytest.raises(MarsdError, match="different content"):
            graph.import_declaration(body.model_copy(update={"edges": []}))
        invalid = Declaration.model_validate(
            {
                "importer_id": "synthetic",
                "event_id": "two",
                "nodes": [{"id": "cal:02", "kind": "entity", "label": "Another"}],
                "edges": [{"source": a, "target": "missing", "relation": "derived"}],
            }
        )
        with pytest.raises(MarsdError):
            graph.import_declaration(invalid)
        assert registry.db.execute("SELECT 1 FROM graph_nodes WHERE id='cal:02'").fetchone() is None
        with pytest.raises(ValueError):
            Declaration.model_validate(
                {
                    "importer_id": "x",
                    "event_id": "y",
                    "edges": [
                        {
                            "source": "x",
                            "target": a,
                            "relation": "references",
                            "evidence": "observed",
                        }
                    ],
                }
            )
    finally:
        registry.close()


def test_cycle_bounds_inferred_filters_and_unknown_links(study):
    registry = Registry(study)
    try:
        a = registry.register(study / "input.csv")["version_id"]
        other = study / "second.csv"
        other.write_text("id\n0002\n")
        b = registry.register(other)["version_id"]
        graph = Graph(registry)
        with registry.db:
            graph.edge(a, b, "derived", "declared", "fixture")
            graph.edge(b, a, "derived", "declared", "fixture")
        bounded = graph.query(a, depth=20, max_nodes=1)
        assert len(bounded["nodes"]) == 1 and not bounded["coverage"]["complete"]
        complete = graph.query(a, depth=20)
        assert len(complete["nodes"]) == 2
        with registry.db:
            graph.node("cal:unknown", "entity", "Missing linkage")
            graph.edge("cal:unknown", a, "references", "inferred", "hypothesis")
        assert graph.query("cal:unknown")["edges"] == []
        assert graph.query("cal:unknown", include_inferred=True)["edges"]
        assert "Missing links are unknown" in complete["limitation"]
    finally:
        registry.close()


def test_validation_contract_and_reference_set_edges(demo):
    with Workbench(demo) as wb:
        report = wb.execute(
            Request(
                operation="validate",
                path=str(demo / "data/run_041.parquet"),
                contract=str(demo / "contracts/measurement.yaml"),
            )
        )
        graph = Graph(wb.registry).query(report["report_id"], direction="upstream", depth=1)
        assert {edge["relation"] for edge in graph["edges"]} == {
            "validated",
            "applies",
            "checked_against",
        }
        assert report["inputs"][0]["version_id"] in {n["id"] for n in graph["nodes"]}


def test_browser_run_graph_endpoints_require_auth_and_never_launch(study):
    registry = Registry(study)
    try:
        run = record(registry, RunSpec(script=str(study / "pipeline.py")))
    finally:
        registry.close()
    with TestClient(
        create_app(study, launch_code="phase-three", port=8765), base_url="http://127.0.0.1:8765"
    ) as client:
        for endpoint in [
            "runs",
            f"runs/{run['id']}",
            f"runs/{run['id']}/readiness",
            f"lineage/{run['id']}",
        ]:
            assert client.get("/api/" + endpoint).status_code == 401
        token = client.post("/api/session", json={"code": "phase-three"}).json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/api/runs", headers=headers).json()[0]["id"] == run["id"]
        assert client.get("/api/lineage/" + run["id"], headers=headers).json()["seed"] == run["id"]
        assert (
            client.post(
                "/api/runs", json={"script": str(study / "pipeline.py")}, headers=headers
            ).status_code
            == 405
        )


def test_pinned_version_survives_views_and_refuses_changed_source(study):
    with Workbench(study) as wb:
        source = wb.registry.register(study / "input.csv", strong=True)
        selected = Request(
            operation="preview", path=source["path"], version_id=source["version_id"]
        )
        assert wb.execute(selected)["inputs"][0]["version_id"] == source["version_id"]
        (study / "input.csv").write_text("id,value\n0001,99\n")
        with pytest.raises(MarsdError) as error:
            wb.execute(selected)
        assert error.value.code == "VERSION_UNAVAILABLE"
        assert wb.registry.register(study / "input.csv")["version_id"] != source["version_id"]


def test_output_declaration_does_not_attribute_unchanged_preexisting_file(study):
    registry = Registry(study)
    try:
        result = record(
            registry, RunSpec(script=str(study / "pipeline.py"), outputs=[str(study / "input.csv")])
        )
        assert result["state"] == "SUCCEEDED"
        assert result["references"][0]["status"] == "unchanged_preexisting_not_attributed"
        assert Graph(registry).query(result["id"])["edges"] == []
    finally:
        registry.close()


def test_bad_input_preparation_does_not_leave_a_running_run(study):
    registry = Registry(study)
    try:
        with pytest.raises(MarsdError):
            record(
                registry,
                RunSpec(
                    script=str(study / "pipeline.py"),
                    inputs=[str(study / "input.csv"), str(study / "missing.csv")],
                ),
            )
        assert registry.db.execute("SELECT 1 FROM runs WHERE state='RUNNING'").fetchone() is None
        assert record(registry, RunSpec(script=str(study / "pipeline.py")))["state"] == "SUCCEEDED"
    finally:
        registry.close()


def test_owner_death_stops_guarded_script_and_restart_interrupts(study):

    marker = study / "guarded.pid"
    code = script(
        study,
        f"import os,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n",
    )
    helper = study / "owner.py"
    helper.write_text(
        f"from marsd.registry import Registry\nfrom marsd.runs import RunSpec,record\nr=Registry({str(study)!r})\nrecord(r,RunSpec(script={code!r}))\n"
    )
    owner = subprocess.Popen(
        [sys.executable, str(helper)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        child_pid = int(marker.read_text())
        owner.kill()
        owner.wait(timeout=2)
        deadline = time.monotonic() + 5
        while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
            if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                break
            time.sleep(0.05)
        assert (
            not psutil.pid_exists(child_pid)
            or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        )
        registry = Registry(study)
        try:
            assert RunStore(registry).list()[0]["state"] == "INTERRUPTED"
        finally:
            registry.close()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=2)
        if child_pid and psutil.pid_exists(child_pid):
            try:
                psutil.Process(child_pid).kill()
            except psutil.Error:
                pass


def test_instrument_refuses_deferred_async_and_generator_boundaries():
    async def coroutine():
        return 1

    def generator():
        yield 1

    for function in [coroutine, generator]:
        with pytest.raises(MarsdError, match="synchronous"):
            instrument(function)


def test_fast_exit_with_live_child_is_cleaned_and_not_success(study):
    code = script(
        study,
        "import subprocess,sys\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath(__file__).with_name('fast-child.pid').write_text(str(p.pid))\n",
    )
    registry = Registry(study)
    try:
        result = record(registry, RunSpec(script=code))
        pid = int((study / "fast-child.pid").read_text())
        assert result["state"] == "FAILED"
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    finally:
        registry.close()


def test_event_channel_rejects_wrong_auth_and_oversize_frames(study):
    code = script(
        study,
        """import os,socket,json
port=int(os.environ['MARSD_RUN_PORT'])
def send(body):
 with socket.create_connection(('127.0.0.1',port),timeout=2) as connection:
  connection.sendall((json.dumps(body)+'\\n').encode())
  return connection.recv(1)
assert send({'token':'wrong-secret','event_id':'injected','kind':'stage_start','name':'must-not-persist'})==b'0'
assert send({'token':os.environ['MARSD_RUN_TOKEN'],'padding':'x'*17000})==b''
assert send({'token':os.environ['MARSD_RUN_TOKEN'],'event_id':'valid','kind':'stage_start','name':'explicit'})==b'1'
assert send({'token':os.environ['MARSD_RUN_TOKEN'],'event_id':'valid-end','kind':'stage_finish','name':'explicit'})==b'1'
""",
    )
    registry = Registry(study)
    try:
        result = record(registry, RunSpec(script=code))
        assert result["state"] == "SUCCEEDED"
        assert len(result["events"]) == 2
        assert "must-not-persist" not in json.dumps(result)
        assert not result["unclosed_stages"]
    finally:
        registry.close()


def test_lineage_import_cli_and_file_boundaries(study):
    declaration = study / "dependencies.json"
    declaration.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "importer_id": "user",
                "event_id": "one",
                "nodes": [
                    {"id": "synthetic:entity", "kind": "entity", "label": "Synthetic entity"}
                ],
                "edges": [],
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(app, ["lineage-import", str(declaration), "--project", str(study)])
    assert result.exit_code == 0 and json.loads(result.stdout)["imported"]
    repeated = runner.invoke(app, ["lineage-import", str(declaration), "--project", str(study)])
    assert repeated.exit_code == 0 and json.loads(repeated.stdout)["idempotent"]
    declaration.write_bytes(b" " * (1024**2 + 1))
    refused = runner.invoke(app, ["lineage-import", str(declaration), "--project", str(study)])
    assert refused.exit_code == 3 and "BUDGET_EXCEEDED" in refused.stderr


def test_project_module_cannot_replace_the_trusted_bootstrap(study):
    shadow = study / "marsd"
    shadow.mkdir()
    marker = study / "unexpected-import.marker"
    (shadow / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('unselected bootstrap')\n"
    )
    registry = Registry(study)
    try:
        assert record(registry, RunSpec(script=str(study / "pipeline.py")))["state"] == "SUCCEEDED"
        assert not marker.exists()
    finally:
        registry.close()


def test_schema_v3_upgrade_and_restore_preserve_version_identity_and_sources(study):
    import sqlite3

    from marsd.maintenance import restore

    registry = Registry(study)
    original = registry.register(study / "input.csv")
    registry.close()
    database = study / ".marsd/registry.sqlite"
    with sqlite3.connect(database) as connection:
        for table in [
            "run_events",
            "run_refs",
            "runs",
            "graph_edges",
            "graph_nodes",
            "graph_imports",
        ]:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=3")
    before = (study / "input.csv").read_bytes()
    registry = Registry(study)
    try:
        assert registry.db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert registry.register(study / "input.csv")["version_id"] == original["version_id"]
        assert Graph(registry).query(original["version_id"])["edges"] == []
        assert RunStore(registry).list() == []
        backup = next((study / ".marsd/backups").glob("schema-v3-*.sqlite"))
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        restored = restore(registry, backup, apply=True)
        assert restored["project_identity"] == "project_id_matches"
        assert restored["restored_schema_version"] == 4
        assert registry.register(study / "input.csv")["version_id"] == original["version_id"]
        assert (study / "input.csv").read_bytes() == before
    finally:
        registry.close()


def test_caught_output_write_failure_remains_partial_after_zero_exit(study):
    code = script(
        study,
        "from pathlib import Path\nfrom marsd.sdk import output_file\np=Path(__file__).parent\ntry:\n with output_file(p/'caught-partial.csv') as output:\n  output.write(b'id\\n0001\\n')\n  raise ValueError('handled by scientific script')\nexcept ValueError:\n pass\n",
    )
    registry = Registry(study)
    try:
        result = record(registry, RunSpec(script=code, outputs=[str(study / "caught-partial.csv")]))
        assert result["state"] == "SUCCEEDED" and result["exit_code"] == 0
        assert all(ref["partial"] for ref in result["references"] if ref["role"] == "output")
        assert all(edge["partial_output"] for edge in Graph(registry).query(result["id"])["edges"])
        assert RunStore(registry).readiness(result["id"])["status"] == "UNKNOWN"
        assert result["events"][0]["event"]["partial"] is True
    finally:
        registry.close()
