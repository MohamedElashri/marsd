"""Strict project manifests and safe internal metadata paths."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from marsd.domain import MarsdError, StrictModel


class ProjectManifest(StrictModel):
    id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    name: str = Field(min_length=1, max_length=256)
    schema_version: Literal[1]
    created_at: str = Field(min_length=1, max_length=64)
    source_roots: list[str] = Field(min_length=1, max_length=128)

    @field_validator("source_roots")
    @classmethod
    def absolute_roots(cls, values: list[str]) -> list[str]:
        if any(
            not value
            or "\x00" in value
            or not (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute())
            for value in values
        ):
            raise ValueError("Source roots must be absolute filesystem paths")
        return values


def metadata_file(home: Path, name: str, *, database: bool = False) -> Path:
    path = home / name
    if Path(name).name != name or path.is_symlink() or path.exists() and not path.is_file():
        raise MarsdError(
            "PATH_NOT_ALLOWED", "Project metadata must use regular files inside .marsd."
        )
    if database and path.exists() and path.stat().st_nlink > 1:
        raise MarsdError("PATH_NOT_ALLOWED", "A registry database cannot be a hard-link alias.")
    return path


def metadata_directory(home: Path, name: str) -> Path:
    path = home / name
    if (
        Path(name).name != name
        or path.is_symlink()
        or path.is_junction()
        or not path.resolve().is_relative_to(home.resolve())
    ):
        raise MarsdError("PATH_NOT_ALLOWED", "Metadata directory escapes the workspace.")
    path.mkdir(exist_ok=True)
    return path


def read_project(home: Path) -> dict[str, Any]:
    if home.is_symlink() or home.is_junction():
        raise MarsdError("PATH_NOT_ALLOWED", "Project metadata directory cannot be a link alias.")
    try:
        path = metadata_file(home, "project.json")
        with path.open("rb") as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("Project manifest exceeds 1 MiB")
        raw = json.loads(content)
        if isinstance(raw, dict) and raw.get("schema_version") != 1:
            raise MarsdError("NEWER_SCHEMA", "Project schema is unsupported.")
        return ProjectManifest.model_validate(raw).model_dump()
    except (OSError, ValueError, ValidationError) as error:
        raise MarsdError(
            "INVALID_PROJECT",
            "No valid Marsd project manifest was found.",
            action="Create a project with marsd project create PATH, or recover its original manifest.",
            exit_code=2,
        ) from error


def foreign_path(value: str) -> bool:
    return (
        bool(PureWindowsPath(value).drive)
        if os.name != "nt"
        else PurePosixPath(value).is_absolute() and not PureWindowsPath(value).is_absolute()
    )


def logical_path(value: str) -> PurePosixPath | PureWindowsPath:
    """Interpret historical paths using the originating OS, never the current cwd."""
    return PureWindowsPath(value) if PureWindowsPath(value).drive else PurePosixPath(value)
