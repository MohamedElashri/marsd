# Using Marsd

[Back to README](../README.md)

## Projects and source files

Create a project with an explicitly approved source root, register files by reference, then open the workspace:

```sh
uv run marsd project create ./study --source-root ./measurements
uv run marsd project register ./measurements/sample.h5 --project ./study
uv run marsd open ./study
```

Use existing local directories for source roots. Network shares and actively synchronized cloud folders are unsupported. One writer owns a project; stop the browser server, or close the desktop project in Settings, before running writing CLI commands. Read-only lists can coexist with the UI.

Project metadata lives in `.marsd`. Source files stay in their original locations. File identity is explicit: stat-based observations are weak, and `project register --sha256` opts into a full content hash. Changed or missing files are labeled rather than silently replacing a selected version.

## Inspect, compare and validate

The synthetic project from the [quick start](../README.md) includes independent expected outcomes:

```sh
uv run marsd nodes ./demo/data/run_042.h5 --project ./demo --node /measurement
uv run marsd preview ./demo/data/run_042.h5 --project ./demo --node /measurement/transmission --select 0:8,0:8 --json
uv run marsd diff ./demo/data/run_041.parquet ./demo/data/run_042.parquet --project ./demo --keys device_id,wavelength_nm --json
uv run marsd validate ./demo/data/run_042.parquet --project ./demo --contract ./demo/contracts/measurement.yaml --json
uv run marsd reports list --project ./demo
uv run marsd reports export REPORT_ID --project ./demo --format html
```

The comparison and `run_042` validation intentionally exit with code 1. Reports are versioned JSON or escaped standalone HTML, saved under `.marsd/reports`.

| Format | Supported reads | Important limits |
|---|---|---|
| HDF5 | Hierarchy, metadata and numeric/bool scalar, 1-D or 2-D selections | Unsupported dtypes can be metadata-only. External links/storage, virtual datasets and filter plugins are excluded. |
| Parquet | Single-file schema, primitive-column preview/profile/compare | No partitioned directories; nested value operations can be unsupported. |
| CSV | UTF-8/BOM, explicit delimiter/header/null policy and projected streaming reads | Default strings preserve leading zeros. Typed parsing needs an explicit schema or contract. |

Tables return at most 1,000 rows and plots at most 10,000 points/cells. Requested scope, visited work, truncation and source consistency remain visible. Comparisons require explicit unique non-null keys or deliberate positional alignment; arrays require compatible paths/shapes and an explicit selection. Integer comparisons preserve precision beyond 2^53. Unit conversion is opt-in and recorded.

Contracts support required fields/paths, dtype, shape, nullability, numeric range, unit compatibility, uniqueness and membership in an explicit local reference set. No executable expressions or remote references are accepted. Print the complete schema with `uv run marsd contract-schema`.

`PASS` requires complete requested coverage. Known violations are `FAIL`; insufficient evidence is `INCONCLUSIVE`; operational failures are `ERROR`. Samples cannot establish whole-file validity. In comparisons, one witnessed difference establishes `DIFFERENT`, while incomplete counts remain labeled.

## Record trusted Python work

```sh
uv run marsd run --project ./demo --input ./demo/data/identifiers.csv --output ./demo/outputs/analysis.csv -- python ./demo/pipeline/analyze.py
uv run marsd runs list --project ./demo
uv run marsd runs show RUN_ID --project ./demo
uv run marsd runs diff RUN_A RUN_B --project ./demo --json
uv run marsd runs replay RUN_ID --project ./demo --dry-run
uv run marsd lineage RUN_ID --project ./demo --direction upstream --json
```

The wrapper accepts the current interpreter and one approved `.py` script. It records deliberately declared inputs/outputs and explicit `marsd.sdk` instrumentation. It does not discover all file access or snapshot imported code and environments. Trusted scripts can write files and use the network.

The generated example uses `@instrument`, `input_file` and `output_file`. SDK helpers report scoped operations; ordinary `open()` calls remain unobserved. Output helpers create new files without overwriting existing ones. Failed or interrupted outputs remain possibly partial, including when a script catches an error and exits zero.

Readiness checks never execute/import the target or install dependencies. `READY_WITH_LIMITATIONS`, `NOT_READY` and `UNKNOWN` describe available evidence, not reproducibility. Arguments and process text are omitted by default; opt-in capture is bounded and redaction is best effort.

Lineage uses actual version/run/entity IDs. Observed, declared and explicitly inferred links retain their labels; inferred links require deliberate inclusion. Bounded traversal reports incomplete coverage. Downstream means potentially dependent, not scientifically invalid. Import local declarations with `marsd lineage-import FILE --project PROJECT`; use `marsd impact ENTITY_ID` for downstream queries.

## Backup, move and restore

Stop the project writer first:

```sh
uv run marsd project backup --project ./demo --name snapshot.sqlite
uv run marsd project restore ./demo/.marsd/reports/snapshot.sqlite --project ./demo
uv run marsd project remap-root OLD_ABSOLUTE_ROOT NEW_ABSOLUTE_ROOT --project ./demo
```

Restore and remap preview by default; add `--apply` after review. Restore validates metadata and keeps a recovery snapshot. A trusted legacy backup lacking project identity needs `--allow-legacy`. Move/copy data yourself before remapping; remapping changes references, never source bytes. Foreign-OS roots require explicit local remapping.

Backups contain metadata, not source data. Manual backups/reports live in `.marsd/reports`; automatic migration/recovery snapshots live in `.marsd/backups`. Do not copy a live SQLite file alone. Existing schema 1–3 projects migrate to schema 4 after a backup; newer unknown schemas are refused.

`marsd project cleanup --project PROJECT` removes only owned abandoned metadata temporaries. Uninstalling Marsd preserves project/source/output folders. Deleting a project is a separate action.

## Desktop and offline use

Desktop builds bundle the runtime. The bundled CLI is `marsd-backend` (`marsd-backend.exe` on Windows) under application resources `runtime/backend`. Substitute its full path for both `marsd` and `python` in examples. Scripts can use bundled modules; use the Python distribution in your own environment for other scientific dependencies.

For an offline Python install, export the tested requirements with `uv export --locked --no-dev --no-emit-project -o requirements.txt`. On a connected machine matching the target OS/Python, download binary wheels using `python -m pip download --only-binary=:all: --require-hashes -r requirements.txt -d wheelhouse`. Copy that wheelhouse, requirements and the built Marsd wheel, then install with `uv pip install --offline --no-index --find-links wheelhouse --requirements requirements.txt PATH_TO_MARSD_WHEEL`. A wheelhouse is specific to its platform/interpreter.

Linux desktop needs GTK 3 and WebKitGTK 4.1 runtime packages installed before going offline. Windows builds embed the offline WebView2 installer. Experimental build artifacts are not signed release installers; see [release requirements](development.md).

## Exit codes and troubleshooting

| Exit | Meaning |
|---|---|
| 0 | Complete successful requested outcome |
| 1 | Analytical difference or validation violation |
| 2 | Usage/configuration error |
| 3 | Operational error |
| 4 | Inconclusive evidence |

For `PROJECT_BUSY`, close the active writer. For stale-source errors, deliberately register/select the current observation. For budget errors, reduce the selection or scan scope. For an expired launch code, restart the server. `uv run marsd --help` and command-specific `--help` list all options.
