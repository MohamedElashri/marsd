"""Deterministic, wholly synthetic reference project and independent expectations."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from marsd.domain import MarsdError, dumps
from marsd.registry import create_project, local_path


def generate(output: str | Path, seed: int = 42) -> Path:
    import h5py
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import yaml

    root = local_path(output)
    if root.exists() and any(root.iterdir()):
        raise MarsdError("OUTPUT_EXISTS", "Fixture destination must be new or empty.", exit_code=2)
    root.mkdir(parents=True, exist_ok=True)
    create_project(root, name="Synthetic spectral study")
    for name in ("data", "contracts", "outputs"):
        (root / name).mkdir()
    rng = np.random.default_rng(seed)
    axis = np.linspace(1500, 1600, 64)
    xx, yy = np.meshgrid(np.linspace(-2, 2, 64), np.linspace(-2, 2, 64))
    image = np.exp(-(xx**2 + yy**2)) * 0.8 + rng.normal(0, 0.002, (64, 64))
    for version in (41, 42):
        with h5py.File(root / "data" / f"run_{version:03}.h5", "w", track_order=True) as f:
            f.attrs["source"] = "Fully synthetic; no instrument or employer data"
            g = f.create_group("measurement")
            g.create_dataset("wavelength", data=axis).attrs["unit"] = "nm"
            data = image.copy()
            if version == 42:
                data[24, 31] += 0.12
            d = g.create_dataset("transmission", data=data, chunks=(16, 16), compression="gzip")
            d.attrs["unit"] = "dimensionless"
            d.attrs["description"] = "Synthetic spatial transmission map"
            f.create_dataset(
                "large_integer", data=np.array([2**53, 2**53 + version - 41], dtype="int64")
            )
    base: dict[str, Any] = {
        "device_id": [f"sample_{i:02}" for i in range(8)],
        "wavelength_nm": [1550] * 8,
        "transmission": [0.31, 0.42, 0.53, 0.64, 0.75, 0.66, 0.57, 0.48],
        "calibration_id": ["cal_synthetic_01"] * 8,
    }
    for version in (41, 42):
        values = {k: list(v) for k, v in base.items()}
        if version == 42:
            values["transmission"][3] = 1.2
            values["calibration_id"][5] = "cal_missing"
        fields = [
            pa.field("device_id", pa.string(), nullable=False),
            pa.field("wavelength_nm", pa.int64(), nullable=False, metadata={b"unit": b"nm"}),
            pa.field(
                "transmission",
                pa.float64(),
                metadata={b"unit": b"dimensionless" if version == 41 else b"s"},
            ),
            pa.field("calibration_id", pa.string()),
        ]
        table = pa.Table.from_pydict(values, schema=pa.schema(fields))
        pq.write_table(
            table,
            root / "data" / f"run_{version:03}.parquet",
            compression="snappy",
            row_group_size=4,
        )
    with (root / "data" / "identifiers.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sample_id", "value", "comment"])
        writer.writerows(
            [
                ["0001", "1.25", "synthetic"],
                ["0002", "", "missing value"],
                ["0003", "2.5", "<script>never execute</script>"],
            ]
        )
    contract = {
        "schema_version": 1,
        "name": "Synthetic measurement handoff",
        "revision": "1",
        "references": {
            "calibrations": {
                "namespace": "synthetic",
                "version": "1",
                "values": ["cal_synthetic_01"],
            }
        },
        "rules": [
            {"id": "device_present", "kind": "required", "field": "device_id"},
            {"id": "wavelength_type", "kind": "dtype", "field": "wavelength_nm", "dtype": "int64"},
            {"id": "wavelength_unit", "kind": "unit", "field": "wavelength_nm", "unit": "nm"},
            {
                "id": "transmission_present",
                "kind": "nullability",
                "field": "transmission",
                "nullable": False,
            },
            {
                "id": "transmission_range",
                "kind": "range",
                "field": "transmission",
                "minimum": 0,
                "maximum": 1,
            },
            {
                "id": "transmission_unit",
                "kind": "unit",
                "field": "transmission",
                "unit": "dimensionless",
            },
            {
                "id": "identity",
                "kind": "unique",
                "field": "device_id",
                "fields": ["device_id", "wavelength_nm"],
            },
            {
                "id": "calibration",
                "kind": "membership",
                "field": "calibration_id",
                "reference": "calibrations",
            },
        ],
    }
    (root / "contracts" / "measurement.yaml").write_text(
        yaml.safe_dump(contract, sort_keys=False), encoding="utf-8"
    )
    (root / "golden.json").write_text(
        dumps(
            {
                "seed": seed,
                "provenance": "Entirely synthetic",
                "expectations": {
                    "good_contract": "PASS",
                    "bad_contract": "FAIL",
                    "bad_rules": ["transmission_range", "transmission_unit", "calibration"],
                    "array_changed_index": [24, 31],
                    "array_different_cells": 1,
                    "csv_first_id": "0001",
                    "table_rows": 8,
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "pipeline").mkdir()
    (root / "pipeline" / "analyze.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "from marsd.sdk import instrument, input_file, output_file\n"
        "root = Path(__file__).resolve().parent.parent\n"
        "@instrument\n"
        "def analyze():\n"
        "    with input_file(root / 'data' / 'identifiers.csv') as source:\n"
        "        values = source.read()\n"
        "    destination = root / 'outputs' / ('partial.csv' if '--fail' in sys.argv else 'analysis.csv')\n"
        "    with output_file(destination) as output:\n"
        "        output.write(values)\n"
        "    if '--fail' in sys.argv:\n"
        "        raise ValueError('Synthetic failure after a partial output')\n"
        "analyze()\n",
        encoding="utf-8",
    )
    # Register reference paths without scanning their contents.
    from marsd.registry import Registry

    registry = Registry(root)
    try:
        for path in sorted((root / "data").iterdir()):
            registry.register(path)
    finally:
        registry.close()
    return root
