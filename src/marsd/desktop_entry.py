"""Frozen runtime dispatch before CLI imports or multiprocessing startup."""

import multiprocessing
import sys


def main() -> None:
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] in {
        "--marsd-run-child",
        "--desktop-serve",
        "--desktop-self-test",
    }:
        mode = sys.argv.pop(1)
        if mode == "--marsd-run-child":
            from marsd._run_child import main as child_main

            child_main()
        elif mode == "--desktop-serve":
            from marsd.desktop_server import main as server_main

            server_main()
        else:
            from marsd.desktop_diagnostics import main as diagnostics_main

            diagnostics_main()
    else:
        from marsd.cli import app

        app()


if __name__ == "__main__":
    main()
