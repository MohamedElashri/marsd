"""Native-owner protocol through real pipes and loopback, including owner failure."""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psutil
import pytest

from marsd.registry import Registry


def launch(project, owner=None, create=False):
    args = [
        sys.executable,
        "-m",
        "marsd",
        "--desktop-serve",
        "--project",
        str(project),
        "--owner-pid",
        str(owner or os.getpid()),
    ]
    if create:
        args.append("--create")
    child = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert child.stdout is not None
    with ThreadPoolExecutor(1) as pool:
        try:
            ready = json.loads(pool.submit(child.stdout.readline).result(timeout=35))
        except BaseException:
            child.kill()
            child.wait()
            raise
    return child, ready


def stop(child):
    if child.poll() is None:
        assert child.stdin is not None
        child.stdin.close()
    try:
        child.wait(timeout=12)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        pytest.fail("Desktop backend did not stop after control-pipe EOF")


def test_native_protocol_one_use_auth_eof_and_reopen(tmp_path):
    project = tmp_path / "workspace with spaces مرصد Δ µ"
    child, ready = launch(project, create=True)
    try:
        assert set(ready) == {"protocol", "pid", "port", "code"}
        assert ready["protocol"] == 1
        # Windows venv python.exe can be a redirector which owns the actual interpreter.
        assert ready["pid"] in {
            child.pid,
            *(p.pid for p in psutil.Process(child.pid).children(recursive=True)),
        }
        with httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}", trust_env=False) as client:
            assert client.get("/api/project").status_code == 401
            assert (
                client.get("/api/project", headers={"Host": "attacker.example"}).status_code == 403
            )
            login = client.post("/api/session", json={"code": ready["code"]})
            assert login.status_code == 200
            assert client.post("/api/session", json={"code": ready["code"]}).status_code == 400
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            assert client.get("/api/project").status_code == 200
            assert client.get("/api/jobs").json() == []
            source = project / "source.csv"
            source.write_text("id,value\n0001,42\n", encoding="utf-8")
            job = client.post(
                "/api/jobs", json={"operation": "preview", "path": str(source)}
            ).json()["id"]
            deadline = time.monotonic() + 20
            while True:
                result = client.get(f"/api/jobs/{job}").json()
                if result["state"] not in {"QUEUED", "RUNNING"}:
                    break
                assert time.monotonic() < deadline, "Native control pipe blocked a worker"
                time.sleep(0.05)
            assert result["state"] == "SUCCEEDED", result
            assert result["result"]["rows"] == [{"id": "0001", "value": "42"}]
    finally:
        stop(child)
    assert child.returncode == 0
    Registry(project).close()
    reopened, _ = launch(project)
    stop(reopened)
    assert reopened.returncode == 0


def test_native_protocol_owner_death_releases_project(tmp_path):
    owner = subprocess.Popen(
        [
            str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable,
            "-c",
            "import time; time.sleep(60)",
        ]
    )
    child = None
    try:
        child, _ = launch(tmp_path / "project", owner.pid, create=True)
        owner.terminate()
        owner.wait(timeout=5)
        child.wait(timeout=12)
        assert child.returncode == 0, child.stderr.read().decode()
        Registry(tmp_path / "project").close()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        if child is not None:
            stop(child)


def test_native_protocol_invalid_project_returns_typed_error(tmp_path):
    child, ready = launch(tmp_path / "absent")
    try:
        child.wait(timeout=10)
        assert child.returncode == 3
        assert ready["error"]["code"]
        assert "code" not in ready
        assert not (tmp_path / "absent").exists()
    finally:
        stop(child)
