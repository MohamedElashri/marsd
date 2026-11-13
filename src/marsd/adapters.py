"""Trusted-file adapters. Every value read is bounded and capability-checked."""

from __future__ import annotations

import csv
import itertools
import math
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Protocol, runtime_checkable

from marsd.domain import MAX_BYTES, CSVPolicy, MarsdError, Request, dumps

MAX_ATTRIBUTE = 1024 * 1024
MAX_NODES = 1000


@dataclass
class Scan:
    fields: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    complete: bool
    total: int | None
    decoded_bytes: int

    @property
    def coverage(self) -> dict[str, Any]:
        return {
            "strategy": "full" if self.complete else "prefix",
            "visited": len(self.rows),
            "total": self.total,
            "complete": self.complete,
        }


def estimate(value: Any) -> int:
    if isinstance(value, dict):
        return sys.getsizeof(value) + sum(estimate(k) + estimate(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return sys.getsizeof(value) + sum(estimate(v) for v in value)
    return sys.getsizeof(value)


def attributes_complete(attributes: dict[str, Any]) -> bool:
    return "$truncated" not in attributes and not any(
        isinstance(v, dict) and (v.get("truncated") or v.get("unsupported"))
        for v in attributes.values()
    )


def numeric_profile(values: list[Any]) -> dict[str, Any]:
    finite = [
        v
        for v in values
        if isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
    ]
    nonfinite = sum(isinstance(v, float) and not math.isfinite(v) for v in values)
    return {
        "count": len(values),
        "nulls": sum(v is None for v in values),
        "nonfinite": nonfinite,
        "finite_count": len(finite),
        "min": min(finite) if finite else None,
        "max": max(finite) if finite else None,
        "mean": float(mean(finite)) if finite else None,
        "mean_precision": "float64_approximation",
    }


def unit_metadata(metadata: dict[bytes, bytes] | None) -> str | None:
    if not metadata:
        return None
    raw = metadata.get(b"unit") or metadata.get(b"units")
    return raw.decode("utf-8", errors="replace")[:1024] if raw else None


@runtime_checkable
class DatasetAdapter(Protocol):
    """Capabilities are explicit; unsupported streaming never pretends to be tabular."""

    def probe(self) -> dict[str, Any]: ...
    def inspect(self) -> dict[str, Any]: ...
    def list_nodes(self, path: str, *, offset: int, limit: int) -> dict[str, Any]: ...
    def preview(self, request: Request) -> dict[str, Any]: ...
    def profile(self, request: Request) -> dict[str, Any]: ...
    def scan(
        self, max_rows: int, columns: list[str] | None = None, *, max_bytes: int = MAX_BYTES
    ) -> Scan: ...


class Dataset(AbstractContextManager):
    def __init__(
        self,
        path: str | Path,
        policy: CSVPolicy | None = None,
        *,
        cancelled: Callable[[], bool] | None = None,
    ):
        self.cancelled = cancelled or (lambda: False)
        self.path = Path(path)
        self.policy = policy or CSVPolicy()
        self.handle: Any = None
        self.format = self.path.suffix.lower()
        if self.format in {".h5", ".hdf5"}:
            # Must be set before h5py loads HDF5; built-in filters remain supported.
            os.environ["HDF5_PLUGIN_PRELOAD"] = "::"
            import h5py

            while h5py.h5pl.size():
                h5py.h5pl.remove(0)
            self.handle = h5py.File(self.path, "r")
        elif self.format == ".parquet":
            import pyarrow.parquet as pq

            self.handle = pq.ParquetFile(self.path)
        elif self.format != ".csv":
            raise MarsdError("UNSUPPORTED_OPERATION", "No adapter supports this format.")

    def __exit__(self, *args: Any) -> None:
        if self.handle is not None:
            self.handle.close()

    def check_cancel(self) -> None:
        if self.cancelled():
            raise MarsdError("CANCELLED", "Operation was cancelled.")

    @property
    def tabular(self) -> bool:
        return self.format in {".csv", ".parquet"}

    def probe(self) -> dict[str, Any]:
        return {
            "format": "CSV" if self.format == ".csv" else "Parquet" if self.tabular else "HDF5",
            "tabular": self.tabular,
            "capabilities": ["inspect", "list_nodes", "preview", "profile", "compare", "validate"],
            "public_stream_batches": False,
        }

    def describe_node(self, path: str) -> dict[str, Any]:
        import h5py

        obj = self.hdf_node(path)
        item: dict[str, Any] = {
            "path": path,
            "kind": "group" if isinstance(obj, h5py.Group) else "array",
            "attributes": self._attrs(obj),
        }
        item["attributes_complete"] = attributes_complete(item["attributes"])
        if isinstance(obj, h5py.Dataset):
            reason = self.array_limitation(obj)
            item.update(
                dtype=str(obj.dtype),
                shape=list(obj.shape) if obj.shape is not None else None,
                supported=reason is None,
                limitation=reason,
            )
        return item

    def list_nodes(self, path: str = "/", *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        if not 0 <= offset <= 100_000 or not 1 <= limit <= MAX_NODES:
            raise MarsdError(
                "INVALID_SELECTION",
                "Node pages need offset 0..100000 and limit 1..1000.",
                exit_code=2,
            )
        if self.tabular:
            fields = self.table_fields()
            return {
                "nodes": fields[offset : offset + limit],
                "next_offset": offset + limit if len(fields) > offset + limit else None,
                "coverage": {
                    "complete": len(fields) <= offset + limit,
                    "scope": "field_page",
                    "offset": offset,
                },
            }
        import h5py

        obj = self.hdf_node(path)
        if not isinstance(obj, h5py.Group):
            return {
                "nodes": [self.describe_node(path)],
                "next_offset": None,
                "coverage": {"complete": True, "scope": "selected_node"},
            }
        names = list(itertools.islice(obj.keys(), offset, offset + limit + 1))
        nodes = []
        for name in names[:limit]:
            child_path = path.rstrip("/") + "/" + name
            if isinstance(obj.get(name, getlink=True), h5py.HardLink):
                nodes.append(self.describe_node(child_path))
            else:
                nodes.append(
                    {
                        "path": child_path,
                        "kind": "link",
                        "supported": False,
                        "limitation": "External/soft links are not followed",
                    }
                )
        return {
            "nodes": nodes,
            "next_offset": offset + limit if len(names) > limit else None,
            "coverage": {
                "complete": len(names) <= limit,
                "scope": "immediate_children_page",
                "offset": offset,
            },
        }

    def hdf_node(self, path: str) -> Any:
        import h5py

        if len(path) > 8192 or any(p in {".", ".."} for p in path.split("/")):
            raise MarsdError("INVALID_SELECTION", "Invalid HDF5 path.", exit_code=2)
        obj = self.handle["/"]
        for part in (p for p in path.split("/") if p):
            if not isinstance(obj, h5py.Group) or part not in obj:
                raise MarsdError("INVALID_SELECTION", "The HDF5 path does not exist.", exit_code=2)
            link = obj.get(part, getlink=True)
            if not isinstance(link, h5py.HardLink):
                raise MarsdError(
                    "UNSUPPORTED_OPERATION", "Soft and external HDF5 links are metadata-only."
                )
            obj = obj[part]
        return obj

    def _attrs(self, obj: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        budget = MAX_ATTRIBUTE
        for i, name in enumerate(obj.attrs):
            if i >= 64:
                result["$truncated"] = "More than 64 attributes"
                break
            try:
                attr = obj.attrs.get_id(name)
                if attr.get_storage_size() > budget:
                    result[name] = {"truncated": True}
                    continue
                value = obj.attrs[name]
                encoded = dumps(value)
                size = len(encoded.encode())
                if size > budget:
                    result[name] = {"truncated": True}
                else:
                    result[name] = value.tolist() if hasattr(value, "tolist") else value
                    budget -= size
            except (TypeError, ValueError, MarsdError):
                result[name] = {"unsupported": True}
        return result

    def inspect(self) -> dict[str, Any]:
        if self.tabular:
            fields = self.table_fields()
            return {
                "format": "CSV" if self.format == ".csv" else "Parquet",
                "kind": "table",
                "fields": fields[:1000],
                "total_fields": len(fields),
                "rows": self.handle.metadata.num_rows if self.handle else None,
                "row_groups": self.handle.num_row_groups if self.handle else None,
                "schema_completeness": "explicit"
                if self.format == ".parquet" or self.policy.types
                else "strings_no_inference",
                "parse_policy": self.policy.model_dump(by_alias=True)
                if self.format == ".csv"
                else None,
                "capabilities": ["preview", "profile", "compare", "validate"],
                "coverage": {
                    "complete": len(fields) <= 1000,
                    "scope": "schema",
                    "field_limit": 1000,
                },
            }
        import h5py

        nodes: list[dict[str, Any]] = []
        seen: set[int] = set()
        truncated = False
        stack = [("/", self.handle["/"])]
        while stack and len(nodes) < MAX_NODES:
            self.check_cancel()
            path, obj = stack.pop()
            address = h5py.h5o.get_info(obj.id).addr
            item: dict[str, Any] = {
                "path": path,
                "kind": "group" if isinstance(obj, h5py.Group) else "array",
                "attributes": self._attrs(obj),
            }
            if isinstance(obj, h5py.Dataset):
                reason = self.array_limitation(obj)
                item.update(
                    dtype=str(obj.dtype),
                    shape=list(obj.shape) if obj.shape is not None else None,
                    unit=obj.attrs.get("unit", obj.attrs.get("units")),
                    supported=reason is None,
                    limitation=reason,
                    chunks=list(obj.chunks) if obj.chunks else None,
                )
                nodes.append(item)
            else:
                nodes.append(item)
                if address in seen:
                    item["alias"] = True
                    continue
                seen.add(address)
                # Bounded immediate children: do not materialize an enormous group.
                remaining = MAX_NODES - len(nodes)
                children = list(itertools.islice(obj.keys(), remaining + 1))
                truncated = truncated or len(children) > remaining
                for name in reversed(children):
                    child_path = path.rstrip("/") + "/" + name
                    link = obj.get(name, getlink=True)
                    if isinstance(link, h5py.HardLink):
                        stack.append((child_path, obj[name]))
                    else:
                        if len(nodes) >= MAX_NODES:
                            break
                        nodes.append(
                            {
                                "path": child_path,
                                "kind": "link",
                                "supported": False,
                                "limitation": "External/soft links are not followed",
                            }
                        )
        return {
            "format": "HDF5",
            "kind": "hierarchy",
            "nodes": nodes,
            "capabilities": ["metadata", "numeric_slice", "profile", "compare", "validate"],
            "coverage": {
                "scope": "metadata",
                "visited": len(nodes),
                "complete": not stack
                and not truncated
                and all(attributes_complete(n.get("attributes", {})) for n in nodes),
                "limit": MAX_NODES,
            },
        }

    @staticmethod
    def array_limitation(obj: Any) -> str | None:
        import h5py

        if not isinstance(obj, h5py.Dataset):
            return "Select a numeric dataset, not a group"
        if obj.shape is None:
            return "Empty/null HDF5 dataspace"
        if obj.is_virtual or obj.external:
            return "Virtual datasets and external raw storage are not read"
        creation = obj.id.get_create_plist()
        if any(
            creation.get_filter(i)[0] not in {1, 2, 3, 4, 5, 6}
            for i in range(creation.get_nfilters())
        ):
            return "External HDF5 filters are disabled"
        if obj.dtype.itemsize > 8 and obj.dtype.kind in "iuf":
            return (
                "Numeric values wider than 64 bits are metadata-only; no silent precision reduction"
            )
        if obj.dtype.kind not in "biuf":
            return "Only fixed-size real numeric and boolean value reads are supported"
        return None

    def array(
        self, node: str, selection: str = "", *, cap: int = 10_000, preview: bool = False
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        import numpy as np

        obj = self.hdf_node(node)
        reason = self.array_limitation(obj)
        if reason:
            raise MarsdError("UNSUPPORTED_OPERATION", reason)
        shape = tuple(obj.shape)
        selectors: list[int | slice] = []
        if selection:
            parts = selection.split(",")
            if len(parts) != len(shape):
                raise MarsdError(
                    "INVALID_SELECTION",
                    "Specify one integer or slice per array dimension.",
                    exit_code=2,
                )
            try:
                for part, size in zip(parts, shape, strict=True):
                    if ":" not in part:
                        idx = int(part)
                        if not -size <= idx < size:
                            raise ValueError("out of bounds")
                        selectors.append(idx)
                    else:
                        bits = part.split(":")
                        if len(bits) > 3:
                            raise ValueError("too many slice components")
                        numbers = [int(x) if x.strip() else None for x in bits]
                        sl = slice(*numbers)
                        if sl.step is not None and sl.step <= 0:
                            raise ValueError("step must be positive")
                        start, stop, step = sl.indices(size)
                        selectors.append(slice(start, stop, step))
            except ValueError as error:
                raise MarsdError(
                    "INVALID_SELECTION",
                    "Use integer indices or start:stop:positive-step slices.",
                    exit_code=2,
                ) from error
        elif preview:
            if len(shape) > 2:
                raise MarsdError(
                    "INVALID_SELECTION",
                    "Select explicit indices/slices yielding at most two dimensions.",
                    exit_code=2,
                )
            edge = 100 if len(shape) == 2 else min(cap, 1000)
            selectors = [slice(0, min(s, edge)) for s in shape]
        else:
            selectors = [slice(0, s) for s in shape]
        output_shape = [
            len(range(*sel.indices(size)))
            for sel, size in zip(selectors, shape, strict=True)
            if isinstance(sel, slice)
        ]
        count = math.prod(output_shape)
        if preview and len(output_shape) > 2:
            raise MarsdError(
                "INVALID_SELECTION",
                "Plot selections must have at most two dimensions.",
                exit_code=2,
            )
        if count > cap or count * obj.dtype.itemsize > MAX_BYTES:
            raise MarsdError(
                "BUDGET_EXCEEDED",
                "The array selection exceeds the work budget.",
                action="Choose a smaller explicit slice.",
                details={"selected_cells": count, "limit": cap},
            )
        # A small hyperslab can still decode a large compressed chunk.
        if obj.chunks and math.prod(obj.chunks) * obj.dtype.itemsize > MAX_BYTES:
            raise MarsdError(
                "BUDGET_EXCEEDED", "A single HDF5 chunk exceeds the decoded-byte budget."
            )
        self.check_cancel()
        data = np.asarray(obj[tuple(selectors)])
        self.check_cancel()
        unit = obj.attrs.get("unit", obj.attrs.get("units"))
        if isinstance(unit, bytes):
            unit = unit.decode("utf-8", errors="replace")
        descriptor = {
            "path": node,
            "dtype": str(obj.dtype),
            "shape": list(shape),
            "selection": selection or ("bounded prefix" if preview else "full"),
            "selected_shape": list(data.shape),
            "unit": str(unit) if unit is not None else None,
        }
        coverage = {
            "visited": int(data.size),
            "total": int(math.prod(shape)),
            "complete": data.size == math.prod(shape),
            "strategy": "explicit_selection" if selection else ("prefix" if preview else "full"),
        }
        return data, descriptor, coverage

    def _csv_rows(self) -> Iterator[dict[str, Any]]:
        csv.field_size_limit(MAX_ATTRIBUTE)
        with self.path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f, delimiter=self.policy.delimiter, strict=True)
            first = next(reader, None)
            if first is None:
                return
            names = first if self.policy.header else [f"column_{i + 1}" for i in range(len(first))]
            if len(names) > 1000 or len(set(names)) != len(names) or any(not n for n in names):
                raise MarsdError(
                    "INVALID_SCHEMA",
                    "CSV headers must be nonempty, unique and at most 1,000 columns.",
                    exit_code=2,
                )
            if set(self.policy.types) - set(names):
                raise MarsdError(
                    "INVALID_SCHEMA", "Typed CSV schema names an absent column.", exit_code=2
                )
            stream = reader if self.policy.header else itertools.chain([first], reader)
            for record in stream:
                if len(record) != len(names):
                    raise MarsdError("PARSE_ERROR", "CSV row width does not match the header.")
                row: dict[str, Any] = {}
                for name, raw in zip(names, record, strict=True):
                    kind = self.policy.types.get(name, "string")
                    if raw in self.policy.null_tokens:
                        value: Any = None
                    elif kind == "string":
                        value = raw
                    else:
                        try:
                            if kind == "int64":
                                value = int(raw)
                                if not -(2**63) <= value < 2**63:
                                    raise ValueError("outside int64")
                            elif kind == "float64":
                                value = float(raw)
                            elif raw.lower() in {"true", "false"}:
                                value = raw.lower() == "true"
                            else:
                                raise ValueError("invalid boolean")
                        except ValueError as error:
                            raise MarsdError(
                                "PARSE_ERROR",
                                f"CSV column {name!r} does not match explicit {kind} typing.",
                            ) from error
                    row[name] = value
                yield row

    def table_fields(self) -> list[dict[str, Any]]:
        if self.format == ".parquet":
            names = self.handle.schema_arrow.names
            if len(set(names)) != len(names):
                raise MarsdError(
                    "INVALID_SCHEMA",
                    "Duplicate Parquet field names cannot be addressed unambiguously.",
                )
            return [
                {
                    "name": f.name,
                    "dtype": str(f.type),
                    "nullable": f.nullable,
                    "unit": unit_metadata(f.metadata),
                }
                for f in self.handle.schema_arrow
            ]
        if self.format == ".csv":
            with self.path.open("r", encoding="utf-8-sig", newline="") as f:
                first = next(csv.reader(f, delimiter=self.policy.delimiter), [])
            names = first if self.policy.header else [f"column_{i + 1}" for i in range(len(first))]
            if len(names) > 1000 or len(set(names)) != len(names) or any(not n for n in names):
                raise MarsdError(
                    "INVALID_SCHEMA",
                    "CSV headers must be unique and nonempty, with at most 1,000 columns.",
                    exit_code=2,
                )
            return [
                {
                    "name": n,
                    "dtype": self.policy.types.get(n, "string"),
                    "nullable": True,
                    "unit": None,
                }
                for n in names
            ]
        raise MarsdError("NOT_TABULAR", "This source is an array hierarchy, not a table.")

    def scan(
        self, max_rows: int, columns: list[str] | None = None, *, max_bytes: int = MAX_BYTES
    ) -> Scan:
        fields = self.table_fields()
        selected = columns if columns is not None else [f["name"] for f in fields]
        if len(set(selected)) != len(selected) or set(selected) - {f["name"] for f in fields}:
            raise MarsdError(
                "INVALID_SELECTION",
                "Projection contains duplicate or unknown columns.",
                exit_code=2,
            )
        if self.format == ".parquet":
            import pyarrow as pa

            for field in self.handle.schema_arrow:
                if field.name in selected and not (
                    pa.types.is_primitive(field.type)
                    or pa.types.is_string(field.type)
                    or pa.types.is_large_string(field.type)
                    or pa.types.is_binary(field.type)
                    or pa.types.is_null(field.type)
                ):
                    raise MarsdError(
                        "UNSUPPORTED_OPERATION",
                        "Nested and extension Parquet values are metadata-only.",
                    )
            for i in range(self.handle.num_row_groups):
                rg = self.handle.metadata.row_group(i)
                decoded = sum(
                    rg.column(j).total_uncompressed_size
                    for j in range(rg.num_columns)
                    if rg.column(j).path_in_schema in selected
                )
                if decoded > max_bytes:
                    raise MarsdError(
                        "BUDGET_EXCEEDED",
                        "A projected Parquet row group exceeds the decoded-byte budget.",
                    )

            def parquet_rows() -> Iterator[dict[str, Any]]:
                for batch in self.handle.iter_batches(
                    batch_size=min(1024, max_rows + 1), columns=selected, use_threads=False
                ):
                    if batch.nbytes > max_bytes:
                        raise MarsdError("BUDGET_EXCEEDED", "Decoded Parquet batch exceeds budget.")
                    yield from batch.to_pylist()

            stream = parquet_rows()
            total: int | None = self.handle.metadata.num_rows
        else:
            stream = self._csv_rows()
            total = None
        rows: list[dict[str, Any]] = []
        used = 0
        complete = True
        try:
            for row in stream:
                self.check_cancel()
                if len(rows) >= max_rows:
                    complete = False
                    break
                projected = {k: row[k] for k in selected}
                used += estimate(projected)
                if used > max_bytes:
                    raise MarsdError(
                        "BUDGET_EXCEEDED",
                        "Decoded table values exceed the work budget.",
                        action="Project fewer columns or request fewer rows.",
                    )
                rows.append(projected)
        finally:
            stream.close() if hasattr(stream, "close") else None
        if complete:
            total = len(rows)
        return Scan([f for f in fields if f["name"] in selected], rows, complete, total, used)

    def preview(self, request: Request) -> dict[str, Any]:
        if self.tabular:
            scan = self.scan(request.offset + request.limit, request.columns or None)
            rows = scan.rows[request.offset :]
            return {
                "kind": "table",
                "fields": scan.fields,
                "rows": rows,
                "offset": request.offset,
                "next_offset": None if scan.complete else request.offset + len(rows),
                "coverage": {**scan.coverage, "visited": len(rows), "scan_visited": len(scan.rows)},
                "decoded_bytes": scan.decoded_bytes,
                "parse_policy": self.policy.model_dump(by_alias=True)
                if self.format == ".csv"
                else None,
            }
        data, descriptor, coverage = self.array(request.node, request.selection, preview=True)
        return {
            "kind": "array",
            "array": descriptor,
            "values": data.tolist(),
            "coverage": coverage,
            "profile": numeric_profile(data.reshape(-1).tolist()),
        }

    def profile(self, request: Request) -> dict[str, Any]:
        if self.tabular:
            scan = self.scan(request.max_rows, request.columns or None)
            return {
                "kind": "table_profile",
                "fields": {
                    f["name"]: numeric_profile([r[f["name"]] for r in scan.rows])
                    for f in scan.fields
                },
                "coverage": scan.coverage,
                "decoded_bytes": scan.decoded_bytes,
            }
        data, descriptor, coverage = self.array(
            request.node, request.selection, cap=request.max_rows
        )
        return {
            "kind": "array_profile",
            "array": descriptor,
            "statistics": numeric_profile(data.reshape(-1).tolist()),
            "coverage": coverage,
        }
