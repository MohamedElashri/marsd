"""Explicit synthetic diagnostics for a bundled runtime outside its source tree."""

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

from marsd import __version__
from marsd.domain import MarsdError, Request, dumps
from marsd.fixtures import generate
from marsd.lineage import Graph
from marsd.registry import Registry
from marsd.runs import RunSpec, RunStore, record
from marsd.service import Workbench


def exercise(root: Path):
    started = time.monotonic()
    root = generate(root)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "data").iterdir()}
    with Workbench(root) as wb:
        source = root / "data/run_041.h5"
        assert wb.execute(Request(operation="inspect", path=str(source)))["format"] == "HDF5"
        assert (
            wb.execute(
                Request(
                    operation="preview",
                    path=str(source),
                    node="/measurement/transmission",
                    selection="0:4,0:4",
                )
            )["coverage"]["visited"]
            == 16
        )
        a, b = root / "data/run_041.parquet", root / "data/run_042.parquet"
        assert (
            wb.execute(
                Request(
                    operation="diff", path=str(a), other=str(b), keys=["device_id", "wavelength_nm"]
                )
            )["outcome"]
            == "DIFFERENT"
        )
        contract = str(root / "contracts/measurement.yaml")
        good = wb.execute(Request(operation="validate", path=str(a), contract=contract))
        bad = wb.execute(Request(operation="validate", path=str(b), contract=contract))
        assert good["outcome"] == "PASS" and bad["outcome"] == "FAIL"
        assert wb.export(bad["report_id"], format="html").is_file()
    registry = Registry(root)
    try:
        run = record(
            registry,
            RunSpec(
                script=str(root / "pipeline/analyze.py"),
                inputs=[str(root / "data/identifiers.csv")],
                outputs=[str(root / "outputs/analysis.csv")],
            ),
        )
        assert run["state"] == "SUCCEEDED", run.get("reason")
        assert len(run["events"]) == 4
        failure = record(
            registry,
            RunSpec(
                script=str(root / "pipeline/analyze.py"),
                arguments=["--fail"],
                inputs=[str(root / "data/identifiers.csv")],
                outputs=[str(root / "outputs/partial.csv")],
            ),
        )
        assert failure["state"] == "FAILED"
        assert any(ref["partial"] for ref in failure["references"] if ref["role"] == "output")
        assert Graph(registry).query(run["id"])["edges"]
        assert RunStore(registry).readiness(run["id"])["executed"] is False
        assert registry.backup("native-runtime.sqlite").is_file()
    finally:
        registry.close()
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "data").iterdir()}
    assert before == after
    return {
        "status": "PASS",
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "seconds": round(time.monotonic() - started, 3),
        "source_preservation": "PASS",
        "journeys": ["J1", "J2", "J3", "J4", "J6"],
        "project": str(root),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="New synthetic project directory; existing data is never replaced.",
    )
    args = parser.parse_args()
    try:
        print(dumps(exercise(args.project)), flush=True)
    except MarsdError as error:
        print(json.dumps({"error": error.payload()}), file=sys.stderr)
        raise SystemExit(error.exit_code) from error
