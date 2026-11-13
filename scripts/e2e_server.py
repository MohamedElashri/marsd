"""Isolated synthetic project and real loopback server for browser tests."""

import os
import tempfile
from pathlib import Path

import uvicorn

from marsd.api import create_app
from marsd.fixtures import generate

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="marsd-browser-") as directory:
        root = generate(Path(directory) / "study")
        from seed_run_demo import seed

        seed(root)
        from marsd.registry import Registry

        registry = Registry(root)
        try:
            for i in range(
                max(0, int(os.environ.get("MARSD_E2E_ARTIFACTS", "7")) - len(registry.artifacts()))
            ):
                path = root / "data" / f"extra_{i:03}.csv"
                path.write_text("id,value\n0001,1\n")
                registry.register(path)
        finally:
            registry.close()
        uvicorn.run(
            create_app(root, launch_code="automated-test-session", port=8765),
            host="127.0.0.1",
            port=8765,
            access_log=False,
        )
