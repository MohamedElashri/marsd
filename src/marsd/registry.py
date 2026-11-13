"""Project ownership, safe local path resolution and SQLite persistence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any

import portalocker

from marsd.domain import MarsdError, digest, dumps, now, unwire
from marsd.project import (
    ProjectManifest,
    foreign_path,
    logical_path,
    metadata_directory,
    metadata_file,
    read_project,
)

SUPPORTED = {".h5": "HDF5", ".hdf5": "HDF5", ".parquet": "Parquet", ".csv": "CSV"}


def local_path(value: str | Path) -> Path:
    text = str(value)
    if text.startswith(("\\\\", "//")) or "://" in text or "\x00" in text:
        raise MarsdError("PATH_NOT_ALLOWED", "Only approved local filesystem paths are supported.")
    if foreign_path(text):
        raise MarsdError(
            "ROOT_REMAP_REQUIRED",
            "This path belongs to another operating system.",
            action="Use project remap-root to map its original root to a current local directory.",
        )
    p = Path(value).expanduser().resolve()
    if os.name == "nt":
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(p.anchor) == 4:  # type: ignore[attr-defined]
            raise MarsdError("PATH_NOT_ALLOWED", "Network drives are not supported.")
    return p


def fingerprint(path: Path, *, strong: bool = False) -> dict[str, Any]:
    try:
        s = path.stat()
        value: dict[str, Any] = {
            "size": s.st_size,
            "mtime_ns": s.st_mtime_ns,
            "ctime_ns": s.st_ctime_ns,
            "device": s.st_dev,
            "inode": s.st_ino,
        }
        if strong:
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            if fingerprint(path) != value:
                raise MarsdError("FILE_CHANGED_DURING_SCAN", "Source changed while hashing.")
            value["sha256"] = h.hexdigest()
        return value
    except OSError as error:
        raise MarsdError(
            "SOURCE_UNAVAILABLE",
            "The selected source is unavailable.",
            action="Check the source path and read permissions.",
        ) from error


def atomic_write(path: Path, content: str, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise MarsdError(
            "OUTPUT_EXISTS",
            "The destination already exists.",
            action="Choose a new destination or explicitly allow overwrite.",
            exit_code=2,
        )
    temporary = path.with_name(f".marsd-tmp-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Link is atomic and refuses an existing destination (including a race).
            os.link(temporary, path)
            temporary.unlink()
    except FileExistsError as error:
        raise MarsdError("OUTPUT_EXISTS", "Destination already exists.", exit_code=2) from error
    finally:
        temporary.unlink(missing_ok=True)


def create_project(
    root: str | Path, *, name: str | None = None, source_roots: list[str] | None = None
) -> Path:
    root = local_path(root)
    if (root / ".marsd").is_symlink() or (root / ".marsd").is_junction():
        raise MarsdError("PATH_NOT_ALLOWED", "Project metadata directory cannot be a link alias.")
    config = root / ".marsd" / "project.json"
    if config.exists():
        raise MarsdError("PROJECT_EXISTS", "A Marsd project already exists here.", exit_code=2)
    roots = [local_path(p) for p in (source_roots or [str(root)])]
    if any(not p.is_dir() and p != root for p in roots):
        raise MarsdError(
            "SOURCE_UNAVAILABLE", "Source roots must be existing directories.", exit_code=2
        )
    config.parent.mkdir(parents=True, exist_ok=True)
    try:
        config.parent.chmod(0o700)
    except OSError:
        pass  # Windows ACLs follow the user's application-directory permissions.
    document = {
        "id": str(uuid.uuid4()),
        "name": name or root.name,
        "schema_version": 1,
        "created_at": now(),
        "source_roots": [str(p) for p in roots],
    }
    document = ProjectManifest.model_validate(document).model_dump()
    atomic_write(config, dumps(document))

    return root


class Registry:
    def __init__(self, root: str | Path):
        self.root = local_path(root)
        self.home = self.root / ".marsd"
        if self.home.is_symlink() or self.home.is_junction():
            raise MarsdError(
                "PATH_NOT_ALLOWED", "Project metadata directory cannot be a link alias."
            )
        self.project = read_project(self.home)
        for name in ("registry.sqlite", "registry.sqlite-wal", "registry.sqlite-shm"):
            metadata_file(self.home, name, database=True)
        metadata_file(self.home, "owner.lock")
        self.guard = threading.RLock()
        self.lock = portalocker.Lock(
            str(self.home / "owner.lock"),
            timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )
        try:
            self.lock.acquire()
        except portalocker.exceptions.LockException as error:
            raise MarsdError(
                "PROJECT_BUSY",
                "Another Marsd process owns this project.",
                action="Stop its UI or command, then retry.",
            ) from error
        try:
            had_database = (self.home / "registry.sqlite").exists()
            self.db = sqlite3.connect(self.home / "registry.sqlite", check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.create_function("path_casefold", 1, str.casefold, deterministic=True)
            self.db.execute("PRAGMA foreign_keys=ON")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
                raise MarsdError("NEWER_SCHEMA", "Database was written by a newer Marsd version.")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA busy_timeout=2000")
            if 0 < version < 4 and had_database:
                backups = metadata_directory(self.home, "backups")
                if not backups.resolve().is_relative_to(self.home.resolve()):
                    raise MarsdError("PATH_NOT_ALLOWED", "Backup directory escapes the project.")
                with sqlite3.connect(
                    backups / f"schema-v{version}-{uuid.uuid4().hex[:8]}.sqlite"
                ) as backup:
                    self.db.backup(backup)
                    if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise MarsdError(
                            "BACKUP_FAILED", "Pre-migration backup failed integrity checks."
                        )
            for target in range(version + 1, 5):
                script = files("marsd").joinpath(f"migrations/{target:03}.sql").read_text()
                self.db.executescript(
                    "BEGIN IMMEDIATE;\n" + script + f"\nPRAGMA user_version={target};\nCOMMIT;"
                )
            if version < 3:
                with self.db:
                    self.db.execute(
                        "INSERT OR IGNORE INTO project_settings VALUES(?,?)",
                        ("project_id", dumps(self.project["id"])),
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO project_settings VALUES(?,?)",
                        ("source_roots", dumps(self.project["source_roots"])),
                    )
            self.load_settings()
            with self.db:
                self.db.execute(
                    "UPDATE jobs SET state='INTERRUPTED', updated_at=? WHERE state IN ('RUNNING','QUEUED')",
                    (now(),),
                )
            with self.db:
                self.db.execute(
                    "UPDATE runs SET state='INTERRUPTED',finished_at=? WHERE state='RUNNING'",
                    (now(),),
                )
            metadata_directory(self.home, "reports")
        except BaseException as error:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.release()
            if isinstance(error, sqlite3.Error):
                raise MarsdError(
                    "INVALID_REGISTRY",
                    "The project registry could not be opened or migrated.",
                    action="Keep the database and restore an integrity-checked backup.",
                ) from error
            raise

    def load_settings(self) -> None:
        try:
            settings = {
                r["key"]: json.loads(r["value"])
                for r in self.db.execute("SELECT * FROM project_settings")
            }
            if settings.get("project_id") != self.project["id"]:
                raise MarsdError(
                    "PROJECT_ID_MISMATCH", "Registry and manifest belong to different projects."
                )
            self.project = ProjectManifest.model_validate(
                {**self.project, "source_roots": settings["source_roots"]}
            ).model_dump()
        except (ValueError, KeyError) as error:
            raise MarsdError(
                "INVALID_REGISTRY", "Project settings in the registry are invalid."
            ) from error

    def close(self) -> None:
        with self.guard:
            self.db.close()
            self.lock.release()

    def source(self, value: str | Path) -> Path:
        path = local_path(value)
        roots = [local_path(p) for p in self.project["source_roots"] if not foreign_path(p)]
        if not roots:
            raise MarsdError(
                "ROOT_REMAP_REQUIRED",
                "Map the original source roots to this computer before opening data.",
            )
        if not any(path.is_relative_to(p) for p in roots):
            raise MarsdError(
                "PATH_NOT_ALLOWED",
                "Source is outside this project's approved roots.",
                action="Choose a source inside an approved root.",
            )
        if not path.is_file():
            raise MarsdError("SOURCE_UNAVAILABLE", "Select an existing readable file.")
        return path

    def register(self, value: str | Path, *, strong: bool = False) -> dict[str, Any]:
        path = self.source(value)
        media = SUPPORTED.get(path.suffix.lower())
        if not media:
            raise MarsdError(
                "UNSUPPORTED_OPERATION", "Supported source formats are HDF5, Parquet and CSV."
            )
        fp = fingerprint(path, strong=strong)
        strength = "sha256" if strong else "stat_weak"
        with self.guard, self.db:
            artifact = self.db.execute(
                "SELECT * FROM artifacts WHERE uri=?", (str(path),)
            ).fetchone()
            if artifact is None:
                # A case-insensitive filesystem may resolve two spellings to one file.
                # Casefold only narrows candidates; OS identity must confirm the alias.
                # Never merge distinct case-sensitive files or unrelated hard-link names.
                for candidate in self.db.execute(
                    "SELECT * FROM artifacts WHERE path_casefold(uri)=?", (str(path).casefold(),)
                ):
                    try:
                        if Path(candidate["uri"]).samefile(path):
                            artifact = candidate
                            break
                    except OSError:
                        continue
            if artifact is None:
                self.db.execute(
                    "INSERT INTO artifacts VALUES(?,?,?,?)",
                    (str(uuid.uuid4()), str(path), media, now()),
                )
                artifact = self.db.execute(
                    "SELECT * FROM artifacts WHERE uri=?", (str(path),)
                ).fetchone()
            self.db.execute(
                "INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), artifact["id"], dumps(fp), strength, now()),
            )
            version = self.db.execute(
                "SELECT * FROM versions WHERE artifact_id=? AND fingerprint=? AND strength=?",
                (artifact["id"], dumps(fp), strength),
            ).fetchone()
            from marsd.lineage import Graph

            Graph(self).node(version["id"], "artifact_version", Path(artifact["uri"]).name)
            return {
                "id": artifact["id"],
                "version_id": version["id"],
                "path": artifact["uri"],
                "name": Path(artifact["uri"]).name,
                "format": media,
                "fingerprint": fp,
                "identity_strength": strength,
                "source_consistency": "best_effort",
                "captured_at": version["captured_at"],
            }

    def artifacts(self) -> list[dict[str, Any]]:
        with self.guard:
            rows = self.db.execute(
                "SELECT a.*, v.id version_id, v.fingerprint, v.strength FROM artifacts a JOIN versions v ON v.id=(SELECT id FROM versions WHERE artifact_id=a.id ORDER BY rowid DESC LIMIT 1) ORDER BY a.uri"
            ).fetchall()
        result = []
        for r in rows:
            try:
                current = fingerprint(self.source(r["uri"]))
                registered = unwire(json.loads(r["fingerprint"]))
                stale = any(current[k] != registered.get(k) for k in current)
            except MarsdError:
                stale = True
            result.append(
                {
                    "id": r["id"],
                    "version_id": r["version_id"],
                    "path": r["uri"],
                    "name": Path(r["uri"]).name,
                    "format": r["media_type"],
                    "identity_strength": r["strength"],
                    "stale": stale,
                }
            )
        return result

    def job_create(self, job_id: str, operation: str, request: dict[str, Any]) -> None:
        with self.guard, self.db:
            self.db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,NULL,NULL)",
                (job_id, operation, "QUEUED", dumps(request), now(), now()),
            )

    def job_update(self, job_id: str, state: str, *, result: Any = None, error: Any = None) -> None:
        with self.guard, self.db:
            self.db.execute(
                "UPDATE jobs SET state=?,updated_at=?,result=?,error=? WHERE id=?",
                (
                    state,
                    now(),
                    dumps(result) if result is not None else None,
                    dumps(error) if error is not None else None,
                    job_id,
                ),
            )

    def job(self, job_id: str) -> dict[str, Any]:
        with self.guard:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise MarsdError("NOT_FOUND", "Job was not found.", exit_code=2)
        value = dict(row)
        for k in ("request", "result", "error"):
            value[k] = json.loads(value[k]) if value[k] else None
        return value

    def save_report(
        self, job_id: str, result: dict[str, Any], contract: dict[str, Any] | None = None
    ) -> str:
        report_id = str(uuid.uuid4())
        with self.guard, self.db:
            self.db.execute(
                "INSERT INTO reports VALUES(?,?,?,?,?)",
                (report_id, job_id, result["kind"], dumps(result), now()),
            )
            if result["kind"] == "validate":
                from marsd.lineage import Graph

                Graph(self).validation(report_id, result, contract)
        return report_id

    def reports(self) -> list[dict[str, Any]]:
        with self.guard:
            rows = self.db.execute(
                "SELECT id,kind,created_at,result FROM reports ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "created_at": r["created_at"],
                "outcome": json.loads(r["result"]).get("outcome"),
                "inputs": json.loads(r["result"]).get("inputs", []),
            }
            for r in rows
        ]

    def report(self, report_id: str) -> dict[str, Any]:
        with self.guard:
            r = self.db.execute("SELECT result FROM reports WHERE id=?", (report_id,)).fetchone()
        if not r:
            raise MarsdError("NOT_FOUND", "Report was not found.")
        value = json.loads(r[0])
        self.recheck_inputs(value)
        return value

    def recheck_inputs(self, value: dict[str, Any]) -> None:
        for source in value.get("inputs", []):
            try:
                path = self.evidence_path(source["path"])
                source["current_path"] = str(path)
                source["relocated"] = str(path) != source["path"]
                current = fingerprint(self.source(path))
                source["stale"] = any(
                    current[k] != unwire(source["fingerprint"]).get(k) for k in current
                )
            except MarsdError:
                source["stale"] = True

    def evidence_path(self, original: str) -> Path:
        with self.guard:
            mappings = self.db.execute("SELECT * FROM root_mappings").fetchall()
        path = logical_path(original)
        for row in sorted(mappings, key=lambda r: len(r["original_root"]), reverse=True):
            old = logical_path(row["original_root"])
            if type(path) is type(old) and path.is_relative_to(old):
                return local_path(Path(row["current_root"]).joinpath(*path.relative_to(old).parts))
        return local_path(original)

    def save_snapshot(self, result: dict[str, Any]) -> None:
        source = result["inputs"][0]
        descriptor = {
            k: result[k]
            for k in (
                "format",
                "fields",
                "nodes",
                "coverage",
                "parse_policy",
                "schema_completeness",
            )
            if k in result
        }
        with self.guard, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO schema_snapshots VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    source["version_id"],
                    result["software_version"],
                    digest(result["parameters"]),
                    dumps(descriptor),
                    int(result["coverage"]["complete"]),
                    now(),
                ),
            )

    def save_contract(self, document: dict[str, Any]) -> None:
        fingerprint = digest(document)
        with self.guard, self.db:
            existing = self.db.execute(
                "SELECT digest FROM contracts WHERE name=? AND revision=?",
                (document["name"], document["revision"]),
            ).fetchone()
            if existing and existing[0] != fingerprint:
                raise MarsdError(
                    "CONTRACT_REVISION_CONFLICT",
                    "This contract revision already has different content.",
                    action="Increment the contract revision before editing its rules or references.",
                    exit_code=2,
                )
            self.db.execute(
                "INSERT OR IGNORE INTO contracts VALUES(?,?,?,?,?)",
                (fingerprint, document["name"], document["revision"], dumps(document), now()),
            )

    def recent_jobs(self) -> list[dict[str, Any]]:
        with self.guard:
            rows = self.db.execute(
                "SELECT id,operation,state,created_at FROM jobs ORDER BY rowid DESC LIMIT 20"
            ).fetchall()
        return [dict(row) for row in rows]

    def backup(self, name: str) -> Path:
        if Path(name).name != name or not name.endswith(".sqlite"):
            raise MarsdError(
                "INVALID_SELECTION", "Backup name must be a simple .sqlite filename.", exit_code=2
            )
        target = self.home / "reports" / name
        if not target.parent.resolve().is_relative_to(self.home.resolve()) or target.is_symlink():
            raise MarsdError("PATH_NOT_ALLOWED", "Backup destination escapes the workspace.")
        from marsd.maintenance import backup_to

        with self.guard:
            return backup_to(self, target)


class ReadOnlyRegistry(Registry):
    """Short-lived WAL reader; does not acquire ownership or change job states."""

    def __init__(self, root: str | Path):
        self.root = local_path(root)
        self.home = self.root / ".marsd"
        self.project = read_project(self.home)
        for name in ("registry.sqlite", "registry.sqlite-wal", "registry.sqlite-shm"):
            metadata_file(self.home, name, database=True)
        try:
            self.guard = threading.RLock()
            database = (self.home / "registry.sqlite").as_uri() + "?mode=ro"
            self.db = sqlite3.connect(database, uri=True, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
            if self.db.execute("PRAGMA user_version").fetchone()[0] != 4:
                self.db.close()
                raise MarsdError(
                    "SCHEMA_MIGRATION_REQUIRED",
                    "Open the project with a writer to migrate its registry first.",
                )
            self.load_settings()
        except BaseException as error:
            if hasattr(self, "db"):
                self.db.close()
            if not isinstance(error, (OSError, ValueError, sqlite3.Error)):
                raise
            raise MarsdError(
                "INVALID_PROJECT", "No readable Marsd project registry was found.", exit_code=2
            ) from error

    def close(self) -> None:
        self.db.close()
