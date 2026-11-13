"""Isolated synthetic project and real loopback server for browser tests."""

import os
import tempfile
from pathlib import Path

import uvicorn

from marsd.api import create_app
from marsd.fixtures import generate
from marsd.lineage import Declaration, Graph
from marsd.registry import Registry
from marsd.runs import RunSpec, record


def seed_runs(root: Path) -> None:
    registry = Registry(root)
    try:
        source = str(root / "data" / "identifiers.csv")
        pipeline = str(root / "pipeline" / "analyze.py")
        success = record(
            registry,
            RunSpec(
                script=pipeline, inputs=[source], outputs=[str(root / "outputs" / "analysis.csv")]
            ),
        )
        failure = record(
            registry,
            RunSpec(
                script=pipeline,
                arguments=["--fail"],
                inputs=[source],
                outputs=[str(root / "outputs" / "partial.csv")],
            ),
        )
        assert success["state"] == "SUCCEEDED" and failure["state"] == "FAILED"
        version = success["references"][0]["version"]["version_id"]
        Graph(registry).import_declaration(
            Declaration.model_validate(
                {
                    "importer_id": "synthetic-fixture",
                    "event_id": "calibration-1",
                    "nodes": [
                        {
                            "id": "synthetic:calibration:01",
                            "kind": "entity",
                            "label": "Synthetic calibration 01",
                        }
                    ],
                    "edges": [
                        {
                            "source": "synthetic:calibration:01",
                            "target": version,
                            "relation": "references",
                            "evidence": "declared",
                        }
                    ],
                }
            )
        )
    finally:
        registry.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="marsd-browser-") as directory:
        root = generate(Path(directory) / "study")
        seed_runs(root)
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
