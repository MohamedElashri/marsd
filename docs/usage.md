# Using Marsd

[README](../README.md) · [Development](development.md)

## Install and open a project

See the [download and source-install instructions](../README.md#get-started). Desktop packages bundle Python; Python installations use the requirements declared in [project metadata](../pyproject.toml). Builds target macOS ARM64, Windows x86_64 and Ubuntu Linux x86_64. Release notes describe compatibility and signing; [desktop configuration](../desktop/src-tauri/tauri.conf.json) declares native runtime dependencies.

Create a project with an existing local source directory:

```sh
uv run marsd project create ./study --source-root ./measurements
uv run marsd project register ./measurements/sample.h5 --project ./study
uv run marsd open ./study
```

One writer owns a project. Stop the browser server with Ctrl-C, or close the desktop project through Project → Close project, before issuing writing CLI commands. Read-only lists can coexist with the UI. Network shares and actively synchronized cloud folders are unsupported.

## Explore, compare and validate

The workbench keeps open files in tabs. Opening a supported file loads bounded structure and a preview; select a dataset in the navigator to change scope. Data, Compare and Validate controls act on that recorded file version. Drag pane dividers or use their arrow keys to resize them. Read options expose explicit slices, paths and scan budgets; the inspector shows source identity and units. Saved reports have their own view.

Using the synthetic project from the README:

```sh
uv run marsd preview ./demo/data/run_042.h5 --project ./demo --node /measurement/transmission --select 0:8,0:8 --json
uv run marsd diff ./demo/data/run_041.parquet ./demo/data/run_042.parquet --project ./demo --keys device_id,wavelength_nm --json
uv run marsd validate ./demo/data/run_042.parquet --project ./demo --contract ./demo/contracts/measurement.yaml --json
uv run marsd reports list --project ./demo
uv run marsd reports export REPORT_ID --project ./demo --format html
```

The comparison and validation intentionally exit with code 1. The other sample table passes the same contract. Reports live under `.marsd/reports`.

HDF5 supports hierarchy/metadata and numeric or boolean slices; excluded links, external storage, virtual datasets and filter plugins are not followed. Parquet supports single files and primitive columns. CSV defaults to strings to preserve leading zeros; typed parsing needs an explicit policy or contract. Unsupported operations and bounded coverage are visible in results.

Comparisons require explicit unique non-null keys or deliberate positional alignment; arrays need compatible paths/shapes and a selection. Integer precision is preserved and unit conversion is opt-in. Contracts use a [closed rule schema](../src/marsd/schemas/contract.json), without executable expressions or remote references. `PASS` requires complete requested coverage; missing evidence is `INCONCLUSIVE`, witnessed violations are `FAIL` and operational failures are `ERROR`. A successful sample cannot prove whole-file validity.

## Record and trace trusted work

```sh
uv run marsd run --project ./demo --input ./demo/data/identifiers.csv --output ./demo/outputs/analysis.csv -- python ./demo/pipeline/analyze.py
uv run marsd runs list --project ./demo
uv run marsd runs diff RUN_A RUN_B --project ./demo --json
uv run marsd runs replay RUN_ID --project ./demo --dry-run
uv run marsd lineage RUN_ID --project ./demo --direction upstream --json
```

The wrapper accepts the current interpreter and an approved Python script. The generated example demonstrates explicit `marsd.sdk` hooks. Scripts can write files and use the network; instrumentation does not discover all I/O or snapshot code/environments. Failed outputs remain possibly partial. Arguments and process text are omitted by default; opt-in capture is bounded and redaction is best effort.

Readiness never executes/imports target code or installs dependencies. It describes available evidence, not reproducibility. Lineage preserves observed, declared and inferred labels; missing or truncated links remain unknown. Downstream means potentially dependent, not scientifically invalid. Use `lineage-import` for explicit declarations and `impact` for downstream queries.

## Preserve and recover data

Source files stay in place; metadata lives in `.marsd`. Stat-based observations are weak; `project register --sha256` opts into a full hash. Changed or missing sources never silently replace a selected version.

Stop the writer before maintenance:

```sh
uv run marsd project backup --project ./demo --name snapshot.sqlite
uv run marsd project restore ./demo/.marsd/reports/snapshot.sqlite --project ./demo
uv run marsd project remap-root OLD_ABSOLUTE_ROOT NEW_ABSOLUTE_ROOT --project ./demo
```

Restore/remap preview by default; add `--apply` after review. Restore keeps a recovery snapshot; trusted legacy backups lacking project identity require `--allow-legacy`. Move/copy data yourself before remapping. Backups contain metadata, not source data. Older projects migrate after a backup; unknown newer schemas are refused. Never copy a live SQLite file alone.

Reports/manual backups live in `.marsd/reports`; automatic backups live in `.marsd/backups`. `project cleanup` removes only abandoned metadata temporaries. Uninstall preserves project/source/output folders; deleting a project is a separate action.

## Desktop, offline use and errors

The bundled CLI is `marsd-backend` (`marsd-backend.exe` on Windows), under application resources `runtime/backend`. Substitute its full path for both `marsd` and `python` in examples. It uses bundled modules; other scientific dependencies need your own Python environment.

For offline Python installation, export locked runtime requirements with `uv export --locked --no-dev --no-emit-project -o requirements.txt`. Download binary wheels on a connected machine matching the target OS/Python using `python -m pip download --only-binary=:all: --require-hashes -r requirements.txt -d wheelhouse`. Copy the requirements, wheelhouse and Marsd wheel, then install with `uv pip install --offline --no-index --find-links wheelhouse --requirements requirements.txt PATH_TO_MARSD_WHEEL`. Desktop system prerequisites must also be installed before going offline.

Exit codes are **0** complete success, **1** analytical difference/violation, **2** usage/configuration, **3** operational error and **4** inconclusive. For `PROJECT_BUSY`, close the writer; for stale-source errors, deliberately select the current observation; for budget errors, reduce scope; for an expired launch code, restart the server. `uv run marsd --help` and command-specific `--help` provide the full command reference.
