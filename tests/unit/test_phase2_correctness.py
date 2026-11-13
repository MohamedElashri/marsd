from fractions import Fraction

import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marsd.adapters import Dataset
from marsd.contracts import Contract, validate
from marsd.domain import Request
from marsd.explore import compare, equal_value, unit_relation


@pytest.mark.parametrize("value", [123, ["nm"], np.array(["nm"]), {}, b"\xff", ""])
def test_malformed_unit_metadata_is_unknown_instead_of_exception(value):
    assert unit_relation(value, "nm")[0] == "unknown"


def test_large_integer_and_affine_unit_conversion_does_not_round():
    relation, convert = unit_relation("nm", "um")
    assert relation == "convertible"
    assert convert(2**53 + 1) == Fraction(2**53 + 1, 1000)
    assert not equal_value(convert(2**53 + 1), convert(2**53), Request(operation="diff", path="a"))
    assert unit_relation("degC", "kelvin")[1](0) == Fraction(27315, 100)
    assert unit_relation(b"nm", "nm")[0] == "same"
    assert unit_relation("nm", "um")[1](float("inf")) == float("inf")


def test_metadata_unit_rule_is_inconclusive_for_nonstring_hdf_attribute(demo):
    path = demo / "data/bad_units.h5"
    with h5py.File(path, "w") as f:
        d = f.create_dataset("data", data=[1.0, 2.0])
        d.attrs["unit"] = [1, 2]
    c = Contract(
        name="unit",
        revision="1",
        rules=[{"id": "unit", "kind": "unit", "field": "/data", "unit": "nm"}],
    )
    with Dataset(path) as data:
        result = validate(data, c, Request(operation="validate", path=str(path)))
    assert result["outcome"] == "INCONCLUSIVE"


def test_validation_projects_only_rule_fields_and_ignores_unrelated_nested_values(demo):
    path = demo / "data/nested.parquet"
    pq.write_table(pa.table({"value": [1, 2], "nested": [[1], [2, 3]]}), path)
    contract = Contract(
        name="bounds",
        revision="1",
        rules=[{"id": "bounds", "kind": "range", "field": "value", "minimum": 0}],
    )
    with Dataset(path) as data:
        assert (
            validate(data, contract, Request(operation="validate", path=str(path)))["outcome"]
            == "PASS"
        )


def test_numeric_unit_diff_keeps_large_integer_distinction(demo):
    paths = [demo / "data/large_a.h5", demo / "data/large_b.h5"]
    for path, unit, value in [(paths[0], "nm", 2**53 + 1), (paths[1], "um", float((2**53) / 1000))]:
        with h5py.File(path, "w") as f:
            d = f.create_dataset("data", data=[value])
            d.attrs["unit"] = unit
    with Dataset(paths[0]) as a, Dataset(paths[1]) as b:
        result = compare(
            a, b, Request(operation="diff", path=str(paths[0]), node="/data", convert_units=True)
        )
    assert result["different_cells"] == 1
    assert result["units"]["conversion_arithmetic"] == "rational_over_pint_definitions"


def test_duplicate_parquet_names_cannot_hide_a_failing_column(demo):
    path = demo / "data/duplicate_names.parquet"
    table = pa.Table.from_arrays([pa.array([1]), pa.array([999])], names=["value", "value"])
    pq.write_table(table, path)
    from marsd.domain import MarsdError

    with Dataset(path) as data:
        with pytest.raises(MarsdError, match="unambiguously"):
            data.table_fields()
