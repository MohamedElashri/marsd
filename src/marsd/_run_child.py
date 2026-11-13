"""Guarded entry point for an explicitly selected trusted script."""

import hashlib
import os
import runpy
import sys
import threading
import time
from pathlib import Path

import psutil


def main() -> None:
    owner = psutil.Process(int(os.environ["MARSD_RUN_OWNER"]))
    created = owner.create_time()

    def watch() -> None:
        while True:
            time.sleep(0.2)
            try:
                if (
                    owner.is_running()
                    and owner.status() != psutil.STATUS_ZOMBIE
                    and owner.create_time() == created
                ):
                    continue
            except psutil.Error:
                pass
            for child in psutil.Process().children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            os._exit(70)

    threading.Thread(target=watch, daemon=True).start()
    script = Path(sys.argv[1])
    with script.open("rb") as stream:
        code = stream.read(4 * 1024**2 + 1)
    if (
        len(code) > 4 * 1024**2
        or hashlib.sha256(code).hexdigest() != os.environ["MARSD_RUN_SCRIPT_SHA"]
    ):
        raise SystemExit(75)
    sys.argv = sys.argv[1:]
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        # Clean up before this guard exits, including children spawned just before
        # a fast script exit that the owner's polling may not have observed yet.
        children = psutil.Process().children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(children, timeout=1)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=1)
        if children and sys.exception() is None:
            raise SystemExit(76)


if __name__ == "__main__":
    main()
