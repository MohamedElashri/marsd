import pytest
import yaml

from marsd.adapters import Dataset
from marsd.contracts import Contract, load_contract, validate
from marsd.domain import MarsdError, Request


def test_good_and_broken_handoff_have_independent_expected_outcomes(demo):
    c = load_contract(demo / "contracts/measurement.yaml")
    with Dataset(demo / "data/run_041.parquet") as data:
        assert (
            validate(data, c, Request(operation="validate", path=str(data.path)))["outcome"]
            == "PASS"
        )
    with Dataset(demo / "data/run_042.parquet") as data:
        result = validate(data, c, Request(operation="validate", path=str(data.path)))
        assert result["outcome"] == "FAIL"
        assert {f["rule_id"] for f in result["findings"] if f["status"] == "FAIL"} == {
            "transmission_range",
            "transmission_unit",
            "calibration",
        }


def test_missing_registry_is_not_false_pass_and_partial_success_is_inconclusive(demo):
    c = load_contract(demo / "contracts/measurement.yaml")
    c.references = {}
    with Dataset(demo / "data/run_041.parquet") as data:
        result = validate(data, c, Request(operation="validate", path=str(data.path)))
        assert result["outcome"] == "INCONCLUSIVE"
        assert (
            next(f for f in result["findings"] if f["rule_id"] == "calibration")["status"]
            == "INCONCLUSIVE"
        )
        partial = validate(data, c, Request(operation="validate", path=str(data.path), max_rows=2))
        assert partial["outcome"] == "INCONCLUSIVE"
        assert not partial["coverage"]["complete"]


def test_known_failure_survives_partial_scan(demo):
    c = Contract(
        name="bounds",
        revision="1",
        rules=[{"id": "range", "kind": "range", "field": "transmission", "maximum": 0.1}],
    )
    with Dataset(demo / "data/run_041.parquet") as data:
        result = validate(data, c, Request(operation="validate", path=str(data.path), max_rows=2))
        assert result["outcome"] == "FAIL"
        assert not result["coverage"]["complete"]


@pytest.mark.parametrize(
    "text",
    [
        'name: x\nname: duplicate\nrevision: "1"\nrules: []',
        'name: x\nrevision: "1"\nrules: &r []\nextra: *r',
        '!!python/object/apply:os.system ["echo should-never-run"]',
        'name: x\nrevision: "1"\nrules: [{id: a, kind: arbitrary_python, field: x}]',
        'name: x\nrevision: "1"\nrules: [{id: a, kind: required, field: x, maximum: 3}]',
    ],
)
def test_invalid_yaml_and_closed_schema_reject_without_execution(demo, text):
    path = demo / "contracts/invalid.yaml"
    path.write_text(text)
    with pytest.raises(MarsdError, match="invalid"):
        load_contract(path)


def test_duplicate_rule_ids_rejected(demo):
    path = demo / "contracts/duplicate.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "x",
                "revision": "1",
                "rules": [{"id": "a", "kind": "required", "field": "x"}] * 2,
            }
        )
    )
    with pytest.raises(MarsdError):
        load_contract(path)


def test_engine_error_takes_precedence_but_keeps_known_schema_violation(demo):
    from marsd.domain import CSVPolicy

    path = demo / "data/broken.csv"
    path.write_text("value\n1\nnot-an-integer\n")
    contract = Contract(
        name="strict",
        revision="1",
        rules=[
            {"id": "missing", "kind": "required", "field": "absent"},
            {"id": "numeric", "kind": "range", "field": "value", "minimum": 0},
        ],
    )
    with Dataset(path, CSVPolicy.model_validate({"schema": {"value": "int64"}})) as data:
        result = validate(data, contract, Request(operation="validate", path=str(path)))
        assert result["outcome"] == "ERROR"
        assert [f["status"] for f in result["findings"]] == ["FAIL", "ERROR"]


def test_invalid_contract_diagnostics_do_not_echo_unrecognized_secret_values(demo):
    path = demo / "contracts/invalid-secret.yaml"
    path.write_text('name: x\nrevision: "1"\nrules: []\npassword: never-echo-this-value\n')
    with pytest.raises(MarsdError) as caught:
        load_contract(path)
    assert "never-echo-this-value" not in str(caught.value.payload())
