"""Explicit scientific instrumentation; no monkeypatching or automatic file discovery."""

from __future__ import annotations

import inspect
import json
import os
import socket
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any

from marsd.domain import MarsdError, dumps
from marsd.registry import Registry, local_path
from marsd.runs import EVENT_BYTES, Recorder, RunSpec, output_path

_current: ContextVar[Recorder | None] = ContextVar("marsd_capture", default=None)


def _emit(kind: str, **values: Any) -> bool:
    event = {"event_id": str(uuid.uuid4()), "kind": kind, **values}
    current = _current.get()
    if current:
        return current.event(event)
    token, port = os.environ.get("MARSD_RUN_TOKEN"), os.environ.get("MARSD_RUN_PORT")
    if not token or not port:
        return False
    body = (dumps({"token": token, **event}) + "\n").encode()
    if len(body) > EVENT_BYTES:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1) as connection:
            connection.sendall(body)
            return connection.recv(1) == b"1"
    except (OSError, ValueError):
        return False


def instrument(function: Any) -> Any:
    """Report a selected function's start/finish/error type; arguments/results are omitted."""
    if (
        inspect.iscoroutinefunction(function)
        or inspect.isgeneratorfunction(function)
        or inspect.isasyncgenfunction(function)
    ):
        raise MarsdError(
            "UNSUPPORTED_OPERATION",
            "The initial instrument decorator supports synchronous functions only.",
        )

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        _emit("stage_start", name=function.__qualname__[:128])
        try:
            result = function(*args, **kwargs)
        except BaseException as error:
            _emit(
                "stage_error",
                name=function.__qualname__[:128],
                exception_type=type(error).__name__[:128],
            )
            raise
        _emit("stage_finish", name=function.__qualname__[:128])
        return result

    return wrapped


def _authorized(value: str | Path, *, writing: bool) -> Path:
    current = _current.get()
    if current:
        return output_path(current.registry, value) if writing else current.registry.source(value)
    roots = json.loads(os.environ.get("MARSD_RUN_ROOTS", "[]"))
    path = local_path(value)
    home = os.environ.get("MARSD_RUN_HOME")
    if (
        not roots
        or not any(path.is_relative_to(local_path(root)) for root in roots)
        or home
        and path.is_relative_to(local_path(home))
    ):
        raise MarsdError(
            "PATH_NOT_ALLOWED", "SDK file helpers require an active run and approved local roots."
        )
    return path


@contextmanager
def input_file(path: str | Path):
    """Open a binary source and report the successful scoped read helper, not all I/O."""
    source = _authorized(path, writing=False)
    with source.open("rb") as stream:
        yield stream
    _emit("read", path=str(source))


@contextmanager
def output_file(path: str | Path):
    """Create a new binary output without clobbering an existing file."""
    destination = _authorized(path, writing=True)
    opened = False
    completed = False
    try:
        with destination.open("xb") as stream:
            opened = True
            yield stream
        completed = True
    finally:
        if opened and destination.is_file():
            _emit("write", path=str(destination), partial=not completed)


@contextmanager
def capture(
    project: str | Path,
    *,
    script: str,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    strong_inputs: bool = False,
):
    """Standalone capture owns the project lock; a child of `marsd run` uses its owner channel."""
    if _current.get() or os.environ.get("MARSD_RUN_TOKEN"):
        raise MarsdError(
            "CAPTURE_ACTIVE", "Use instrument/input_file/output_file inside an existing capture."
        )
    registry = Registry(project)
    try:
        recorder = Recorder(
            registry,
            RunSpec(
                script=script,
                inputs=inputs or [],
                outputs=outputs or [],
                strong_inputs=strong_inputs,
            ),
        )
        token = _current.set(recorder)
        try:
            yield recorder
        except BaseException as error:
            recorder.document["exception_type"] = type(error).__name__
            recorder.finish(
                "CANCELLED" if isinstance(error, KeyboardInterrupt) else "FAILED",
                reason="SDK_EXCEPTION",
            )
            raise
        else:
            recorder.finish("SUCCEEDED", exit_code=0)
        finally:
            _current.reset(token)
    finally:
        registry.close()
