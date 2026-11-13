import json
import os
import threading
import time

import h5py
import numpy as np
import psutil
import pytest

from marsd.domain import Request
from marsd.registry import create_project
from marsd.service import Workbench

pytestmark = pytest.mark.performance


def sample_process(parent, stop, samples):
    while not stop.wait(0.02):
        try:
            samples.append(
                parent.memory_info().rss
                + sum(p.memory_info().rss for p in parent.children(recursive=True))
            )
        except psutil.Error:
            pass


def test_logical_and_physically_populated_arrays_have_bounded_previews(tmp_path):
    root = create_project(tmp_path / "bench")
    sparse = root / "logical_10gib.h5"
    with h5py.File(sparse, "w") as f:
        f.create_dataset("array", shape=(131072, 10240), dtype="f8", chunks=(64, 64))
    physical = root / "physical_64mib.h5"
    with h5py.File(physical, "w") as f:
        d = f.create_dataset("array", shape=(2048, 4096), dtype="f8", chunks=(256, 1024))
        rng = np.random.default_rng(123)
        for start in range(0, 2048, 256):
            d[start : start + 256, :] = rng.normal(size=(256, 4096))
    measurements = []
    with Workbench(root) as wb:
        for path in (sparse, physical):
            for attempt in range(4):
                start = time.monotonic()
                stop = threading.Event()
                parent = psutil.Process(os.getpid())
                baseline = parent.memory_info().rss
                samples = [baseline]

                sampler = threading.Thread(
                    target=sample_process, args=(parent, stop, samples), daemon=True
                )
                sampler.start()
                try:
                    result = wb.execute(
                        Request(
                            operation="preview",
                            path=str(path),
                            node="/array",
                            selection="0:32,0:32",
                        )
                    )
                finally:
                    stop.set()
                    sampler.join(timeout=1)
                additional = max(samples) - baseline
                assert additional < 800 * 1024**2
                assert result["coverage"]["visited"] == 1024
                assert result["execution"]["peak_worker_rss"] < 800 * 1024**2
                assert path.stat().st_size < 80 * 1024**2
                measurements.append(
                    {
                        "source": path.name,
                        "physical_bytes": path.stat().st_size,
                        "logical_bytes": 10 * 1024**3 if path == sparse else 64 * 1024**2,
                        "attempt": attempt,
                        "peak_additional_backend_and_children_rss": additional,
                        "seconds": time.monotonic() - start,
                        **result["execution"],
                    }
                )
    print(json.dumps(measurements))
