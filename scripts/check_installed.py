"""Install the wheel into a clean venv and exercise it outside the source tree.

Pass --wheelhouse DIR to prohibit network during installation as well.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from check_wheel import current_wheel

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path)
    options = parser.parse_args()
    wheel = current_wheel()
    with tempfile.TemporaryDirectory(
        prefix="marsd-installed-", ignore_cleanup_errors=True
    ) as directory:
        temporary = Path(directory)
        environment = temporary / "environment"
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run("uv", "venv", "--python", sys.executable, environment)
        requirements = temporary / "requirements.txt"
        run(
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--output-file",
            requirements,
            cwd=ROOT,
        )
        flags = (
            ["--offline", "--no-index", "--find-links", options.wheelhouse]
            if options.wheelhouse
            else []
        )
        run(
            "uv",
            "pip",
            "install",
            "--python",
            python,
            *flags,
            "--requirements",
            requirements,
            wheel,
        )
        smoke = temporary / "smoke.py"
        shutil.copyfile(ROOT / "scripts/installed_smoke.py", smoke)
        runtime_env = {
            **os.environ,
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
        }
        project = temporary / "user-project"
        run(python, "-I", smoke, "--project", project, cwd=temporary, env=runtime_env)
        run(python, "-I", "-m", "marsd", "version", cwd=temporary, env=runtime_env)

        before = {
            p.relative_to(project).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in project.rglob("*")
            if p.is_file()
        }
        run("uv", "pip", "uninstall", "--python", python, "marsd")
        run(
            python,
            "-I",
            "-c",
            "import importlib.util; assert importlib.util.find_spec('marsd') is None",
            cwd=temporary,
        )
        after = {
            p.relative_to(project).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in project.rglob("*")
            if p.is_file()
        }
        assert before == after, "Package uninstall modified user project/source files"
        print("Source-preserving uninstall: PASS")
