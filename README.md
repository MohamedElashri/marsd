<p align="center">
  <img src="desktop/icon.svg" width="72" height="72" alt="Marsd mark">
</p>
<h1 align="center">Marsd · مرصد</h1>
<p align="center"><strong>Scientific data. Clear evidence.</strong></p>
<p align="center">Explore files, check contracts and trace recorded work — on your own machine.</p>
<p align="center">
  <a href="docs/usage.md">Usage</a> ·
  <a href="docs/development.md">Contributing</a> ·
  <a href="LICENSE">Apache-2.0</a>
</p>

Marsd is a local scientific data workbench for **HDF5, Parquet and CSV**. Inspect a bounded selection, compare versions, validate a handoff and follow the evidence behind a recorded Python run. Your files stay where they are; inspection registers them by reference.

**Version 0.1.0 · Python 3.12–3.13 · macOS, Windows and Linux**

The CLI and browser workspace are available from source. Native desktop builds are experimental; release signing and baseline user-machine qualification are still in progress.

## A workspace for investigation

| Capability | What it gives you |
|---|---|
| **Explore** | File hierarchy, schemas, numeric slices, plots and visible coverage limits |
| **Compare** | Deliberate key/position alignment, exact integers, tolerances and optional unit conversion |
| **Validate** | Strict YAML contracts, local reference checks and explainable JSON/HTML reports |
| **Trace** | Trusted Python run recording, nonexecuting readiness checks and evidence-labeled dependencies |

![Marsd Explorer showing a synthetic HDF5 transmission map, its hierarchy and selection metadata](docs/assets/workspace.png)

*The workspace above uses generated, entirely synthetic data.*

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
git clone https://github.com/MohamedElashri/marsd.git
cd marsd
uv sync --locked
uv run marsd fixtures generate --output ./demo --seed 42
uv run marsd open ./demo
```

Open the printed local URL and enter the one-use launch code. Select `run_042.h5` to explore the synthetic measurement array. Node.js is not needed to run the checked-in browser workspace.

Keep the server running while using the UI. Stop it with Ctrl-C before issuing CLI commands that write project metadata. Fixture generation requires a new destination and never replaces an existing project.

## Work from the command line

```sh
uv run marsd preview ./demo/data/run_042.h5 --project ./demo --node /measurement/transmission --select 0:8,0:8 --json
uv run marsd diff ./demo/data/run_041.parquet ./demo/data/run_042.parquet --project ./demo --keys device_id,wavelength_nm --json
uv run marsd validate ./demo/data/run_041.parquet --project ./demo --contract ./demo/contracts/measurement.yaml --json
```

The comparison intentionally finds a difference and exits with code 1. Validation of `run_041.parquet` passes; `run_042.parquet` contains deliberate violations. See the [usage guide](docs/usage.md) for projects, contracts, run recording and recovery.

## Local by design

- Inspection does not copy or modify source data. Metadata and reports live in the chosen project.
- The application works offline after dependencies and platform prerequisites are installed. No account, telemetry or hosted service is required.
- Coverage and uncertainty stay visible. A successful sample does not prove a whole dataset valid; a changed input does not establish why a run failed.
- Explicitly selected scripts and native scientific files must be trusted. Recorded execution is not an OS sandbox, automatic I/O discovery or a reproducibility guarantee.

## Platforms

| Target | Python / browser | Desktop build |
|---|---|---|
| macOS ARM64 | Python 3.12 and 3.13 | `.app` / `.dmg`, macOS 15+ |
| Windows x86_64 | Native Python 3.12 and 3.13 | Per-user NSIS, Windows 11 + WebView2 |
| Linux x86_64 | Python 3.12 and 3.13 | `.deb`, Ubuntu 24.04 + GTK/WebKitGTK |

Desktop builds bundle Python and scientific libraries. Native CI verifies all three build targets; Windows Server runner results do not replace Windows 11 qualification. Other architectures and Linux distributions are not qualified. See [desktop development and release checks](docs/development.md) for prerequisites and current limits.

## Contributing

Start with the [development guide](docs/development.md) for setup, architecture and verification. Keep changes focused, include relevant regression coverage and describe scientific assumptions or compatibility changes. Use synthetic data in examples and bug reports.

## License

[Apache License 2.0](LICENSE). Distributed dependencies retain their own licenses and notices.
