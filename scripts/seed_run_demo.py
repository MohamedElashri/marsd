"""Record real synthetic success/failure and declare one calibration dependency."""

from pathlib import Path

from marsd.lineage import Declaration, Graph
from marsd.registry import Registry
from marsd.runs import RunSpec, record


def seed(root: Path):
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
        return {"success": success["id"], "failure": failure["id"], "input_version": version}
    finally:
        registry.close()
