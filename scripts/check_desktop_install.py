"""Install/remove the native candidate and exercise its real WebKit/WebView2 UI in CI.

Test-only tools: tauri-driver, WebKitWebDriver or matching msedgedriver. No driver is shipped.
"""

import argparse
import base64
import hashlib
import json
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import psutil

from marsd import __version__

ELEMENT = "element-6066-11e4-a52e-4f735466cecf"


def run(*args, **kwargs):
    kwargs.setdefault("timeout", 300)
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Driver:
    def __init__(self, port):
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=120
        )
        self.session = None

    def call(self, method, path, body=None):
        response = self.client.request(method, path, json=body)
        value = response.json().get("value")
        if response.is_error or isinstance(value, dict) and value.get("error"):
            raise AssertionError(f"Native WebDriver failed: {value}")
        return value

    def execute(self, script, *args, asynchronous=False):
        return self.call(
            "POST",
            f"/session/{self.session}/execute/{'async' if asynchronous else 'sync'}",
            {"script": script, "args": list(args)},
        )

    def wait_text(self, text, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in self.execute("return document.body.innerText"):
                return
            time.sleep(0.1)
        raise AssertionError(f"Native view did not show {text!r}")

    def click(self, label, selector="button"):
        element = self.execute(
            "return [...document.querySelectorAll(arguments[0])].find(e => e.textContent.includes(arguments[1]))",
            selector,
            label,
        )
        assert element, label
        self.call("POST", f"/session/{self.session}/element/{element[ELEMENT]}/click", {})

    def input(self, label, value):
        element = self.execute(
            'return [...document.querySelectorAll("label")].find(e => e.textContent.includes(arguments[0]))?.querySelector("input")',
            label,
        )
        assert element, label
        path = f"/session/{self.session}/element/{element[ELEMENT]}"
        self.call("POST", path + "/clear", {})
        self.call("POST", path + "/value", {"text": value})

    def open_project(self, project):
        # Exercise the same scoped bridge used by the bundled React UI; no credentials are read.
        result = self.execute(
            """const done = arguments[arguments.length - 1];
        window.__TAURI_INTERNALS__.invoke('open_project', {project: arguments[0], create: false, roots: []})
          .then(() => { window.dispatchEvent(new Event('marsd-project-changed')); done({ok:true}); })
          .catch(error => done({error:String(error)}));""",
            str(project),
            asynchronous=True,
        )
        assert result == {"ok": True}, result
        self.wait_text("Synthetic spectral study")


def verify(package, output, native_driver=None):
    system = platform.system()
    assert system in {"Windows", "Linux"}, "Direct tauri-driver smoke targets Windows and Linux."
    started = time.monotonic()
    with tempfile.TemporaryDirectory(
        prefix="marsd-install-", ignore_cleanup_errors=True
    ) as directory:
        root = Path(directory).resolve()
        project = root / "study مرصد Δ µ"
        package_name, install, executable, backend = None, root / "application", None, None
        if system == "Linux":
            package_name = subprocess.check_output(
                ["dpkg-deb", "-f", package, "Package"], text=True
            ).strip()
            run("sudo", "apt-get", "install", "-y", package)
            files = subprocess.check_output(["dpkg", "-L", package_name], text=True).splitlines()
            executable = next(Path(p) for p in files if p.endswith("/bin/marsd-desktop"))
            backend = next(Path(p) for p in files if p.endswith("/runtime/backend/marsd-backend"))
        else:
            # NSIS /D must be the final argument; this temporary install path has no spaces.
            assert " " not in str(install)
            run(package, "/S", f"/D={install}")
            executable = install / "marsd-desktop.exe"
            backend = install / "runtime/backend/marsd-backend.exe"
        assert executable.is_file() and backend.is_file()
        driver_process, driver, session = None, None, None
        try:
            diagnostic = run(
                backend,
                "--desktop-self-test",
                "--project",
                project,
                timeout=150,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            assert json.loads(diagnostic.stdout)["version"] == __version__
            source_hashes = hashes(project / "data")
            port, native_port = free_port(), free_port()
            command = [
                shutil.which("tauri-driver"),
                "--port",
                str(port),
                "--native-port",
                str(native_port),
            ]
            assert command[0], "Install the pinned tauri-driver test tool"
            if native_driver:
                command += ["--native-driver", str(native_driver)]
            with output.with_suffix(".driver.log").open("w", encoding="utf-8") as log:
                driver_process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                driver = Driver(port)
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if driver.client.get("/status").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    assert driver_process.poll() is None and time.monotonic() < deadline, (
                        "Native driver did not start"
                    )
                    time.sleep(0.1)
                session = driver.call(
                    "POST",
                    "/session",
                    {
                        "capabilities": {
                            "alwaysMatch": {"tauri:options": {"application": str(executable)}}
                        }
                    },
                )
                driver.session = session["sessionId"]
                driver.call(
                    "POST",
                    f"/session/{driver.session}/timeouts",
                    {"script": 100000, "pageLoad": 60000, "implicit": 1000},
                )
                driver.wait_text("Open a local project.")
                driver.open_project(project)
                driver.wait_text(f"Marsd {__version__}")
                driver.click("run_041.h5")
                driver.wait_text("Structure loaded.")
                driver.input("Selection", "0:4,0:4")
                driver.click("Preview selection")
                driver.wait_text("16 visited of 4096")
                driver.click("run_041.parquet")
                driver.wait_text("inspect · succeeded")
                driver.click("Compare", "nav button")
                driver.click("run_042.parquet", "select option")
                driver.input("Unique alignment keys", "device_id,wavelength_nm")
                driver.click("Compare A with B")
                driver.wait_text("DIFFERENT")
                driver.click("Validate", "nav button")
                driver.click("Validate declared rules")
                driver.wait_text("Validation receipt")
                assert "PASS" in driver.execute("return document.body.innerText")
                driver.click("Runs", "nav button")
                driver.click("Show all project runs", "label")
                driver.wait_text("SUCCEEDED")
                driver.wait_text("FAILED")
                driver.click("Assess readiness")
                driver.wait_text("No target code was executed.")
                run_id = driver.execute(
                    """const done = arguments[arguments.length-1];
                window.__TAURI_INTERNALS__.invoke('backend_request', {path:'runs', method:'GET', body:null})
                  .then(reply => done(reply.data[0].id)).catch(e => done({error:String(e)}));""",
                    asynchronous=True,
                )
                assert isinstance(run_id, str), run_id
                driver.click("Lineage", "nav button")
                driver.input("Entity or artifact version ID", run_id)
                driver.click("Load dependencies")
                driver.wait_text("visible entities")
                screenshot = driver.call("GET", f"/session/{driver.session}/screenshot")
                output.with_suffix(".png").write_bytes(base64.b64decode(screenshot))
                driver.click("Settings", "nav button")
                driver.click("Close project")
                driver.wait_text("Open a local project.")
                driver.open_project(project)
                driver.click("Settings", "nav button")
                driver.click("Close project")
                driver.wait_text("Open a local project.")
                assert source_hashes == hashes(project / "data")
                driver.call("DELETE", f"/session/{driver.session}")
                driver.session = None
        finally:
            if driver and driver.session:
                try:
                    output.with_suffix(".png").write_bytes(
                        base64.b64decode(
                            driver.call("GET", f"/session/{driver.session}/screenshot")
                        )
                    )
                    output.with_suffix(".page.txt").write_text(
                        driver.execute("return document.body.innerText"), encoding="utf-8"
                    )
                except (httpx.HTTPError, AssertionError):
                    pass
                try:
                    driver.call("DELETE", f"/session/{driver.session}")
                except (httpx.HTTPError, AssertionError):
                    pass
            if driver:
                driver.client.close()
            if driver_process and driver_process.poll() is None:
                driver_process.terminate()
                driver_process.wait(timeout=10)
            # Only terminate this test installation, never an unrelated desktop app.
            for process in psutil.process_iter(["exe"]):
                try:
                    if process.info["exe"] and Path(process.info["exe"]) in {executable, backend}:
                        process.terminate()
                        psutil.wait_procs([process], timeout=10)
                except psutil.Error:
                    pass
            before_remove = hashes(project) if project.exists() else {}
            if system == "Linux":
                run("sudo", "apt-get", "install", "--reinstall", "-y", package)
            else:
                run(package, "/S", f"/D={install}")
            assert not project.exists() or hashes(project) == before_remove, (
                "Manual package replacement changed project files"
            )
            if system == "Linux":
                run("sudo", "apt-get", "remove", "-y", package_name)
            else:
                uninstall = next(install.glob("*uninstall*.exe"))
                run(uninstall, "/S")
            assert not project.exists() or hashes(project) == before_remove, (
                "Native uninstaller changed project files"
            )
        deadline = time.monotonic() + 20
        while executable.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not executable.exists(), "Native uninstaller left the installed executable"
        result = {
            "status": "PASS",
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            "version": __version__,
            "seconds": round(time.monotonic() - started, 3),
            "webview": {
                key: session.get("capabilities", {}).get(key)
                for key in ("browserName", "browserVersion", "platformName")
            },
            "installed_native_webview": "PASS",
            "manual_same_version_replacement_preserves_project": "PASS",
            "inspect_preview_diff_validate_run_readiness_lineage": "PASS",
            "close_reopen": "PASS",
            "source_preservation": "PASS",
            "native_uninstall_preserves_project": "PASS",
            "scope": "Runner OS and X11/WebView2 only. Project selection uses the real scoped native IPC; OS file-picker/confirmation dialogs, minimum-OS user walkthrough, signing and offline prerequisite certification remain separate.",
        }
        output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-driver", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    verify(args.package.resolve(), args.output.resolve(), args.native_driver)
