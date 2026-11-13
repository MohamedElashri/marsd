"""Single-worker spawn supervisor with bounded queues, payloads and process cleanup."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from marsd import __version__
from marsd.domain import MAX_RESPONSE, MarsdError, Request, digest, dumps, now, wire
from marsd.project import foreign_path
from marsd.registry import Registry, fingerprint, local_path


@dataclass
class Pending:
    id: str
    request: dict[str, Any]
    inputs: list[dict[str, Any]]
    roots: list[str]


@dataclass
class Active:
    pending: Pending
    process: Any
    connection: Any
    started: float
    cancel_event: Any = None
    cancelled_at: float | None = None
    baseline_rss: int = 0
    peak_rss: int = 0
    cancel: bool = False
    monitoring_limited: bool = False


def _work(pending: Pending, cancel_event: Any = None) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        raise MarsdError("CANCELLED", "Operation was cancelled.")
    from marsd.adapters import Dataset
    from marsd.contracts import load_contract, validate
    from marsd.explore import compare

    request = Request.model_validate(pending.request)
    paths = (
        [request.path]
        + ([request.other] if request.other else [])
        + ([request.contract] if request.contract else [])
    )
    before = {}
    for value in paths:
        path = local_path(value)
        if not path.is_file() or not any(
            path.is_relative_to(local_path(r)) for r in pending.roots if not foreign_path(r)
        ):
            raise MarsdError(
                "PATH_NOT_ALLOWED", "Source moved outside the approved roots or became unavailable."
            )
        before[str(path)] = fingerprint(path)
    for source in pending.inputs:
        if any(
            before[source["path"]][k] != source["fingerprint"].get(k)
            for k in before[source["path"]]
        ):
            raise MarsdError(
                "FILE_CHANGED_DURING_SCAN", "Source changed after the request was prepared."
            )
    contract = load_contract(request.contract) if request.contract else None
    if contract and Path(request.path).suffix.lower() == ".csv":
        explicit = {
            r.field: r.dtype
            for r in contract.rules
            if r.kind == "dtype" and r.dtype in {"string", "int64", "float64", "bool"}
        }
        explicit.update(request.csv.types)
        request.csv = type(request.csv).model_validate(
            {**request.csv.model_dump(), "types": explicit}
        )
    with Dataset(
        request.path,
        request.csv,
        cancelled=cancel_event.is_set if cancel_event is not None else None,
    ) as data:
        if request.operation == "inspect":
            result = data.inspect()
        elif request.operation == "nodes":
            result = data.list_nodes(request.node, offset=request.offset, limit=request.limit)
        elif request.operation == "preview":
            result = data.preview(request)
        elif request.operation == "profile":
            result = data.profile(request)
        elif request.operation == "diff":
            if not request.other:
                raise MarsdError("INVALID_REQUEST", "Comparison needs a second input.", exit_code=2)
            with Dataset(
                request.other,
                request.csv,
                cancelled=cancel_event.is_set if cancel_event is not None else None,
            ) as other:
                result = compare(data, other, request)
        else:
            if contract is None:
                raise MarsdError(
                    "INVALID_REQUEST", "Validation needs a contract path.", exit_code=2
                )
            result = validate(data, contract, request)
    for value, previous in before.items():
        if fingerprint(Path(value)) != previous:
            raise MarsdError(
                "FILE_CHANGED_DURING_SCAN",
                "Source changed during the operation; result was invalidated.",
            )
    if contract is not None:
        result["_contract_document"] = contract.model_dump()
    parameters = request.model_dump(
        exclude={"path", "other", "contract", "timeout", "version_id"}, by_alias=True
    )
    contract_document = result.pop("_contract_document", None)
    analytical = {
        "analysis": result,
        "parameters": parameters,
        "software_version": __version__,
        "input_evidence": [
            {"sha256": s["fingerprint"]["sha256"]}
            if "sha256" in s["fingerprint"]
            else s["fingerprint"]
            for s in pending.inputs
        ],
    }
    return wire(
        {
            **result,
            "_contract_document": contract_document,
            "kind": request.operation,
            "schema_version": 1,
            "software_version": __version__,
            "created_at": now(),
            "inputs": pending.inputs,
            "parameters": parameters,
            "result_digest": digest(analytical),
            "source_consistency": "best_effort",
        }
    )


def worker_entry(connection: Any, pending: Pending, cancel_event: Any = None) -> None:
    done = threading.Event()
    parent = mp.parent_process()

    def watch_parent() -> None:
        while not done.wait(0.2):
            if parent is not None and not parent.is_alive():
                # Workers own only read handles and no registry writes. Exit if their owner dies.
                os._exit(70)

    threading.Thread(target=watch_parent, name="marsd-parent-watch", daemon=True).start()
    try:
        result = {"result": _work(pending, cancel_event)}
        encoded = dumps(result).encode()
        if len(encoded) > MAX_RESPONSE:
            raise MarsdError(
                "BUDGET_EXCEEDED",
                "Serialized result exceeds 8 MiB.",
                action="Select fewer fields, nodes or values.",
            )
        connection.send_bytes(encoded)
    except MarsdError as error:
        connection.send_bytes(
            dumps({"error": error.payload(), "exit_code": error.exit_code}).encode()
        )
    except BaseException as error:
        # Native/third-party exception text may expose paths or source values.
        connection.send_bytes(
            dumps(
                {
                    "error": {
                        "code": "OPERATION_FAILED",
                        "message": "Source could not be processed.",
                        "details": {"exception_type": type(error).__name__},
                        "suggested_action": "Check the format, parse policy and supported capabilities.",
                        "coverage": None,
                    },
                    "exit_code": 3,
                }
            ).encode()
        )
    finally:
        done.set()
        connection.close()


def terminate_tree(process: Any) -> None:
    try:
        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
        for child in reversed(descendants):
            try:
                child.terminate()
            except (psutil.Error, OSError):
                pass
        if process.is_alive():
            process.terminate()
        _, alive = psutil.wait_procs(descendants, timeout=1)
        for child in alive:
            try:
                child.kill()
            except (psutil.Error, OSError):
                pass
    except (psutil.Error, OSError):
        pass
    process.join(timeout=1)
    if process.is_alive():
        process.kill()
        process.join(timeout=1)


class Supervisor:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.context = mp.get_context("spawn")
        self.lock = threading.RLock()
        self.queue: deque[Pending] = deque()
        self.active: Active | None = None
        self.results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.failures: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.closed = False
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="marsd-job-supervisor", daemon=True)
        self.thread.start()

    def submit(self, request: Request, inputs: list[dict[str, Any]]) -> str:
        with self.lock:
            if self.closed:
                raise MarsdError("SHUTTING_DOWN", "The project is shutting down.")
            if len(self.queue) >= 8:
                raise MarsdError(
                    "QUEUE_FULL",
                    "Eight jobs are already waiting.",
                    action="Cancel a job or wait for completion.",
                )
            job_id = str(uuid.uuid4())
            self.registry.job_create(job_id, request.operation, request.model_dump())
            self.queue.append(
                Pending(job_id, request.model_dump(), inputs, self.registry.project["source_roots"])
            )
            self.wake.set()
            return job_id

    def get(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if job_id in self.failures:
                return self.failures[job_id]
            value = self.registry.job(job_id)
            if job_id in self.results:
                value["result"] = self.results[job_id]
                self.registry.recheck_inputs(value["result"])
            if self.active and self.active.pending.id == job_id:
                value["progress"] = {
                    "stage": "Reading and computing in an isolated worker",
                    "elapsed_seconds": round(time.monotonic() - self.active.started, 1),
                    "peak_worker_rss": self.active.peak_rss,
                }
            return value

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            self.registry.job(job_id)
            for pending in list(self.queue):
                if pending.id == job_id:
                    self.queue.remove(pending)
                    self.registry.job_update(job_id, "CANCELLED")
                    return self.get(job_id)
            if self.active and self.active.pending.id == job_id:
                self.active.cancel = True
                self.active.cancelled_at = self.active.cancelled_at or time.monotonic()
                self.active.cancel_event.set()
            self.wake.set()
            return self.get(job_id)

    def _finish(self, state: str, *, result: Any = None, error: Any = None) -> None:
        active = self.active
        assert active is not None
        try:
            if result is not None:
                document = result.pop("_contract_document", None)
                if document is not None:
                    self.registry.save_contract(document)
                result["execution"] = {
                    "peak_worker_rss": active.peak_rss,
                    "descendant_monitoring": "limited" if active.monitoring_limited else "enabled",
                    "elapsed_seconds": round(time.monotonic() - active.started, 3),
                }
                if active.pending.request["operation"] == "inspect":
                    self.registry.save_snapshot(result)
                if active.pending.request["operation"] in {"diff", "validate", "profile"}:
                    report_id = self.registry.save_report(active.pending.id, result, document)
                    result["report_id"] = report_id
                    persisted = {"report_id": report_id}
                else:
                    persisted = None  # Preview values remain in bounded session memory only.
                self.results[active.pending.id] = result
                while (
                    len(self.results) > 8
                    or sum(len(dumps(v).encode()) for v in self.results.values()) > 32 * 1024 * 1024
                ):
                    self.results.popitem(last=False)
            else:
                persisted = None
            self.registry.job_update(active.pending.id, state, result=persisted, error=error)
        finally:
            active.connection.close()
            terminate_tree(active.process)
            self.active = None

    def _tick(self) -> None:
        if self.active is None and self.queue:
            pending = self.queue.popleft()
            receiver, sender = self.context.Pipe(duplex=False)
            cancel_event = self.context.Event()
            process = self.context.Process(
                target=worker_entry, args=(sender, pending, cancel_event), name="marsd-scan"
            )
            try:
                process.start()
                sender.close()
                self.active = Active(
                    pending, process, receiver, time.monotonic(), cancel_event=cancel_event
                )
                self.registry.job_update(pending.id, "RUNNING")
            except BaseException:
                receiver.close()
                sender.close()
                self.registry.job_update(
                    pending.id,
                    "FAILED",
                    error={
                        "code": "WORKER_START_FAILED",
                        "message": "Could not start the scan worker.",
                        "suggested_action": "Restart Marsd and check the Python installation.",
                        "details": None,
                        "coverage": None,
                    },
                )
            return
        active = self.active
        if not active:
            return
        if active.cancel:
            if (
                active.connection.poll()
                or not active.process.is_alive()
                or time.monotonic() - (active.cancelled_at or active.started) >= 2
            ):
                self._finish("CANCELLED")
            return
        if time.monotonic() - active.started > active.pending.request["timeout"]:
            self._finish(
                "TIMED_OUT",
                error={
                    "code": "TIMEOUT",
                    "message": "The operation exceeded its time budget.",
                    "suggested_action": "Choose a smaller selection or explicitly increase the timeout.",
                    "details": None,
                    "coverage": None,
                },
            )
            return
        try:
            parent = psutil.Process(active.process.pid)
            rss = parent.memory_info().rss
            try:
                rss += sum(p.memory_info().rss for p in parent.children(recursive=True))
            except (psutil.Error, OSError):
                active.monitoring_limited = True
            active.peak_rss = max(active.peak_rss, rss)
            if rss > 800 * 1024 * 1024:
                self._finish(
                    "FAILED",
                    error={
                        "code": "BUDGET_EXCEEDED",
                        "message": "Worker memory exceeded 800 MiB.",
                        "suggested_action": "Choose a smaller selection or a differently chunked source.",
                        "details": None,
                        "coverage": None,
                    },
                )
                return
        except (psutil.Error, OSError):
            pass
        if active.connection.poll():
            try:
                response = json.loads(active.connection.recv_bytes(MAX_RESPONSE + 1024))
                if "error" in response:
                    self._finish(
                        "FAILED",
                        error={**response["error"], "exit_code": response.get("exit_code", 3)},
                    )
                else:
                    self._finish("SUCCEEDED", result=response["result"])
            except (EOFError, OSError, ValueError):
                self._finish(
                    "FAILED",
                    error={
                        "code": "WORKER_CRASHED",
                        "message": "The worker ended without a complete result.",
                        "suggested_action": "Check the source and retry with a smaller selection.",
                        "details": None,
                        "coverage": None,
                    },
                )
        elif not active.process.is_alive():
            self._finish(
                "FAILED",
                error={
                    "code": "WORKER_CRASHED",
                    "message": "The worker exited unexpectedly; no result was committed.",
                    "suggested_action": "Check the source and retry.",
                    "details": None,
                    "coverage": None,
                },
            )

    def _loop(self) -> None:
        while True:
            self.wake.wait(timeout=0.05)
            self.wake.clear()
            with self.lock:
                if self.closed:
                    return
                pending_id = (
                    self.active.pending.id
                    if self.active
                    else self.queue[0].id
                    if self.queue
                    else None
                )
                try:
                    self._tick()
                except Exception as error:
                    if self.active:
                        terminate_tree(self.active.process)
                        self.active.connection.close()
                        self.active = None
                    if pending_id:
                        failure = {
                            "code": error.code
                            if isinstance(error, MarsdError)
                            else "STORAGE_OR_SUPERVISOR_FAILED",
                            "message": error.message
                            if isinstance(error, MarsdError)
                            else "The operation could not be committed; no successful job is claimed.",
                            "details": {"exception_type": type(error).__name__},
                            "suggested_action": "Check free space and registry health, then restart the project.",
                            "coverage": None,
                        }
                        self.results.pop(pending_id, None)
                        self.failures[pending_id] = {
                            "id": pending_id,
                            "state": "FAILED",
                            "error": failure,
                            "result": None,
                        }
                        while len(self.failures) > 16:
                            self.failures.popitem(last=False)
                        try:
                            self.registry.job_update(pending_id, "FAILED", error=failure)
                        except Exception:
                            pass  # Runtime failure stays visible even if the database is unavailable.

    def close(self) -> None:
        with self.lock:
            self.closed = True
            try:
                for pending in self.queue:
                    try:
                        self.registry.job_update(pending.id, "CANCELLED")
                    except Exception:
                        pass  # Startup will mark uncommitted records interrupted.
            finally:
                self.queue.clear()
                self.results.clear()
                self.failures.clear()
                try:
                    if self.active:
                        self._finish("CANCELLED")
                finally:
                    self.wake.set()
        self.thread.join(timeout=5)
