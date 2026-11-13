"""Explicitly aligned comparisons without float coercion of integer measurements."""

from __future__ import annotations

import math
from fractions import Fraction
from functools import lru_cache
from typing import Any

from marsd.adapters import Dataset
from marsd.domain import MAX_BYTES, MarsdError, Request, dumps


def equal_value(a: Any, b: Any, request: Request) -> bool:
    if a is None or b is None:
        return a is None and b is None and request.null_equal
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float, Fraction)) and isinstance(b, (int, float, Fraction)):
        if isinstance(a, float) and math.isnan(a) or isinstance(b, float) and math.isnan(b):
            return (
                request.nan_equal
                and isinstance(a, float)
                and isinstance(b, float)
                and math.isnan(a)
                and math.isnan(b)
            )
        if isinstance(a, float) and math.isinf(a) or isinstance(b, float) and math.isinf(b):
            return a == b
        # Fractions preserve large integer differences and avoid intermediate overflow.
        return abs(Fraction(a) - Fraction(b)) <= Fraction(request.atol) + Fraction(
            request.rtol
        ) * abs(Fraction(b))
    return type(a) is type(b) and a == b


@lru_cache(maxsize=1)
def unit_registry() -> Any:
    import pint

    return pint.UnitRegistry(non_int_type=Fraction, autoconvert_offset_to_baseunit=True)


def unit_relation(a: Any, b: Any) -> tuple[str, Any]:
    if a is None and b is None:
        return "unspecified", None
    normalized = []
    for value in (a, b):
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return "unknown", None
        if not isinstance(value, str) or not value.strip() or len(value) > 1024:
            return "unknown", None
        normalized.append(value)
    a, b = normalized
    if any(x in text.lower() for text in (a, b) for x in ("db", "decibel", "neper", "dbm")):
        return "unsupported_logarithmic", None
    try:
        registry = unit_registry()
        left, right = registry.Unit(a), registry.Unit(b)
        if left.dimensionality != right.dimensionality:
            return "incompatible", None
        if left == right:
            return "same", None

        def convert(value: int | float) -> Any:
            if isinstance(value, float) and not math.isfinite(value):
                return value
            # Rational arithmetic over Pint's definitions avoids float64 loss of int64 bits.
            return registry.Quantity(Fraction(value), left).to(right).magnitude

        return "convertible", convert
    except Exception:
        return "unknown", None


