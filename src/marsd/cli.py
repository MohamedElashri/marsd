"""Marsd commands. Machine-readable results go to stdout; diagnostics to stderr."""

from __future__ import annotations

import asyncio
import json as json_module
import secrets
import shutil
import socket
import sys
import threading
import webbrowser
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from marsd import __version__
from marsd.domain import CSVPolicy, MarsdError, Request, dumps, outcome_exit
from marsd.registry import ReadOnlyRegistry, Registry, create_project
from marsd.service import Workbench

app = typer.Typer(
    help="Inspect and validate sources; record trusted Python runs and documented lineage. Run code is not sandboxed.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
project_app = typer.Typer(
    help="Create projects, register sources and back up metadata.", no_args_is_help=True
)
fixtures_app = typer.Typer(
    help="Generate entirely synthetic demonstration data.", no_args_is_help=True
)
reports_app = typer.Typer(
    help="List and export stored investigation reports.", no_args_is_help=True
)
runs_app = typer.Typer(
    help="Review recorded execution evidence; replay is readiness only.", no_args_is_help=True
)
app.add_typer(project_app, name="project")
app.add_typer(fixtures_app, name="fixtures")
app.add_typer(reports_app, name="reports")
app.add_typer(runs_app, name="runs")
Project = Annotated[Path, typer.Option("--project", "-p", help="Explicit Marsd project directory.")]
Json = Annotated[
    bool, typer.Option("--json", help="Print versioned JSON only; diagnostics use stderr.")
]


def guarded(function: Any) -> Any:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except MarsdError as error:
            typer.echo(dumps({"error": error.payload()}), err=True)
            raise typer.Exit(error.exit_code) from error
        except ValidationError as error:
            typer.echo(
                dumps(
                    {
                        "error": {
                            "code": "INVALID_REQUEST",
                            "message": "Request fields are invalid.",
                            "details": [
                                {"field": str(e["loc"]), "reason": e["msg"]} for e in error.errors()
                            ],
                        }
                    }
                ),
                err=True,
            )
            raise typer.Exit(2) from error
        except OSError as error:
            typer.echo(
                dumps(
                    {
                        "error": {
                            "code": "IO_ERROR",
                            "message": "A filesystem or local server operation failed.",
                            "details": {"exception_type": type(error).__name__},
                        }
                    }
                ),
                err=True,
            )
            raise typer.Exit(3) from error

    return wrapped


def emit(value: Any, json: bool = True) -> None:
    if json:
        typer.echo(dumps(value))
    else:
        typer.echo(json_module.dumps(value, indent=2, ensure_ascii=False))


def policy(types: list[str], delimiter: str) -> CSVPolicy:
    schema = {}
    for item in types:
        if "=" not in item:
            raise MarsdError(
                "INVALID_REQUEST",
                "CSV types use column=int64, float64, bool or string.",
                exit_code=2,
            )
        key, value = item.split("=", 1)
        schema[key] = value
    return CSVPolicy.model_validate({"delimiter": delimiter, "schema": schema})


def execute(project: Path, request: Request, json: bool) -> None:
    with Workbench(project) as wb:
        result = wb.execute(request)
    emit(result, json)
    raise typer.Exit(outcome_exit(result))


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@project_app.command("create")
@guarded
def project_create(
    path: Path,
    name: str | None = None,
    source_root: Annotated[list[str] | None, typer.Option("--source-root")] = None,
) -> None:
    """Create a local project; existing source files remain untouched."""
    root = create_project(path, name=name, source_roots=source_root)
    with Workbench(root) as wb:
        emit({"project": wb.registry.project, "path": str(root)})


@project_app.command("register")
@guarded
def register(path: Path, project: Project, sha256: bool = False) -> None:
    """Register one supported file by reference; hashing is explicitly opt-in."""
    with Workbench(project) as wb:
        emit(wb.registry.register(path, strong=sha256))


@project_app.command("list")
@guarded
def artifacts(project: Project) -> None:
    """List registered files and detect changed/unavailable sources."""
    reader = ReadOnlyRegistry(project)
    try:
        emit(reader.artifacts())
    finally:
        reader.close()


@project_app.command("backup")
@guarded
def backup(project: Project, name: str = "metadata-backup.sqlite") -> None:
    """Create and integrity-check an online SQLite backup in workspace reports."""
    with Workbench(project) as wb:
        emit({"path": str(wb.registry.backup(name))})


@project_app.command("restore")
@guarded
def project_restore(
    snapshot: Path, project: Project, apply: bool = False, allow_legacy: bool = False
) -> None:
    """Preview restoring a metadata snapshot; --apply preserves a recovery snapshot first."""
    from marsd.maintenance import restore

    registry = Registry(project)
    try:
        emit(restore(registry, snapshot, apply=apply, allow_legacy=allow_legacy))
    finally:
        registry.close()


@project_app.command("remap-root")
@guarded
def project_remap_root(
    original: str, destination: str, project: Project, apply: bool = False
) -> None:
    """Preview relinking an approved root after relocation. Source files are never moved."""
    from marsd.maintenance import remap_root

    registry = Registry(project)
    try:
        emit(remap_root(registry, original, destination, apply=apply))
    finally:
        registry.close()


@project_app.command("cleanup")
@guarded
def project_cleanup(project: Project) -> None:
    """Remove only abandoned Marsd temporary metadata files, keeping sources/reports."""
    from marsd.maintenance import cleanup_temporaries

    registry = Registry(project)
    try:
        emit(cleanup_temporaries(registry))
    finally:
        registry.close()


@fixtures_app.command("generate")
@guarded
def fixtures_generate(output: Annotated[Path, typer.Option()], seed: int = 42) -> None:
    """Generate fictional HDF5, Parquet, CSV, contract and golden cases."""
    from marsd.fixtures import generate

    emit({"project": str(generate(output, seed))})


@app.command("inspect")
@guarded
def inspect_source(path: Path, project: Project, json: Json = False) -> None:
    """Inspect bounded HDF5 hierarchy or a table's schema; no full data scan."""
    execute(project, Request(operation="inspect", path=str(path)), json)


@app.command()
@guarded
def nodes(
    path: Path,
    project: Project,
    json: Json = False,
    node: str = "/",
    offset: int = 0,
    limit: int = 100,
) -> None:
    """Page immediate HDF5 children or table fields without recursive traversal."""
    execute(
        project,
        Request(operation="nodes", path=str(path), node=node, offset=offset, limit=limit),
        json,
    )


@app.command()
@guarded
def preview(
    path: Path,
    project: Project,
    json: Json = False,
    node: str = "/",
    select: str = "",
    columns: str = "",
    offset: int = 0,
    limit: int = 64,
    csv_type: Annotated[list[str] | None, typer.Option("--csv-type")] = None,
    delimiter: str = ",",
) -> None:
    """Preview <=1,000 table rows or a numeric array slice (e.g. --select 0:32,0:32)."""
    execute(
        project,
        Request(
            operation="preview",
            path=str(path),
            node=node,
            selection=select,
            columns=columns.split(",") if columns else [],
            offset=offset,
            limit=limit,
            csv=policy(csv_type or [], delimiter),
        ),
        json,
    )


@app.command()
@guarded
def profile(
    path: Path,
    project: Project,
    json: Json = False,
    node: str = "/",
    select: str = "",
    columns: str = "",
    max_rows: int = 100_000,
    timeout: float = 30,
    csv_type: Annotated[list[str] | None, typer.Option("--csv-type")] = None,
) -> None:
    """Compute bounded profiles, explicitly labeled full or prefix/selection."""
    execute(
        project,
        Request(
            operation="profile",
            path=str(path),
            node=node,
            selection=select,
            columns=columns.split(",") if columns else [],
            max_rows=max_rows,
            timeout=timeout,
            csv=policy(csv_type or [], ","),
        ),
        json,
    )


@app.command()
@guarded
def diff(
    a: Path,
    b: Path,
    project: Project,
    json: Json = False,
    keys: str = "",
    position: bool = False,
    node: str = "/",
    select: str = "",
    atol: float = 0,
    rtol: float = 0,
    nan_equal: bool = False,
    null_unequal: bool = False,
    convert_units: bool = False,
    max_rows: int = 100_000,
    timeout: float = 30,
    csv_type: Annotated[list[str] | None, typer.Option("--csv-type")] = None,
) -> None:
    """Compare by explicit keys/position, or an HDF5 path/selection. Differences exit 1."""
    execute(
        project,
        Request(
            operation="diff",
            path=str(a),
            other=str(b),
            keys=keys.split(",") if keys else [],
            position=position,
            node=node,
            selection=select,
            atol=atol,
            rtol=rtol,
            nan_equal=nan_equal,
            null_equal=not null_unequal,
            convert_units=convert_units,
            max_rows=max_rows,
            timeout=timeout,
            csv=policy(csv_type or [], ","),
        ),
        json,
    )


@app.command()
@guarded
def validate(
    path: Path,
    project: Project,
    contract: Annotated[Path, typer.Option()],
    json: Json = False,
    max_rows: int = 100_000,
    timeout: float = 30,
) -> None:
    """Apply a native YAML contract. FAIL=1, ERROR=3, INCONCLUSIVE=4."""
    execute(
        project,
        Request(
            operation="validate",
            path=str(path),
            contract=str(contract),
            max_rows=max_rows,
            timeout=timeout,
        ),
        json,
    )


@app.command("contract-schema")
def contract_schema() -> None:
    """Print the JSON Schema of the closed native YAML contract format."""
    from marsd.contracts import Contract

    emit(Contract.model_json_schema())


@reports_app.command("list")
@guarded
def reports_list(project: Project) -> None:
    """List up to 100 stored profiles, comparisons and validation receipts."""
    reader = ReadOnlyRegistry(project)
    try:
        emit(reader.reports())
    finally:
        reader.close()


@reports_app.command("export")
@guarded
def report_export(
    report_id: str,
    project: Project,
    format: str = "html",
    name: str | None = None,
    overwrite: bool = False,
) -> None:
    """Write JSON or standalone escaped HTML to the workspace reports directory."""
    with Workbench(project) as wb:
        emit({"path": str(wb.export(report_id, format=format, name=name, overwrite=overwrite))})


@app.command("open")
@guarded
def open_project(
    path: Path, port: Annotated[int, typer.Option(min=0, max=65535)] = 0, no_browser: bool = False
) -> None:
    """Serve the bundled UI on loopback. Keep this process running; Ctrl-C stops workers."""
    import uvicorn

    from marsd.api import create_app

    # Fail before printing a launch URL if ownership or project validation fails.
    with Workbench(path):
        pass
    code = secrets.token_urlsafe(24)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        actual_port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{actual_port}"
        typer.echo(
            f"Marsd: {url}\nLaunch code (paste into the local page): {code}\nKeep this terminal open. Ctrl-C stops the server and its workers."
        )
        application = create_app(path, launch_code=code, port=actual_port)
        if not no_browser:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        config = uvicorn.Config(application, host="127.0.0.1", port=actual_port, access_log=False)
        asyncio.run(uvicorn.Server(config).serve(sockets=[listener]))
    finally:
        listener.close()


@app.command(context_settings={"ignore_unknown_options": True})
@guarded
def run(
    command: Annotated[
        list[str],
        typer.Argument(
            help="After --, specify the current Python interpreter and a trusted .py script."
        ),
    ],
    project: Project,
    input: Annotated[list[str] | None, typer.Option("--input")] = None,
    output: Annotated[list[str] | None, typer.Option("--output")] = None,
    config: str | None = None,
    timeout: float = 60,
    sha256: bool = False,
    capture_text: bool = False,
    capture_arguments: bool = False,
) -> None:
    """Execute explicitly selected trusted Python code. This is not an OS sandbox."""
    from marsd.runs import RunSpec, record

    executable = shutil.which(command[0]) if command else None
    if len(command) < 2 or not executable or not Path(executable).samefile(sys.executable):
        raise MarsdError(
            "INVALID_COMMAND",
            "Use the current Python interpreter followed by an approved .py script.",
            exit_code=2,
        )
    spec = RunSpec(
        script=str(Path(command[1]).resolve()),
        arguments=command[2:],
        inputs=input or [],
        outputs=output or [],
        config=config,
        timeout=timeout,
        strong_inputs=sha256,
        capture_text=capture_text,
        capture_arguments=capture_arguments,
    )
    registry = Registry(project)
    try:
        result = record(registry, spec)
        emit(result)
        raise typer.Exit(0 if result["state"] == "SUCCEEDED" else 3)
    finally:
        registry.close()


@runs_app.command("list")
@guarded
def runs_list(project: Project, version_id: str | None = None, offset: int = 0) -> None:
    from marsd.runs import RunStore

    reader = ReadOnlyRegistry(project)
    try:
        emit(RunStore(reader).list(version_id=version_id, offset=offset))
    finally:
        reader.close()


@runs_app.command("show")
@guarded
def runs_show(run_id: str, project: Project) -> None:
    from marsd.runs import RunStore

    reader = ReadOnlyRegistry(project)
    try:
        emit(RunStore(reader).get(run_id))
    finally:
        reader.close()


@runs_app.command("diff")
@guarded
def runs_diff(a: str, b: str, project: Project, json: Json = True) -> None:
    from marsd.runs import RunStore

    reader = ReadOnlyRegistry(project)
    try:
        emit(RunStore(reader).compare(a, b), json)
    finally:
        reader.close()


@runs_app.command("replay")
@guarded
def runs_replay(run_id: str, project: Project, dry_run: bool = True) -> None:
    """Assess readiness without importing/executing target code or installing anything."""
    from marsd.runs import RunStore

    if not dry_run:
        raise MarsdError(
            "UNSUPPORTED_OPERATION", "Only --dry-run readiness is supported.", exit_code=2
        )
    reader = ReadOnlyRegistry(project)
    try:
        result = RunStore(reader).readiness(run_id)
        emit(result)
        raise typer.Exit(0 if result["status"] == "READY_WITH_LIMITATIONS" else 4)
    finally:
        reader.close()


@app.command("lineage")
@guarded
def lineage_query(
    entity_id: str,
    project: Project,
    direction: str = "upstream",
    depth: int = 1,
    max_nodes: int = 200,
    max_edges: int = 500,
    include_inferred: bool = False,
    json: Json = True,
) -> None:
    """Query recorded dependencies by real version/run/entity ID; never infer from filenames."""
    from marsd.lineage import Graph

    reader = ReadOnlyRegistry(project)
    try:
        emit(
            Graph(reader).query(
                entity_id,
                direction=direction,
                depth=depth,
                max_nodes=max_nodes,
                max_edges=max_edges,
                include_inferred=include_inferred,
            ),
            json,
        )
    finally:
        reader.close()


@app.command("impact")
@guarded
def impact(
    entity_id: str,
    project: Project,
    depth: int = 4,
    max_nodes: int = 200,
    max_edges: int = 500,
    include_inferred: bool = False,
    json: Json = True,
) -> None:
    """Bounded downstream potential dependencies, not proof of scientific invalidity."""
    lineage_query(
        entity_id, project, "downstream", depth, max_nodes, max_edges, include_inferred, json
    )


@app.command("lineage-import")
@guarded
def lineage_import(path: Path, project: Project) -> None:
    """Import bounded, labeled JSON declarations; imported evidence cannot be observed."""
    from marsd.lineage import Graph

    registry = Registry(project)
    try:
        emit(Graph(registry).import_file(path))
    finally:
        registry.close()
