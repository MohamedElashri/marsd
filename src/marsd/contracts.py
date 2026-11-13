"""Closed native contracts: strict YAML, immutable revisions, honest coverage."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from marsd.adapters import Dataset
from marsd.domain import MarsdError, Request, StrictModel, digest
from marsd.explore import unit_relation


class ReferenceSet(StrictModel):
    namespace: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    values: list[str | int | float | bool] = Field(max_length=10_000)


class Rule(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "required", "dtype", "shape", "nullability", "range", "unit", "unique", "membership"
    ]
    field: str = Field(min_length=1, max_length=8192)
    dtype: str | None = None
    shape: list[int | None] | None = None
    nullable: bool | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    unit: str | None = None
    reference: str | None = None
    fields: list[str] | None = None

    @model_validator(mode="after")
    def parameters(self) -> Rule:
        allowed = {
            "required": set(),
            "dtype": {"dtype"},
            "shape": {"shape"},
            "nullability": {"nullable"},
            "range": {"minimum", "maximum"},
            "unit": {"unit"},
            "unique": {"fields"},
            "membership": {"reference"},
        }[self.kind]
        extras = self.model_fields_set - {"id", "kind", "field"} - allowed
        if extras:
            raise ValueError(f"Unsupported parameters for {self.kind}: {sorted(extras)}")
        needed = {
            "dtype": self.dtype,
            "shape": self.shape,
            "nullability": self.nullable,
            "unit": self.unit,
            "membership": self.reference,
        }
        if self.kind in needed and needed[self.kind] is None:
            raise ValueError(f"{self.kind} needs its parameter")
        if self.kind == "range" and self.minimum is None and self.maximum is None:
            raise ValueError("range needs minimum or maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if self.shape and any(v is not None and (isinstance(v, bool) or v < 0) for v in self.shape):
            raise ValueError("shape dimensions must be nonnegative integers or null")
        return self


class Contract(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=256)
    revision: str = Field(min_length=1, max_length=128)
    rules: list[Rule] = Field(min_length=1, max_length=256)
    references: dict[str, ReferenceSet] = Field(default_factory=dict)
    source_units: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_rules(self) -> Contract:
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("Rule IDs must be unique")
        return self


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: UniqueLoader, node: Any, deep: bool = False) -> dict[Any, Any]:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, (str, int, float, bool)) or key in result:
            raise ValueError("YAML keys must be unique scalar values")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_contract(path: str | Path) -> Contract:
    try:
        path = Path(path)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Contract exceeds 1 MiB")
        text = path.read_text(encoding="utf-8")
        depth, count = 0, 0
        for event in yaml.parse(text):
            count += 1
            if isinstance(event, yaml.AliasEvent):
                raise ValueError("YAML aliases are not supported")
            if isinstance(event, (yaml.SequenceStartEvent, yaml.MappingStartEvent)):
                depth += 1
            elif isinstance(event, (yaml.SequenceEndEvent, yaml.MappingEndEvent)):
                depth -= 1
            if depth > 32 or count > 50_000:
                raise ValueError("YAML depth/node budget exceeded")
        return Contract.model_validate(yaml.load(text, Loader=UniqueLoader))
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as error:
        raise MarsdError(
            "INVALID_CONTRACT",
            "Contract is invalid or exceeds the safe YAML limits.",
            action="Use the native contract schema; remove unknown rules, duplicate keys and aliases.",
            details={
                "fields": [
                    {"field": str(e["loc"]), "reason": e["msg"]}
                    for e in error.errors(include_input=False, include_context=False)
                ]
            }
            if isinstance(error, ValidationError)
            else {
                "exception_type": type(error).__name__,
                "reason": "Malformed YAML, duplicate keys, aliases, unreadable input or a configured limit was exceeded.",
            },
            exit_code=2,
        ) from error


def normalized_dtype(value: str) -> str:
    return {
        "double": "float64",
        "float": "float32",
        "boolean": "bool",
        "large_string": "string",
        "utf8": "string",
    }.get(value, value)


def validate(dataset: Dataset, contract: Contract, request: Request) -> dict[str, Any]:
    metadata = dataset.inspect()
    scan = None
    scan_error: MarsdError | None = None
    if dataset.tabular:
        fields = {f["name"]: f for f in metadata["fields"]}
        if any(r.kind in {"range", "nullability", "unique", "membership"} for r in contract.rules):
            try:
                needed = {
                    r.field
                    for r in contract.rules
                    if r.kind in {"range", "nullability", "unique", "membership"}
                    and r.field in fields
                }
                for rule in contract.rules:
                    if rule.kind == "unique":
                        needed.update(name for name in (rule.fields or []) if name in fields)
                scan = dataset.scan(request.max_rows, sorted(needed))
            except MarsdError as error:
                scan_error = error
    else:
        fields = {n["path"]: n for n in metadata["nodes"]}
    findings: list[dict[str, Any]] = []
    for rule in contract.rules:
        dataset.check_cancel()
        field = fields.get(rule.field)
        coverage: dict[str, Any] = {
            "scope": "metadata",
            "complete": field is not None or metadata["coverage"]["complete"],
        }
        status = "PASS"
        reason = "Rule satisfied"
        observed: Any = None
        violations = 0
        examples: list[Any] = []
        try:
            if rule.kind == "required":
                status = "PASS" if field else ("FAIL" if coverage["complete"] else "INCONCLUSIVE")
                reason = "Required field/path exists" if field else "Required field/path is absent"
            elif field is None:
                status, reason = (
                    "INCONCLUSIVE",
                    "Field/path is absent; add a required rule to assert presence",
                )
            elif rule.kind == "dtype":
                observed = field.get("dtype")
                status = (
                    "PASS"
                    if normalized_dtype(str(observed)) == normalized_dtype(str(rule.dtype))
                    else "FAIL"
                )
                reason = "Native dtype compared without coercion"
            elif rule.kind == "shape":
                observed = field.get("shape")
                if observed is None:
                    status, reason = "INCONCLUSIVE", "Shape rule needs an array dataset"
                else:
                    status = (
                        "PASS"
                        if len(observed) == len(rule.shape or [])
                        and all(
                            e is None or e == v
                            for e, v in zip(rule.shape or [], observed, strict=False)
                        )
                        else "FAIL"
                    )
            elif rule.kind == "unit":
                source_unit = field.get("unit")
                if source_unit is None:
                    source_unit = contract.source_units.get(rule.field)
                relation, _ = unit_relation(source_unit, rule.unit)
                observed = {
                    "source_unit": source_unit,
                    "expected_unit": rule.unit,
                    "relation": relation,
                }
                status = (
                    "PASS"
                    if relation in {"same", "convertible"}
                    else ("FAIL" if relation == "incompatible" else "INCONCLUSIVE")
                )
                reason = (
                    "Dimensional compatibility only; does not establish semantic quantity validity"
                )
            else:
                if dataset.tabular:
                    if scan_error is not None:
                        raise scan_error
                    assert scan is not None
                    values = [row[rule.field] for row in scan.rows]
                    coverage = scan.coverage
                else:
                    if rule.kind == "unique":
                        raise MarsdError(
                            "UNSUPPORTED_OPERATION", "Uniqueness is supported only for tables"
                        )
                    data, _, coverage = dataset.array(rule.field, cap=request.max_rows)
                    values = data.reshape(-1).tolist()
                if rule.kind == "membership":
                    ref = contract.references.get(rule.reference or "")
                    if ref is None:
                        status, reason = (
                            "INCONCLUSIVE",
                            "The declared reference registry is unavailable",
                        )
                    else:
                        known = {digest(v) for v in ref.values}
                        bad = [v for v in values if digest(v) not in known]
                        violations, examples = len(bad), bad[:10]
                        observed = {
                            "namespace": ref.namespace,
                            "version": ref.version,
                            "reference_digest": digest(ref.model_dump()),
                        }
                elif rule.kind == "nullability":
                    bad = (
                        [i for i, v in enumerate(values) if v is None]
                        if rule.nullable is False
                        else []
                    )
                    violations, examples = len(bad), bad[:10]
                elif rule.kind == "range":
                    bad, unsupported = [], False
                    for i, value in enumerate(values):
                        if value is None:
                            continue  # Nullability is a separate explicit rule.
                        if not isinstance(value, (int, float)) or isinstance(value, bool):
                            unsupported = True
                            continue
                        if (
                            isinstance(value, float)
                            and not math.isfinite(value)
                            or rule.minimum is not None
                            and value < rule.minimum
                            or rule.maximum is not None
                            and value > rule.maximum
                        ):
                            bad.append({"index": i, "value": value})
                    violations, examples = len(bad), bad[:10]
                    if unsupported:
                        status, reason = (
                            "INCONCLUSIVE",
                            "Non-numeric values require explicit typing; no inferred numeric cast",
                        )
                elif rule.kind == "unique":
                    names = rule.fields or [rule.field]
                    if any(n not in fields for n in names):
                        status, reason = (
                            "INCONCLUSIVE",
                            "A composite uniqueness field is unavailable",
                        )
                    else:
                        assert scan is not None
                        seen = set()
                        for i, row in enumerate(scan.rows):
                            key = digest([row[n] for n in names])
                            if key in seen:
                                violations += 1
                                if len(examples) < 10:
                                    examples.append({"row": i})
                            seen.add(key)
                if violations:
                    status, reason = "FAIL", "Observed values violate the rule"
                elif status == "PASS" and not coverage["complete"]:
                    status, reason = "INCONCLUSIVE", "Unscanned data prevents a whole-source PASS"
        except MarsdError as error:
            status = (
                "INCONCLUSIVE"
                if error.code in {"UNSUPPORTED_OPERATION", "BUDGET_EXCEEDED"}
                else "ERROR"
            )
            reason = error.message
            coverage = {"complete": False, "scope": "unavailable"}
        findings.append(
            {
                "rule_id": rule.id,
                "kind": rule.kind,
                "field": rule.field,
                "severity": "error",
                "status": status,
                "reason": reason,
                "expected": rule.model_dump(exclude_none=True),
                "observed": observed,
                "violations": violations,
                "counts_complete": coverage["complete"],
                "examples": examples,
                "coverage": coverage,
                "evidence": {
                    "source_path": rule.field,
                    "contract_digest": digest(contract.model_dump()),
                },
            }
        )
    statuses = {f["status"] for f in findings}
    outcome = next((s for s in ("ERROR", "FAIL", "INCONCLUSIVE") if s in statuses), "PASS")
    return {
        "outcome": outcome,
        "contract": {
            "name": contract.name,
            "revision": contract.revision,
            "digest": digest(contract.model_dump()),
            "schema_version": 1,
        },
        "findings": findings,
        "coverage": {
            "complete": all(f["coverage"]["complete"] for f in findings),
            "scope": "declared_rules",
        },
    }
