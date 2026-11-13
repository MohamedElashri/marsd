"""Build only on the native supported host. Generated runtimes never enter source archives."""

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    ("Darwin", "arm64"): "aarch64-apple-darwin",
    ("Windows", "amd64"): "x86_64-pc-windows-msvc",
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
}


def run(*command, cwd=ROOT, **kwargs):
    args = [str(item) for item in command]
    args[0] = shutil.which(args[0]) or args[0]
    subprocess.run(args, check=True, cwd=cwd, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()
    target = TARGETS.get((platform.system(), platform.machine().lower()))
    if not target:
        raise SystemExit(
            "Native builds require macOS ARM64, Windows x86_64 or Linux x86_64; cross-compiling a Python runtime is unsupported."
        )
    if not args.skip_frontend:
        run("npm", "--prefix", "web", "run", "build")
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        ROOT / "desktop/runtime",
        "--workpath",
        ROOT / "desktop/build",
        ROOT / "desktop/python/backend.spec",
    )
    runtime = ROOT / "desktop/runtime/backend"
    if platform.system() == "Darwin":
        run(
            sys.executable,
            ROOT / "scripts/check_macos_runtime.py",
            "--runtime",
            runtime,
            "--minimum",
            "15.0",
        )
    (runtime / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": target,
                "python": platform.python_version(),
                "platform": platform.system(),
                "architecture": platform.machine(),
            },
            indent=2,
        )
        + "\n"
    )
    if not args.backend_only:
        run(sys.executable, ROOT / "scripts/desktop_notices.py", "--target", target)
        cli = ROOT / "web/node_modules/.bin" / ("tauri.cmd" if sys.platform == "win32" else "tauri")
        run(
            cli,
            "build",
            "--config",
            "tauri.conf.json",
            "--target",
            target,
            "--bundles",
            {"Darwin": "app,dmg", "Windows": "nsis", "Linux": "deb"}[platform.system()],
            cwd=ROOT / "desktop/src-tauri",
        )