def _schema_changes(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    a = {f["name"]: f for f in left}
    b = {f["name"]: f for f in right}
    return [
        {"field": key, "a": a.get(key), "b": b.get(key)}
        for key in sorted(a.keys() | b.keys())
        if dumps(a.get(key)) != dumps(b.get(key))
    ]


def _index(rows: list[dict[str, Any]], keys: list[str]) -> dict[tuple[Any, ...], dict[str, Any]]:
    index = {}
    for row in rows:
        key = tuple(row[k] for k in keys)
        if any(v is None or isinstance(v, float) and not math.isfinite(v) for v in key):
            raise MarsdError(
                "INVALID_ALIGNMENT",
                "Alignment keys cannot be null or nonfinite.",
                action="Choose complete unique keys, or explicitly compare by position.",
                exit_code=2,
            )
        if key in index:
            raise MarsdError(
                "DUPLICATE_KEYS",
                "Alignment keys are not unique.",
                action="Choose unique composite keys or explicitly compare by position.",
                exit_code=2,
            )
        index[key] = row
    return index


def compare(a: Dataset, b: Dataset, request: Request) -> dict[str, Any]:
    if a.tabular != b.tabular:
        return {
            "outcome": "DIFFERENT",
            "scope": "structure",
            "structural_changes": [{"reason": "Incompatible dataset kinds"}],
            "coverage": {"complete": True, "scope": "structure"},
        }
    return compare_tables(a, b, request) if a.tabular else compare_arrays(a, b, request)


def compare_tables(a: Dataset, b: Dataset, request: Request) -> dict[str, Any]:
    limit = min(request.max_rows, 100_000) if request.keys else request.max_rows
    if request.keys and request.position:
        raise MarsdError(
            "INVALID_ALIGNMENT", "Choose keys or positional alignment, not both.", exit_code=2
        )
    projection = list(dict.fromkeys([*request.keys, *request.columns])) if request.columns else None
    left = a.scan(limit, projection, max_bytes=MAX_BYTES // 2)
    right = b.scan(limit, projection, max_bytes=MAX_BYTES // 2)
    structural = _schema_changes(left.fields, right.fields)
    if left.total is not None and right.total is not None and left.total != right.total:
        structural.append({"field": "$row_count", "a": left.total, "b": right.total})
    if request.keys and (not left.complete or not right.complete):
        raise MarsdError(
            "BUDGET_EXCEEDED",
            "Exact keyed comparison exceeds the row budget.",
            action="Select smaller input tables; external joins are deferred.",
            details={"maximum_rows_per_side": limit},
        )
    common = sorted({f["name"] for f in left.fields} & {f["name"] for f in right.fields})
    pairs: list[tuple[Any, dict[str, Any], dict[str, Any]]]
    if request.keys:
        if set(request.keys) - set(common):
            raise MarsdError(
                "INVALID_ALIGNMENT", "An alignment key is absent in one input.", exit_code=2
            )
        ai, bi = _index(left.rows, request.keys), _index(right.rows, request.keys)
        shared = sorted(ai.keys() & bi.keys(), key=dumps)
        pairs = [(key, ai[key], bi[key]) for key in shared]
        unmatched_a, unmatched_b = len(ai.keys() - bi.keys()), len(bi.keys() - ai.keys())
    elif request.position:
        pairs = [(i, x, y) for i, (x, y) in enumerate(zip(left.rows, right.rows, strict=False))]
        unmatched_a, unmatched_b = (
            max(0, len(left.rows) - len(right.rows)),
            max(0, len(right.rows) - len(left.rows)),
        )
    else:
        raise MarsdError(
            "ALIGNMENT_REQUIRED",
            "Choose alignment keys or explicitly select positional comparison.",
            exit_code=2,
        )
    units_a = {f["name"]: f.get("unit") for f in left.fields}
    units_b = {f["name"]: f.get("unit") for f in right.fields}
    policies: dict[str, Any] = {}
    unknown: list[str] = []
    for field in common:
        relation, converter = unit_relation(units_a[field], units_b[field])
        policies[field] = (relation, converter)
        if (
            relation in {"unknown", "unsupported_logarithmic"}
            or relation == "convertible"
            and not request.convert_units
        ):
            unknown.append(field)
    differences: list[dict[str, Any]] = []
    count = 0
    paired_nans = 0
    for key, x, y in pairs:
        a.check_cancel()
        for field in common:
            if field in unknown:
                continue
            av, bv = x[field], y[field]
            if (
                isinstance(av, float)
                and isinstance(bv, float)
                and math.isnan(av)
                and math.isnan(bv)
            ):
                paired_nans += 1
            relation, converter = policies[field]
            if relation == "convertible" and av is not None and isinstance(av, (int, float)):
                av = converter(av)
            same = relation != "incompatible" and equal_value(av, bv, request)
            if not same:
                count += 1
                if len(differences) < 100:
                    differences.append({"key": key, "field": field, "a": x[field], "b": bv})
    complete = left.complete and right.complete and not unknown
    changed = bool(structural or count or unmatched_a or unmatched_b)
    return {
        "outcome": "DIFFERENT" if changed else ("EQUAL" if complete else "INCONCLUSIVE"),
        "scope": "projected_table_values_and_schema" if projection else "table_values_and_schema",
        "projection": projection,
        "alignment": {"keys": request.keys, "position": request.position},
        "structural_changes": structural,
        "different_cells": count,
        "paired_nans": paired_nans,
        "unmatched_a": unmatched_a,
        "unmatched_b": unmatched_b,
        "examples": differences,
        "examples_truncated": count > len(differences),
        "units": {
            k: {
                "a": units_a[k],
                "b": units_b[k],
                "relation": v[0],
                "converted_a_to_b": v[0] == "convertible" and request.convert_units,
                "conversion_arithmetic": "rational_over_pint_definitions",
            }
            for k, v in policies.items()
        },
        "inconclusive_fields": unknown,
        "coverage": {
            "complete": complete,
            "a": left.coverage,
            "b": right.coverage,
            "counts_complete": complete,
            "aligned_rows": len(pairs),
        },
        "tolerance": {
            "atol": request.atol,
            "rtol": request.rtol,
            "reference": "B",
            "nan_equal": request.nan_equal,
            "null_equal": request.null_equal,
        },
    }


def compare_arrays(a: Dataset, b: Dataset, request: Request) -> dict[str, Any]:
    if request.node == "/":
        left, right = a.inspect(), b.inspect()
        aa = {n["path"]: n for n in left["nodes"]}
        bb = {n["path"]: n for n in right["nodes"]}
        changes = [
            {"path": k, "a": aa.get(k), "b": bb.get(k)}
            for k in sorted(aa.keys() | bb.keys())
            if dumps(aa.get(k)) != dumps(bb.get(k))
        ]
        complete = left["coverage"]["complete"] and right["coverage"]["complete"]
        return {
            "outcome": "DIFFERENT" if changes else ("EQUAL" if complete else "INCONCLUSIVE"),
            "scope": "structure_and_metadata_only",
            "structural_changes": changes[:100],
            "changes_truncated": len(changes) > 100,
            "coverage": {"complete": complete},
            "limitation": "Select a dataset path and selection to compare numeric values.",
        }
    x = a.hdf_node(request.node)
    y = b.hdf_node(request.node)
    if getattr(x, "shape", None) != getattr(y, "shape", None):
        return {
            "outcome": "DIFFERENT",
            "scope": "structure",
            "structural_changes": [
                {
                    "path": request.node,
                    "a_shape": getattr(x, "shape", None),
                    "b_shape": getattr(y, "shape", None),
                }
            ],
            "coverage": {"complete": True, "scope": "shape"},
            "limitation": "Value comparison requires equal shapes.",
        }
    left_values, left, lc = a.array(request.node, request.selection, cap=request.max_rows)
    right_values, right, rc = b.array(request.node, request.selection, cap=request.max_rows)
    relation, converter = unit_relation(left["unit"], right["unit"])
    unknown = (
        relation in {"unknown", "unsupported_logarithmic"}
        or relation == "convertible"
        and not request.convert_units
    )
    structural = [
        {"field": k, "a": left[k], "b": right[k]}
        for k in ("dtype", "shape", "unit")
        if left[k] != right[k]
    ]
    a_meta, b_meta = a.describe_node(request.node), b.describe_node(request.node)
    metadata_complete = a_meta["attributes_complete"] and b_meta["attributes_complete"]
    if dumps(a_meta["attributes"]) != dumps(b_meta["attributes"]):
        structural.append(
            {"field": "attributes", "a": a_meta["attributes"], "b": b_meta["attributes"]}
        )
    examples: list[dict[str, Any]] = []
    count, paired_nans = 0, 0
    for i, (av, bv) in enumerate(
        zip(left_values.reshape(-1).tolist(), right_values.reshape(-1).tolist(), strict=True)
    ):
        if unknown:
            break
        if isinstance(av, float) and isinstance(bv, float) and math.isnan(av) and math.isnan(bv):
            paired_nans += 1
        value = converter(av) if relation == "convertible" else av
        if relation == "incompatible" or not equal_value(value, bv, request):
            count += 1
            if len(examples) < 100:
                examples.append({"selection_flat_index": i, "a": av, "b": bv})
    return {
        "outcome": "DIFFERENT"
        if structural or count
        else ("INCONCLUSIVE" if unknown or not metadata_complete else "EQUAL"),
        "scope": "explicit_array_selection",
        "array": left,
        "structural_changes": structural,
        "different_cells": count,
        "paired_nans": paired_nans,
        "examples": examples,
        "examples_truncated": count > len(examples),
        "units": {
            "relation": relation,
            "converted_a_to_b": relation == "convertible" and request.convert_units,
            "conversion_arithmetic": "rational_over_pint_definitions",
        },
        "coverage": {
            "complete": not unknown and metadata_complete,
            "metadata_complete": metadata_complete,
            "scope": "requested_selection",
            "a": lc,
            "b": rc,
        },
        "tolerance": {
            "atol": request.atol,
            "rtol": request.rtol,
            "reference": "B",
            "nan_equal": request.nan_equal,
        },
    }
