import json
import statistics
import time

import h5py
import pytest

from marsd.domain import Request
from marsd.registry import create_project
from marsd.service import Workbench

pytestmark = pytest.mark.performance


def test_hdf_metadata_1000_nodes_without_eager_value_scan(tmp_path):
    root = create_project(tmp_path / "hierarchy")
    path = root / "metadata.h5"
    with h5py.File(path, "w") as file:
        for i in range(999):
            file.create_dataset(f"array_{i:04}", shape=(1_000_000,), dtype="f8", chunks=(1000,))
    assert path.stat().st_size < 4 * 1024**2  # Logical data is unallocated; metadata is real.
    times = []
    with Workbench(root) as wb:
        for _ in range(4):
            start = time.monotonic()
            result = wb.execute(Request(operation="inspect", path=str(path)))
            times.append(time.monotonic() - start)
            assert len(result["nodes"]) == 1000 and result["coverage"]["complete"]
            assert result["execution"]["peak_worker_rss"] < 800 * 1024**2
        assert len(wb.jobs.results) <= 8
    assert wb.jobs.results == {}
    assert statistics.median(times[1:]) <= 5
    print(
        json.dumps(
            {
                "benchmark": "1000-node HDF5 metadata",
                "seconds": times,
                "median_seconds": statistics.median(times[1:]),
                "physical_bytes": path.stat().st_size,
            }
        )
    )
