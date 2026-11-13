import math

import pytest
from pydantic import ValidationError

from marsd.domain import Request, dumps, wire
from marsd.explore import equal_value, unit_relation


def request(**kwargs):
    return Request(operation="diff", path="a", **kwargs)


def test_large_integer_and_integer_float_comparison_remain_exact():
    r = request()
    assert not equal_value(2**53, 2**53 + 1, r)
    assert not equal_value(2**53 + 1, float(2**53), r)
    assert equal_value(2**53, float(2**53), r)
    assert equal_value(2**63 - 1, 2**63 - 2, request(atol=1))


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (None, None, True),
        (None, float("nan"), False),
        (float("nan"), float("nan"), False),
        (float("inf"), float("inf"), True),
        (float("inf"), float("-inf"), False),
        (-0.0, 0.0, True),
        (True, 1, False),
    ],
)
def test_explicit_special_value_policies(a, b, expected):
    assert equal_value(a, b, request()) is expected


def test_nan_and_null_options_are_independent():
    assert equal_value(math.nan, math.nan, request(nan_equal=True))
    assert not equal_value(None, None, request(null_equal=False))
    assert equal_value(101, 100, request(rtol=0.01))
    assert not equal_value(101.01, 100, request(rtol=0.01))


@pytest.mark.parametrize(
    "field,value",
    [("atol", -1), ("rtol", float("nan")), ("timeout", 0), ("limit", 1001), ("max_rows", 0)],
)
def test_invalid_budgets_and_tolerances_are_rejected(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


def test_lossless_standard_json():
    value = wire({"large": 2**63 - 1, "nan": math.nan, "inf": math.inf, "bytes": b"\x00"})
    assert value["large"] == {"$int": str(2**63 - 1)}
    assert value["nan"] == {"$float": "NaN"}
    assert "NaN" not in dumps(value).replace('"NaN"', "")


def test_units_handle_dimensions_offsets_and_unknowns():
    status, convert = unit_relation("degC", "kelvin")
    assert status == "convertible"
    assert convert(0) == pytest.approx(273.15)
    assert unit_relation("nm", "s")[0] == "incompatible"
    assert unit_relation("mystery_quantity", "nm")[0] == "unknown"
    assert unit_relation("dB", "dimensionless")[0] == "unsupported_logarithmic"
    assert unit_relation(None, "nm")[0] == "unknown"
