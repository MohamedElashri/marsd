"""Deliberately selected trusted Python runs, bounded evidence and nonexecuting readiness."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

import psutil
from pydantic import Field, ValidationError

from marsd import __version__
from marsd.domain import MarsdError, StrictModel, dumps, now, unwire
from marsd.lineage import Graph
from marsd.project import foreign_path
from marsd.registry import Registry, fingerprint, local_path

EVENT_BYTES = 16 * 1024
EVENT_LIMIT = 512
CAPTURE_BYTES = 64 * 1024


class RunSpec(StrictModel):
    script: str = Field(min_length=1, max_length=8192)
    arguments: list[str] = Field(default_factory=list, max_length=128)
    inputs: list[str] = Field(default_factory=list, max_length=128)
    outputs: list[str] = Field(default_factory=list, max_length=128)
    config: str | None = Field(default=None, max_length=8192)
    strong_inputs: bool = False
    capture_text: bool = False
    capture_arguments: bool = False
    timeout: float = Field(default=60, ge=0.1, le=300)


class Event(StrictModel):
    event_id: str = Field(min_length=1, max_length=128)
    kind: Literal["stage_start", "stage_finish", "stage_error", "read", "write"]
    name: str = Field(default="", max_length=128)
    path: str | None = Field(default=None, max_length=8192)
    exception_type: str | None = Field(default=None, max_length=128)
    partial: bool = False


def small_identity(path: Path, limit: int) -> dict[str, Any]:
    before = fingerprint(path)
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise MarsdError(
            "BUDGET_EXCEEDED", "Selected code/config file exceeds its identity budget."
        )
    if before != fingerprint(path):
        raise MarsdError("FILE_CHANGED_DURING_SCAN", "Code/config changed while fingerprinting.")
    return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}


def environment_facts() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    for package in ("marsd", "numpy", "h5py", "pyarrow", "pint"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "packages": packages,
    }


def vcs_facts(directory: Path) -> dict[str, Any]:
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(directory)]
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        revision = (
            subprocess.run(
                [*command, "rev-parse", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=True,
                env=environment,
            )
            .stdout.decode()
            .strip()
        )
        dirty = any(
            subprocess.run(
                [
                    *command,
                    "diff",
                    *flags,
                    "--no-ext-diff",
                    "--no-textconv",
                    "--ignore-submodules=all",
                    "--quiet",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                env=environment,
            ).returncode
            != 0
            for flags in ([], ["--cached"])
        )
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise ValueError("Invalid revision")
        return {"revision": revision, "tracked_dirty": dirty, "untracked_files": "not_enumerated"}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"revision": None, "tracked_dirty": None, "status": "unavailable"}


def redact(text: str) -> str:
    return re.sub(
        r"(?i)((?:password|passwd|token|secret|api[_-]?key|authorization)\s*[=:]\s*)([^\s,;]+)",
        r"\1[REDACTED]",
        text,
    )


def safe_arguments(arguments: list[str]) -> list[str]:
    result = []
    hide_next = False
    for argument in arguments:
        secret = bool(
            re.search(r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)", argument)
        )
        result.append("[REDACTED]" if hide_next else redact(argument)[:1024])
        hide_next = secret and "=" not in argument
    return result


def output_path(registry: Registry, value: str | Path) -> Path:
    path = local_path(value)
    roots = [
        local_path(root) for root in registry.project["source_roots"] if not foreign_path(root)
    ]
    if (
        path.is_relative_to(registry.home.resolve())
        or not any(path.is_relative_to(root) for root in roots)
        or not path.parent.is_dir()
    ):
        raise MarsdError(
            "PATH_NOT_ALLOWED",
            "Declared outputs must have an existing parent inside approved roots, outside .marsd.",
        )
    if path.exists() and not path.is_file():
        raise MarsdError("PATH_NOT_ALLOWED", "Declared output is not a regular file.")
    return path


class RunStore:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.db = registry.db

    def get(self, run_id: str) -> dict[str, Any]:
        with self.registry.guard:
            row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise MarsdError("NOT_FOUND", "Run was not found.")
            document = json.loads(row["document"])
            document.update(state=row["state"], finished_at=row["finished_at"])
            document["events"] = [
                json.loads(r[0])
                for r in self.db.execute(
                    "SELECT document FROM run_events WHERE run_id=? ORDER BY rowid LIMIT ?",
                    (run_id, EVENT_LIMIT),
                )
            ]
        for ref in document.get("references", []):
            if ref.get("version"):
                self.registry.recheck_inputs({"inputs": [ref["version"]]})
        if document["state"] == "INTERRUPTED":
            document["limitation"] = (
                "Owner stopped without terminal evidence. Outputs are unverified/possibly partial."
            )
        return document

    def list(
        self, *, version_id: str | None = None, offset: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        if not 0 <= offset <= 100_000 or not 1 <= limit <= 100:
            raise MarsdError("INVALID_REQUEST", "Run page is outside its budget.")
        query = "SELECT id,state,started_at,finished_at,json_extract(document,'$.code.path') AS script FROM runs"
        arguments: list[Any] = []
        if version_id:
            query += " WHERE id IN (SELECT run_id FROM run_refs WHERE version_id=?)"
            arguments.append(version_id)
        query += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
        with self.registry.guard:
            rows = self.db.execute(query, [*arguments, limit, offset]).fetchall()
        return [
            {
                "id": r["id"],
                "state": r["state"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "script": r["script"],
            }
            for r in rows
        ]

    def compare(self, left: str, right: str) -> dict[str, Any]:
        a, b = self.get(left), self.get(right)
        fields = [
            "state",
            "exit_code",
            "code",
            "config",
            "environment",
            "arguments",
            "argument_count",
            "references",
        ]
        changes = [
            {"field": key, "a": a.get(key), "b": b.get(key)}
            for key in fields
            if a.get(key) != b.get(key)
        ]
        return {
            "schema_version": 1,
            "a": left,
            "b": right,
            "changes": changes,
            "limitation": "Changed evidence is not causal attribution. Omitted arguments, imports and undeclared file access remain unknown.",
        }

    def readiness(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        blockers: list[str] = []
        unknowns: list[str] = []
        checks = []
        for kind, limit in (("code", 4 * 1024 * 1024), ("config", 1024 * 1024)):
            recorded = run.get(kind)
            if not recorded:
                continue
            try:
                path = self.registry.source(self.registry.evidence_path(recorded["path"]))
                current = small_identity(path, limit)
                same = current["sha256"] == recorded["sha256"]
            except (MarsdError, OSError):
                same = False
            checks.append({"kind": kind, "matches": same})
            if not same:
                blockers.append(f"{kind}_missing_or_changed")
        inputs = [r for r in run.get("references", []) if r["role"] == "input"]
        for ref in inputs:
            source = ref.get("version")
            if not source or source.get("stale", True):
                blockers.append("input_missing_or_changed")
            elif source["identity_strength"] != "sha256":
                unknowns.append("input_identity_is_weak")
            else:
                try:
                    path = self.registry.source(self.registry.evidence_path(source["path"]))
                    if path.stat().st_size > 256 * 1024**2:
                        unknowns.append("input_hash_recheck_exceeds_256MiB_budget")
                        continue
                    if fingerprint(path, strong=True)["sha256"] != source["fingerprint"]["sha256"]:
                        blockers.append("input_hash_changed")
                except MarsdError:
                    blockers.append("input_missing_or_changed")
        if run.get("argument_count") and run.get("arguments") is None:
            unknowns.append("arguments_not_retained")
        elif any("[REDACTED]" in argument for argument in run.get("arguments") or []):
            unknowns.append("arguments_redacted")
        if environment_facts() != run.get("environment"):
            blockers.append("recorded_environment_differs")
        if run["state"] != "SUCCEEDED":
            unknowns.append("recorded_run_did_not_succeed")
        if run.get("rejected_events") or run.get("unclosed_stages"):
            unknowns.append("instrumentation_evidence_is_incomplete")
        if any(ref["role"] == "output" and ref.get("partial") for ref in run.get("references", [])):
            unknowns.append("partial_or_missing_output_evidence")
        if not inputs:
            unknowns.append("no_declared_or_instrumented_inputs")
        return {
            "schema_version": 1,
            "run_id": run_id,
            "status": "NOT_READY"
            if blockers
            else "UNKNOWN"
            if unknowns
            else "READY_WITH_LIMITATIONS",
            "blockers": sorted(set(blockers)),
            "unknowns": sorted(set(unknowns)),
            "checks": checks,
            "executed": False,
            "limitations": [
                "No target subprocess/import/install is performed.",
                "Only selected script/config, selected package versions and recorded inputs are checked; imported code, environment and unreported dependencies are not snapshots.",
                "Inputs remain mutable; no reproducibility or safe-execution guarantee.",
            ],
        }


class Recorder:
    def __init__(self, registry: Registry, spec: RunSpec):
        self.registry, self.spec = registry, spec
        self.id = str(uuid.uuid4())
        self.graph = Graph(registry)
        self.count = 0
        self.event_bytes = 0
        self.failed_output_versions: set[str] = set()
        self.started = time.monotonic()
        script = registry.source(spec.script)
        if (
            script.suffix.lower() != ".py"
            or sum(len(a) for a in spec.arguments) > 16 * 1024
            or any("\x00" in a for a in spec.arguments)
        ):
            raise MarsdError(
                "INVALID_REQUEST", "Select a Python script and at most 16 KiB of arguments."
            )
        self.script = script
        self.outputs = [output_path(registry, p) for p in spec.outputs]
        self.output_before = {str(p): fingerprint(p) if p.is_file() else None for p in self.outputs}
        self.document: dict[str, Any] = {
            "schema_version": 1,
            "id": self.id,
            "state": "RUNNING",
            "started_at": now(),
            "software_version": __version__,
            "code": {**small_identity(script, 4 * 1024 * 1024), "vcs": vcs_facts(script.parent)},
            "config": small_identity(registry.source(spec.config), 1024 * 1024)
            if spec.config
            else None,
            "environment": environment_facts(),
            "arguments": safe_arguments(spec.arguments) if spec.capture_arguments else None,
            "argument_count": len(spec.arguments),
            "text_capture": "opt_in_redacted" if spec.capture_text else "off",
            "references": [],
            "declared_outputs": [str(p) for p in self.outputs],
            "rejected_events": 0,
            "event_limit": EVENT_LIMIT,
            "limitation": "Trusted code can write files/use the network. SDK evidence is reported by explicit wrappers; no automatic I/O discovery, sandbox or complete secret-removal guarantee.",
        }
        input_sources = [registry.register(path, strong=spec.strong_inputs) for path in spec.inputs]
        if len(dumps(self.document).encode()) > 2 * 1024**2:
            raise MarsdError("BUDGET_EXCEEDED", "Run metadata exceeds 2 MiB before launch.")
        with registry.guard, registry.db:
            if registry.db.execute("SELECT 1 FROM runs WHERE state='RUNNING'").fetchone():
                raise MarsdError("PROJECT_BUSY", "Another run is active.")
            registry.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,NULL)",
                (self.id, "RUNNING", dumps(self.document), self.document["started_at"]),
            )
            self.graph.node(self.id, "run", script.name, {"run_id": self.id})
            for source in input_sources:
                self.reference(source["path"], "input", "declared", partial=False, source=source)
            self.persist()

    def persist(self) -> None:
        self.registry.db.execute(
            "UPDATE runs SET state=?,document=?,finished_at=? WHERE id=?",
            (
                self.document["state"],
                dumps(self.document),
                self.document.get("finished_at"),
                self.id,
            ),
        )

    def reference(
        self,
        path: str | Path,
        role: str,
        evidence: str,
        *,
        partial: bool,
        source: dict[str, Any] | None = None,
    ) -> str:
        if role == "output":
            path = output_path(self.registry, path)
        if source is None:
            source = self.registry.register(
                path, strong=self.spec.strong_inputs if role == "input" else False
            )
        key = (role, evidence, source["version_id"])
        if any(
            (r["role"], r["evidence"], r.get("version", {}).get("version_id")) == key
            for r in self.document["references"]
        ):
            return source["version_id"]
        self.document["references"].append(
            {"role": role, "evidence": evidence, "partial": partial, "version": source}
        )
        if len(dumps(self.document).encode()) > 2 * 1024**2:
            self.document["references"].pop()
            raise MarsdError("BUDGET_EXCEEDED", "Run reference metadata exceeds 2 MiB.")
        self.registry.db.execute(
            "INSERT OR IGNORE INTO run_refs VALUES(?,?,?,?,?)",
            (self.id, source["version_id"], role, evidence, int(partial)),
        )
        self.graph.edge(
            source["version_id"] if role == "input" else self.id,
            self.id if role == "input" else source["version_id"],
            "used" if role == "input" else "generated",
            evidence,
            self.id,
        )
        return source["version_id"]

    def event(self, body: dict[str, Any]) -> bool:
        try:
            event = Event.model_validate(body)
        except ValidationError:
            self.document["rejected_events"] += 1
            return False
        with self.registry.guard, self.registry.db:
            old = self.registry.db.execute(
                "SELECT document FROM run_events WHERE run_id=? AND event_id=?",
                (self.id, event.event_id),
            ).fetchone()
            if old:
                return json.loads(old[0])["event"] == event.model_dump()
            event_size = len(dumps(event.model_dump()).encode())
            if self.count >= EVENT_LIMIT or self.event_bytes + event_size > 1024**2:
                self.document["rejected_events"] += 1
                return False
            try:
                if event.kind in {"read", "write"}:
                    if not event.path:
                        raise MarsdError("INVALID_EVENT", "File events need a path.")
                    version_id = self.reference(
                        event.path,
                        "input" if event.kind == "read" else "output",
                        "observed",
                        partial=event.kind == "write",
                    )
                    if event.kind == "write" and event.partial:
                        self.failed_output_versions.add(version_id)
                        self.document["partial_output_versions"] = sorted(
                            self.failed_output_versions
                        )
            except (MarsdError, OSError):
                self.document["rejected_events"] += 1
                return False
            self.registry.db.execute(
                "INSERT INTO run_events VALUES(?,?,?)",
                (
                    self.id,
                    event.event_id,
                    dumps(
                        {
                            "event": event.model_dump(),
                            "received_at": now(),
                            "evidence": "sdk_reported",
                        }
                    ),
                ),
            )
            self.count += 1
            self.event_bytes += event_size
            self.persist()
        return True

    def finish(
        self, state: str, *, exit_code: int | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        stages: dict[str, int] = {}
        for row in self.registry.db.execute(
            "SELECT document FROM run_events WHERE run_id=?", (self.id,)
        ):
            event = json.loads(row[0])["event"]
            if event["kind"].startswith("stage_"):
                stages[event["name"]] = stages.get(event["name"], 0) + (
                    1 if event["kind"] == "stage_start" else -1
                )
        self.document["unclosed_stages"] = [name for name, count in stages.items() if count != 0]
        self.document["code_consistency"] = "best_effort"
        for kind, limit in (("code", 4 * 1024**2), ("config", 1024**2)):
            identity = self.document.get(kind)
            if identity:
                try:
                    if (
                        small_identity(self.registry.source(identity["path"]), limit)["sha256"]
                        == identity["sha256"]
                    ):
                        continue
                except (MarsdError, OSError):
                    pass
                self.document["code_consistency"] = "changed_or_unavailable"
                if state == "SUCCEEDED":
                    state, reason = "FAILED", "CODE_OR_CONFIG_CHANGED"
        self.document.update(
            state=state,
            exit_code=exit_code,
            reason=reason,
            finished_at=now(),
            elapsed_seconds=round(time.monotonic() - self.started, 3),
        )
        with self.registry.guard, self.registry.db:
            for path in self.outputs:
                try:
                    if not path.is_file():
                        raise MarsdError("MISSING_OUTPUT", "Declared output is absent.")
                    changed = self.output_before[str(path)] != fingerprint(path)
                    if changed:
                        self.reference(path, "output", "declared", partial=state != "SUCCEEDED")
                    else:
                        self.document["references"].append(
                            {
                                "role": "output",
                                "evidence": "declared",
                                "path": str(path),
                                "status": "unchanged_preexisting_not_attributed",
                                "partial": True,
                            }
                        )
                except (MarsdError, OSError) as error:
                    self.document["references"].append(
                        {
                            "role": "output",
                            "evidence": "declared",
                            "path": str(path),
                            "status": error.code
                            if isinstance(error, MarsdError)
                            else "SOURCE_UNAVAILABLE",
                            "partial": True,
                        }
                    )
            for reference in self.document["references"]:
                if reference["role"] == "output" and reference.get("version"):
                    vid = reference["version"]["version_id"]
                    reference["partial"] = (
                        state != "SUCCEEDED" or vid in self.failed_output_versions
                    )
                    self.registry.db.execute(
                        "UPDATE run_refs SET partial=? WHERE run_id=? AND version_id=? AND role='output'",
                        (int(reference["partial"]), self.id, vid),
                    )
            self.registry.db.execute(
                "UPDATE graph_nodes SET metadata=? WHERE id=?",
                (dumps({"run_id": self.id, "state": state}), self.id),
            )
            self.persist()
        return RunStore(self.registry).get(self.id)


def _stop(process: subprocess.Popen, known: dict[int, psutil.Process]) -> bool:
    try:
        for child in psutil.Process(process.pid).children(recursive=True):
            known.setdefault(child.pid, child)
    except psutil.Error:
        pass
    active = []
    for child in known.values():
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                active.append(child)
                child.terminate()
        except psutil.Error:
            pass
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    _, alive = psutil.wait_procs(active, timeout=1)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=1)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)
    return bool(active)


def record(
    registry: Registry, spec: RunSpec, *, cancel: threading.Event | None = None
) -> dict[str, Any]:
    recorder = Recorder(registry, spec)
    token = secrets.token_urlsafe(32)
    process: subprocess.Popen | None = None
    known: dict[int, psutil.Process] = {}
    captures: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    truncated: dict[str, bool] = {"stdout": False, "stderr": False}
    threads: list[threading.Thread] = []
    state: str = "FAILED"
    reason: str | None = "LAUNCH_FAILED"
    exit_code: int | None = None

    def drain(stream: Any, key: str) -> None:
        try:
            while block := stream.read(4096):
                remaining = CAPTURE_BYTES - len(captures[key])
                captures[key].extend(block[:remaining])
                if len(block) > remaining:
                    truncated[key] = True
        finally:
            stream.close()

    try:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(8)
            server.settimeout(0.02)
            environment = {
                **os.environ,
                "MARSD_RUN_PORT": str(server.getsockname()[1]),
                "MARSD_RUN_TOKEN": token,
                "MARSD_RUN_ROOTS": dumps(registry.project["source_roots"]),
                "MARSD_RUN_HOME": str(registry.home),
                "MARSD_RUN_OWNER": str(os.getpid()),
                "MARSD_RUN_SCRIPT_SHA": recorder.document["code"]["sha256"],
            }
            process = subprocess.Popen(
                [
                    sys.executable,
                    *(
                        ["--marsd-run-child"]
                        if getattr(sys, "frozen", False)
                        else ["-I", "-m", "marsd._run_child"]
                    ),
                    str(recorder.script),
                    *spec.arguments,
                ],
                cwd=registry.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if spec.capture_text else subprocess.DEVNULL,
                stderr=subprocess.PIPE if spec.capture_text else subprocess.DEVNULL,
                start_new_session=os.name != "nt",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0,
            )
            recorder.document["pid"] = process.pid
            with registry.guard, registry.db:
                recorder.persist()
            if spec.capture_text:
                for stream, key in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    thread = threading.Thread(target=drain, args=(stream, key), daemon=True)
                    thread.start()
                    threads.append(thread)
            while True:
                try:
                    owner = psutil.Process(process.pid)
                    for child in owner.children(recursive=True):
                        known.setdefault(child.pid, child)
                    rss = sum(
                        p.memory_info().rss for p in [owner, *known.values()] if p.is_running()
                    )
                    recorder.document["peak_process_tree_rss"] = max(
                        recorder.document.get("peak_process_tree_rss", 0), rss
                    )
                    if rss > 800 * 1024**2:
                        state, reason = "FAILED", "MEMORY_BUDGET_EXCEEDED"
                        break
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error:
                    recorder.document["monitoring_limited"] = True
                if cancel is not None and cancel.is_set():
                    state, reason = "CANCELLED", "USER_CANCELLED"
                    break
                if time.monotonic() - recorder.started > spec.timeout:
                    state, reason = "TIMED_OUT", "WALL_TIME_BUDGET_EXCEEDED"
                    break
                try:
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(0.1)
                        with connection.makefile("rb") as stream:
                            raw = stream.readline(EVENT_BYTES + 1)
                        if len(raw) > EVENT_BYTES:
                            continue
                        body = json.loads(raw)
                        ok = (
                            isinstance(body, dict)
                            and isinstance(body.get("token"), str)
                            and secrets.compare_digest(body.pop("token").encode(), token.encode())
                            and recorder.event(body)
                        )
                        connection.sendall(b"1" if ok else b"0")
                except (OSError, ValueError, RecursionError):
                    pass
                exit_code = process.poll()
                if exit_code is not None:
                    state, reason = (
                        ("SUCCEEDED", None) if exit_code == 0 else ("FAILED", "NONZERO_EXIT")
                    )
                    break
    except KeyboardInterrupt:
        state, reason = "CANCELLED", "USER_CANCELLED"
    except (OSError, ValueError) as error:
        recorder.document["launch_error_type"] = type(error).__name__
    finally:
        if process is not None:
            children_left = _stop(process, known)
            if state == "SUCCEEDED" and children_left:
                state, reason = "FAILED", "DESCENDANTS_LEFT_RUNNING"
            for thread in threads:
                thread.join(timeout=1)
            recorder.document["capture"] = (
                {
                    key: {
                        "excerpt": redact(value.decode("utf-8", errors="replace")),
                        "truncated": truncated[key],
                    }
                    for key, value in captures.items()
                }
                if spec.capture_text
                else None
            )
    return recorder.finish(state, exit_code=exit_code, reason=reason)


def artifact_version(registry: Registry, identifier: str) -> dict[str, Any]:
    with registry.guard:
        row = registry.db.execute(
            "SELECT v.*,a.uri,a.media_type FROM versions v JOIN artifacts a ON a.id=v.artifact_id WHERE v.id=?",
            (identifier,),
        ).fetchone()
    if not row:
        raise MarsdError("NOT_FOUND", "Artifact version was not found.")
    source = {
        "id": row["artifact_id"],
        "version_id": identifier,
        "path": row["uri"],
        "name": Path(row["uri"]).name,
        "format": row["media_type"],
        "fingerprint": unwire(json.loads(row["fingerprint"])),
        "identity_strength": row["strength"],
        "captured_at": row["captured_at"],
    }
    registry.recheck_inputs({"inputs": [source]})
    return source
