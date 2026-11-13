"""Exercise a frozen runtime without developer tools, then remove only its copied installation."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psutil


def digest_data(project):
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / "data").iterdir()
    }


def check(backend):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="marsd-native-") as directory:
        root = Path(directory)
        install = root / "installation with spaces مرصد Δ µ"
        shutil.copytree(backend.parent, install, symlinks=True)
        executable = install / backend.name
        project = root / "user project مرصد Δ µ"
        env = {
            **os.environ,
            "PATH": str(root / "no-tools"),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
        }
        for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT"]:
            env.pop(key, None)
        diagnostic = subprocess.run(
            [executable, "--desktop-self-test", "--project", project],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        report = json.loads(diagnostic.stdout)
        assert report["status"] == "PASS" and report["frozen"]
        before = digest_data(project)
        failed = subprocess.run(
            [
                executable,
                "run",
                "--project",
                project,
                "--output",
                project / "outputs/partial.csv",
                "--",
                executable,
                project / "pipeline/analyze.py",
                "--fail",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        failure = json.loads(failed.stdout)
        assert failed.returncode == 3 and failure["state"] == "FAILED", failure
        assert any(event["event"]["kind"] == "stage_error" for event in failure["events"])
        assert (project / "outputs/partial.csv").is_file()
        sleeper = project / "pipeline/native_timeout.py"
        sleeper.write_text("import time\ntime.sleep(60)\n")
        timeout = subprocess.run(
            [
                executable,
                "run",
                "--project",
                project,
                "--timeout",
                "0.5",
                "--",
                executable,
                sleeper,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert timeout.returncode == 3 and json.loads(timeout.stdout)["state"] == "TIMED_OUT"
        startup = []
        for cycle in range(3):
            begin = time.monotonic()
            owner = (
                subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
                if cycle == 2
                else None
            )
            child = subprocess.Popen(
                [
                    executable,
                    "--desktop-serve",
                    "--project",
                    project,
                    "--owner-pid",
                    str(owner.pid if owner else os.getpid()),
                ],
                cwd=root,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            descendants = []
            try:
                with ThreadPoolExecutor(1) as pool:
                    try:
                        ready = json.loads(pool.submit(child.stdout.readline).result(timeout=45))
                    except BaseException:
                        child.kill()
                        raise
                startup.append(round(time.monotonic() - begin, 3))
                assert ready["protocol"] == 1 and ready["pid"] == child.pid
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{ready['port']}", trust_env=False, timeout=10
                ) as client:
                    assert client.get("/api/project").status_code == 401
                    login = client.post("/api/session", json={"code": ready["code"]})
                    assert login.status_code == 200
                    assert (
                        client.post("/api/session", json={"code": ready["code"]}).status_code == 400
                    )
                    client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
                    assert client.get("/api/project").status_code == 200
                    job = client.post(
                        "/api/jobs",
                        json={
                            "operation": "preview",
                            "path": str(project / "data/run_041.h5"),
                            "node": "/measurement/transmission",
                            "selection": "0:4,0:4",
                        },
                    ).json()["id"]
                    deadline = time.monotonic() + 35
                    while True:
                        result = client.get(f"/api/jobs/{job}").json()
                        if result["state"] in ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]:
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.05)
                    assert result["state"] == "SUCCEEDED", result
                    # Cancel a second, queued operation while one worker starts.
                    first = client.post(
                        "/api/jobs",
                        json={"operation": "inspect", "path": str(project / "data/run_041.h5")},
                    ).json()["id"]
                    queued = client.post(
                        "/api/jobs",
                        json={"operation": "inspect", "path": str(project / "data/run_042.h5")},
                    ).json()["id"]
                    assert client.delete(f"/api/jobs/{queued}").json()["state"] == "CANCELLED"
                    descendants = psutil.Process(child.pid).children(recursive=True)
                    if cycle == 0:
                        client.delete(f"/api/jobs/{first}")
                if owner:
                    owner.terminate()
                    owner.wait(timeout=5)
                    child.wait(
                        timeout=15
                    )  # Owner death with stdin still open must also stop the runtime.
            finally:
                if owner and owner.poll() is None:
                    owner.kill()
                    owner.wait()
                child.stdin.close()  # Owner pipe EOF must cancel/join any active worker.
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                    raise AssertionError(
                        "Frozen backend did not exit after owner pipe EOF"
                    ) from None
            assert child.returncode == 0
            assert all(
                not p.is_running() or p.status() == psutil.STATUS_ZOMBIE for p in descendants
            ), "Worker survived owner shutdown"
        assert digest_data(project) == before
        project_before = {
            str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in project.rglob("*")
            if p.is_file()
        }
        shutil.rmtree(install)
        assert {
            str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in project.rglob("*")
            if p.is_file()
        } == project_before
        return {
            "status": "PASS",
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "diagnostics": report,
            "startup_seconds": startup,
            "cancel_shutdown_reopen": "PASS",
            "frozen_cli_failed_run_partial_output_and_timeout": "PASS",
            "owner_death_with_open_control_pipe": "PASS",
            "copied_runtime_removal_preserves_project": "PASS",
            "seconds": round(time.monotonic() - started, 3),
            "scope": "Frozen runtime only; native webview and installer qualification are separate.",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.backend.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
