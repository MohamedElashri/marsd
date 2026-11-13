"""Versioned request, budget, error and lossless wire contracts."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1
MAX_RESPONSE = 8 * 1024 * 1024
MAX_BYTES = 256 * 1024 * 1024


def now() -> str:
    return datetime.now(UTC).isoformat()


class MarsdError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        action: str = "Review the request and retry.",
        details: Any = None,
        exit_code: int = 3,
    ):
        super().__init__(message)
        self.code, self.message, self.action = code, message, action
        self.details, self.exit_code = details, exit_code

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "suggested_action": self.action,
            "details": self.details,
            "coverage": None,
        }


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, populate_by_name=True
    )


class CSVPolicy(StrictModel):
    delimiter: str = ","
    header: bool = True
    null_tokens: list[str] = Field(default_factory=lambda: [""])
    types: dict[str, Literal["string", "int64", "float64", "bool"]] = Field(
        default_factory=dict, alias="schema"
    )

    @field_validator("delimiter")
    @classmethod
    def delimiter_length(cls, value: str) -> str:
        if len(value) != 1 or value in "\r\n\x00":
            raise ValueError("delimiter must be one non-newline character")
        return value


class Request(StrictModel):
    operation: Literal["inspect", "nodes", "preview", "profile", "diff", "validate"]
    path: str
    version_id: str | None = Field(default=None, max_length=128)
    other: str | None = None
    node: str = "/"
    selection: str = ""
    columns: list[str] = Field(default_factory=list, max_length=1000)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=64, ge=1, le=1000)
    max_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    timeout: float = Field(default=30, ge=1, le=300)
    keys: list[str] = Field(default_factory=list, max_length=32)
    position: bool = False
    atol: float = Field(default=0, ge=0)
    rtol: float = Field(default=0, ge=0)
    nan_equal: bool = False
    null_equal: bool = True
    convert_units: bool = False
    contract: str | None = None
    csv: CSVPolicy = Field(default_factory=CSVPolicy)

    @field_validator("path", "other", "contract")
    @classmethod
    def bounded_path(cls, value: str | None) -> str | None:
        if value is not None and (not value or len(value) > 8192 or "\x00" in value):
            raise ValueError("invalid path")
        return value


def wire(value: Any) -> Any:
    """Encode nonfinite numbers, bytes and integers outside JS's safe range."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return {"$int": str(value)} if abs(value) > 2**53 - 1 else value
    if isinstance(value, float):
        if not math.isfinite(value):
            return {
                "$float": "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
            }
        return value
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (date, datetime)):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        return {str(k): wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if hasattr(value, "tolist"):
        return wire(value.tolist())
    raise MarsdError(
        "UNSUPPORTED_OPERATION", f"Cannot serialize value of type {type(value).__name__}."
    )


def dumps(value: Any) -> str:
    return json.dumps(
        wire(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def outcome_exit(result: dict[str, Any]) -> int:
    return {"FAIL": 1, "DIFFERENT": 1, "ERROR": 3, "INCONCLUSIVE": 4}.get(
        result.get("outcome", ""), 0
    )


def unwire(value: Any) -> Any:
    """Decode tagged numeric evidence when comparing persisted fingerprints."""
    if isinstance(value, dict):
        if set(value) == {"$int"}:
            return int(value["$int"])
        return {k: unwire(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwire(v) for v in value]
    return value
