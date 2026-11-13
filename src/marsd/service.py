"""Shared application services for CLI, SDK and local browser API."""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from marsd.domain import MarsdError, Request, dumps
from marsd.jobs import Supervisor
from marsd.registry import Registry, atomic_write
from marsd.reports import render_html

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "INTERRUPTED"}


class Workbench(AbstractContextManager):
    def __init__(self, root: str | Path):
        self.registry = Registry(root)
        try:
            self.jobs = Supervisor(self.registry)
        except BaseException:
            self.registry.close()
            raise

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.jobs.close()
        finally:
            self.registry.close()

    def submit(self, request: Request) -> str:
        request = request.model_copy(deep=True)
        if request.version_id:
            from marsd.runs import artifact_version

            source = artifact_version(self.registry, request.version_id)
            current = self.registry.source(source.get("current_path", source["path"]))
            selected = self.registry.source(request.path)
            if source.get("stale", True) or not current.samefile(selected):
                raise MarsdError(
                    "VERSION_UNAVAILABLE",
                    "This recorded version is stale or belongs to another source. Historical values are not retained.",
                    action="Review the saved evidence or deliberately select the current artifact version.",
                )
            source = {**source, "original_path": source["path"], "path": str(current)}
        else:
            source = self.registry.register(request.path)
        request.path = source["path"]
        inputs = [source]
        if request.other:
            other = self.registry.register(request.other)
            request.other = other["path"]
            inputs.append(other)
        if request.contract:
            request.contract = str(self.registry.source(request.contract))
            from marsd.contracts import load_contract

            self.registry.save_contract(load_contract(request.contract).model_dump())
        return self.jobs.submit(request, inputs)

    def execute(self, request: Request) -> dict[str, Any]:
        job_id = self.submit(request)
        try:
            while True:
                if not self.jobs.thread.is_alive():
                    raise MarsdError(
                        "SUPERVISOR_FAILED", "The job supervisor stopped; restart the project."
                    )
                value = self.jobs.get(job_id)
                if value["state"] in TERMINAL:
                    if value["state"] == "SUCCEEDED":
                        return value["result"]
                    error = value["error"] or {
                        "code": value["state"],
                        "message": "Operation did not complete.",
                    }
                    raise MarsdError(
                        error["code"],
                        error["message"],
                        action=error.get("suggested_action", "Retry the operation."),
                        details=error.get("details"),
                        exit_code=error.get("exit_code", 3),
                    )
                time.sleep(0.05)
        except KeyboardInterrupt:
            self.jobs.cancel(job_id)
            raise

    def export(
        self,
        report_id: str,
        *,
        format: str = "json",
        name: str | None = None,
        overwrite: bool = False,
    ) -> Path:
        if format not in {"json", "html"}:
            raise MarsdError(
                "INVALID_SELECTION", "Export format must be json or html.", exit_code=2
            )
        name = name or f"{report_id}.{format}"
        if Path(name).name != name or not name.endswith(f".{format}"):
            raise MarsdError(
                "PATH_NOT_ALLOWED",
                "Use a simple filename with the selected format extension.",
                exit_code=2,
            )
        destination = self.registry.home / "reports" / name
        # Resolve before write: reject a report-directory symlink escape.
        if (
            not destination.parent.resolve().is_relative_to(self.registry.home.resolve())
            or destination.is_symlink()
        ):
            raise MarsdError("PATH_NOT_ALLOWED", "Export destination escapes the workspace.")
        result = self.registry.report(report_id)
        atomic_write(
            destination,
            render_html(result) if format == "html" else dumps(result),
            overwrite=overwrite,
        )
        return destination
