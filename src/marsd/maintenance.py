"""Explicit project recovery and root relocation; scientific source files never move."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marsd.domain import MarsdError, dumps, now
from marsd.project import ProjectManifest, logical_path, metadata_directory

if TYPE_CHECKING:
    from marsd.registry import Registry

SCHEMA = 4
TEMP_LIMIT = 2 * 1024**3


def _temporary(directory: Path) -> Path:
    path = directory / f".marsd-tmp-{uuid.uuid4().hex}.sqlite"
    with path.open("xb"):
        pass
    return path


def _integrity(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    ):
        raise MarsdError("INVALID_BACKUP", "Snapshot integrity or foreign-key checks failed.")


def backup_to(registry: Registry, target: Path) -> Path:
    """Publish a complete backup atomically; never overwrite a preexisting file."""
    if target.parent.resolve() != target.parent or not target.parent.is_relative_to(
        registry.home.resolve()
    ):
        raise MarsdError("PATH_NOT_ALLOWED", "Backup directory escapes the project.")
    if target.exists() or target.is_symlink():
        raise MarsdError("OUTPUT_EXISTS", "Backup destination already exists.", exit_code=2)
    size = (
        registry.db.execute("PRAGMA page_count").fetchone()[0]
        * registry.db.execute("PRAGMA page_size").fetchone()[0]
    )
    if size > TEMP_LIMIT or shutil.disk_usage(target.parent).free < size + 16 * 1024**2:
        raise MarsdError(
            "BUDGET_EXCEEDED", "Insufficient temporary storage for a safe metadata snapshot."
        )
    temporary = _temporary(target.parent)
    try:
        with closing(sqlite3.connect(temporary)) as copy:
            registry.db.backup(copy, pages=256)
            _integrity(copy)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(temporary, target)
        return target
    except FileExistsError as error:
        raise MarsdError(
            "OUTPUT_EXISTS", "Backup destination already exists.", exit_code=2
        ) from error
    except sqlite3.Error as error:
        raise MarsdError(
            "BACKUP_FAILED", "The metadata snapshot could not be completed."
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _idle(registry: Registry) -> None:
    if registry.db.execute("SELECT 1 FROM runs WHERE state='RUNNING' LIMIT 1").fetchone():
        raise MarsdError("PROJECT_BUSY", "Finish the active run before maintenance.")
    if registry.db.execute(
        "SELECT 1 FROM jobs WHERE state IN ('RUNNING','QUEUED') LIMIT 1"
    ).fetchone():
        raise MarsdError(
            "PROJECT_BUSY", "Finish or cancel running jobs before project maintenance."
        )


def remap_root(
    registry: Registry, original_root: str, new_root: str, *, apply: bool = False
) -> dict[str, Any]:
    from marsd.registry import local_path

    old = logical_path(original_root)
    matches = [root for root in registry.project["source_roots"] if logical_path(root) == old]
    new = local_path(new_root)
    if not matches or not new.is_dir():
        raise MarsdError(
            "INVALID_ROOT_MAPPING",
            "Choose an existing approved old root and an existing new local directory.",
            exit_code=2,
        )
    if str(new) == str(old):
        raise MarsdError(
            "INVALID_ROOT_MAPPING", "The original and new roots are the same.", exit_code=2
        )
    with registry.guard:
        _idle(registry)
        rows = registry.db.execute("SELECT id,uri FROM artifacts").fetchall()
        changes: list[dict[str, Any]] = []
        owners = {row["uri"]: row["id"] for row in rows}
        untouched = {
            r["uri"]
            for r in rows
            if not (
                type(logical_path(r["uri"])) is type(old)
                and logical_path(r["uri"]).is_relative_to(old)
            )
        }
        for row in rows:
            path = logical_path(row["uri"])
            if type(path) is not type(old) or not path.is_relative_to(old):
                continue
            destination = new.joinpath(*path.relative_to(old).parts).resolve()
            if not destination.is_relative_to(new) or not destination.is_file():
                raise MarsdError(
                    "SOURCE_UNAVAILABLE",
                    "A mapped source is missing or escapes the new root.",
                    action="Copy or move the data yourself, then retry; Marsd never moves source files.",
                    details={"artifact_id": row["id"]},
                )
            if (
                (str(destination) in owners and owners[str(destination)] != row["id"])
                or str(destination) in untouched
                or any(c["current"] == str(destination) for c in changes)
            ):
                raise MarsdError(
                    "ARTIFACT_COLLISION",
                    "The mapping would merge distinct artifact references.",
                    exit_code=2,
                )
            changes.append({"id": row["id"], "original": row["uri"], "current": str(destination)})
        result = {
            "operation": "remap_root",
            "applied": False,
            "original_root": str(old),
            "new_root": str(new),
            "artifact_count": len(changes),
            "examples": changes[:20],
            "content_identity": "not_asserted; re-register to observe current fingerprints",
        }
        if not apply:
            return result
        recovery = backup_to(
            registry,
            metadata_directory(registry.home, "backups") / f"pre-remap-{uuid.uuid4().hex}.sqlite",
        )
        roots = list(
            dict.fromkeys(
                str(new) if logical_path(root) == old else root
                for root in registry.project["source_roots"]
            )
        )
        aliases = {
            row["original_root"]: row["current_root"]
            for row in registry.db.execute("SELECT * FROM root_mappings")
        }
        for alias, target in list(aliases.items()):
            target_path = logical_path(target)
            if type(target_path) is type(old) and target_path.is_relative_to(old):
                aliases[alias] = str(new.joinpath(*target_path.relative_to(old).parts))
        aliases[str(old)] = str(new)
        aliases = {
            a: b
            for a, b in aliases.items()
            if logical_path(a) != logical_path(b)
            and not any(logical_path(a) == logical_path(root) for root in roots)
        }
        with registry.db:
            for change in changes:
                registry.db.execute(
                    "UPDATE artifacts SET uri=? WHERE id=?", (change["current"], change["id"])
                )
            registry.db.execute(
                "UPDATE project_settings SET value=? WHERE key=?", (dumps(roots), "source_roots")
            )
            registry.db.execute("DELETE FROM root_mappings")
            registry.db.executemany("INSERT INTO root_mappings VALUES(?,?)", aliases.items())
            registry.db.execute(
                "INSERT INTO maintenance_events VALUES(?,?,?,?)",
                (str(uuid.uuid4()), "remap_root", dumps(result), now()),
            )
        registry.load_settings()
        return {**result, "applied": True, "recovery_snapshot": str(recovery)}


def _snapshot_info(registry: Registry, path: Path, *, allow_legacy: bool) -> dict[str, Any]:
    if (
        not path.is_file()
        or path.stat().st_size > TEMP_LIMIT
        or path.samefile(registry.home / "registry.sqlite")
    ):
        raise MarsdError(
            "INVALID_BACKUP", "Choose a separate metadata snapshot within the 2 GiB budget."
        )
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise MarsdError(
            "INVALID_BACKUP",
            "Live WAL databases are not standalone snapshots; use project backup first.",
        )
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            _integrity(source)
            version = source.execute("PRAGMA user_version").fetchone()[0]
            if version not in {1, 2, 3, 4}:
                raise MarsdError("NEWER_SCHEMA", "Snapshot schema is unsupported.")
            expected = {"artifacts", "versions", "contracts", "jobs", "reports"}
            tables = {
                r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not expected <= tables:
                raise MarsdError("INVALID_BACKUP", "This file is not a Marsd metadata snapshot.")
            verified = False
            if version >= 3:
                settings = {
                    r[0]: json.loads(r[1]) for r in source.execute("SELECT * FROM project_settings")
                }
                if settings.get("project_id") != registry.project["id"]:
                    raise MarsdError(
                        "PROJECT_ID_MISMATCH", "Snapshot belongs to a different project."
                    )
                ProjectManifest.model_validate(
                    {**registry.project, "source_roots": settings["source_roots"]}
                )
                verified = True
            elif not allow_legacy:
                raise MarsdError(
                    "LEGACY_BACKUP_IDENTITY",
                    "Legacy snapshots lack project ownership evidence.",
                    action="Use --allow-legacy only for a trusted snapshot from this project.",
                    exit_code=2,
                )
            return {
                "schema_version": version,
                "project_identity": "project_id_matches"
                if verified
                else "explicit_legacy_assertion",
                "artifacts": source.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
                "reports": source.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
            }
    except (sqlite3.Error, ValueError, KeyError) as error:
        raise MarsdError("INVALID_BACKUP", "Snapshot could not be validated.") from error


def restore(
    registry: Registry, snapshot: str | Path, *, apply: bool = False, allow_legacy: bool = False
) -> dict[str, Any]:
    from marsd.registry import local_path

    path = local_path(snapshot)
    with registry.guard:
        _idle(registry)
        info = _snapshot_info(registry, path, allow_legacy=allow_legacy)
        result = {"operation": "restore", "snapshot": str(path), "applied": False, **info}
        if not apply:
            return result
        directory = metadata_directory(registry.home, "backups")
        required = (
            path.stat().st_size
            + registry.db.execute("PRAGMA page_count").fetchone()[0]
            * registry.db.execute("PRAGMA page_size").fetchone()[0]
        )
        if required > TEMP_LIMIT or shutil.disk_usage(directory).free < required + 16 * 1024**2:
            raise MarsdError(
                "BUDGET_EXCEEDED",
                "Insufficient bounded temporary storage for restore and its recovery snapshot.",
            )
        staged = _temporary(directory)
        try:
            with (
                closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source,
                closing(sqlite3.connect(staged)) as prepared,
            ):
                source.backup(prepared, pages=256)
                for version in range(info["schema_version"] + 1, SCHEMA + 1):
                    sql = files("marsd").joinpath(f"migrations/{version:03}.sql").read_text()
                    prepared.executescript(
                        "BEGIN IMMEDIATE;\n" + sql + f"\nPRAGMA user_version={version};\nCOMMIT;"
                    )
                with prepared:
                    prepared.execute(
                        "INSERT OR IGNORE INTO project_settings VALUES(?,?)",
                        ("project_id", dumps(registry.project["id"])),
                    )
                    prepared.execute(
                        "INSERT OR IGNORE INTO project_settings VALUES(?,?)",
                        ("source_roots", dumps(registry.project["source_roots"])),
                    )
                _integrity(prepared)
            # Revalidate staged ownership and schema before touching the live registry.
            _snapshot_info(registry, staged, allow_legacy=False)
            recovery = backup_to(registry, directory / f"pre-restore-{uuid.uuid4().hex}.sqlite")
            with closing(sqlite3.connect(staged.as_uri() + "?mode=ro", uri=True)) as prepared:
                prepared.backup(registry.db, pages=256)
            with registry.db:
                registry.db.execute(
                    "UPDATE runs SET state='INTERRUPTED',finished_at=? WHERE state='RUNNING'",
                    (now(),),
                )
                registry.db.execute(
                    "UPDATE jobs SET state='INTERRUPTED', updated_at=? WHERE state IN ('RUNNING','QUEUED')",
                    (now(),),
                )
                registry.db.execute(
                    "INSERT INTO maintenance_events VALUES(?,?,?,?)",
                    (str(uuid.uuid4()), "restore", dumps(result), now()),
                )
            registry.load_settings()
            return {
                **result,
                "applied": True,
                "recovery_snapshot": str(recovery),
                "restored_schema_version": SCHEMA,
            }
        except sqlite3.Error as error:
            raise MarsdError(
                "RESTORE_FAILED",
                "Restore could not be completed; preserve the pre-restore recovery snapshot.",
                action="Inspect .marsd/backups before retrying.",
            ) from error
        finally:
            staged.unlink(missing_ok=True)


def cleanup_temporaries(registry: Registry) -> dict[str, Any]:
    pattern = re.compile(r"^\.marsd-tmp-[0-9a-f]{32}\.(tmp|sqlite)$")
    with registry.guard:
        _idle(registry)
        directories = [registry.home]
        for name in ("reports", "backups"):
            if (registry.home / name).exists() or (registry.home / name).is_symlink():
                directories.append(metadata_directory(registry.home, name))
        candidates = [
            p
            for directory in directories
            for p in directory.iterdir()
            if pattern.fullmatch(p.name) and not p.is_dir()
        ]
        for path in candidates:
            path.unlink()
        return {
            "removed_temporaries": len(candidates),
            "source_files_removed": 0,
            "reports_removed": 0,
        }
