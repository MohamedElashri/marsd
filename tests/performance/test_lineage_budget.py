import json
import statistics
import time

import pytest

from marsd.lineage import Graph
from marsd.registry import Registry, create_project

pytestmark = pytest.mark.performance


def test_ten_thousand_node_thirty_thousand_edge_queries(tmp_path):
    root = create_project(tmp_path / "graph")
    registry = Registry(root)
    try:
        with registry.db:
            registry.db.executemany(
                "INSERT INTO graph_nodes VALUES(?,?,?,?)",
                [
                    (f"node:{i}", "artifact_version", f"Synthetic node {i}", "{}")
                    for i in range(10000)
                ],
            )
            registry.db.executemany(
                "INSERT INTO graph_edges VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        f"edge:{i}:{step}",
                        f"node:{i}",
                        f"node:{(i + step) % 10000}",
                        "derived",
                        "declared",
                        "synthetic-benchmark",
                        "2026-11-13T00:00:00Z",
                    )
                    for i in range(10000)
                    for step in (1, 17, 113)
                ],
            )
        graph = Graph(registry)
        measurements = {}
        for depth in (1, 6):
            durations = []
            for _ in range(4):
                start = time.monotonic()
                result = graph.query("node:0", depth=depth, max_nodes=200, max_edges=500)
                durations.append(time.monotonic() - start)
                assert len(result["nodes"]) <= 200 and len(result["edges"]) <= 500
            measurements[str(depth)] = {
                "seconds": durations,
                "warm_median_seconds": statistics.median(durations[1:]),
            }
            assert statistics.median(durations[1:]) <= 2
        physical = sum(p.stat().st_size for p in registry.home.glob("registry.sqlite*"))
        print(
            json.dumps(
                {
                    "benchmark": "10000 nodes / 30000 edges",
                    "measurements": measurements,
                    "database_and_wal_bytes": physical,
                }
            )
        )
    finally:
        registry.close()
