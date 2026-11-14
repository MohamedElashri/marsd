<p align="center">
  <img src="desktop/icon.svg" width="72" height="72" alt="Marsd mark">
</p>
<h1 align="center">Marsd · مرصد</h1>
<p align="center"><strong>Scientific data. Clear evidence.</strong></p>
<p align="center">A desktop and local browser app for exploring, comparing and validating scientific data.</p>
<p align="center">
  <a href="https://github.com/MohamedElashri/marsd/releases/latest">Latest release</a> ·
  <a href="docs/usage.md">Usage</a> ·
  <a href="docs/development.md">Contributing</a> ·
  <a href="LICENSE">Apache-2.0</a>
</p>

Marsd helps researchers and engineers inspect **HDF5, Parquet and CSV** files, compare experimental outputs, and check results against explicit data rules.

Use it as **a desktop app or a local browser application**, with a CLI and Python SDK for scripted workflows. Datasets stay in their original folders; metadata and findings live in a local project.

![Marsd workbench showing a synthetic HDF5 transmission map, its hierarchy and selection metadata](docs/assets/workspace.png)

*The workbench above shows entirely synthetic data.*

## How it runs

| Interface | How you use it |
|---|---|
| **Desktop app** | An application window on macOS, Windows or Linux. Python is bundled; Marsd manages its local backend automatically. |
| **Local browser app** | Run `marsd open PROJECT` from a Python installation. It starts a server on `127.0.0.1` and opens the UI in your browser. Keep the terminal running. |
| **CLI and Python SDK** | Inspect, compare and validate from the terminal; instrument selected Python scripts to record inputs, outputs and execution evidence. |

Both graphical interfaces provide the same workbench and project format. No account, telemetry or hosted service is required for ordinary use.

## What you can do

- **Look inside a file:** browse HDF5 hierarchies, inspect table schemas, and view numeric slices as plots or values.
- **Compare results:** align tables by explicit keys or row position, compare array slices, and control numeric tolerances and unit conversion.
- **Check data quality:** apply YAML rules for required fields, types, shapes, ranges and units; export findings as JSON or standalone HTML.
- **Trace recorded work:** review selected Python runs, failures and documented dependencies between inputs and outputs.

Marsd keeps source identity, scope and coverage with each result, so you can review the evidence behind a check. Inspection is read-only; recorded Python scripts must be trusted and are not sandboxed.

## Get started

### Desktop

Install a desktop package from the [latest release](https://github.com/MohamedElashri/marsd/releases/latest). Open Marsd, create or open a project, and add a file. No separate Python installation or server command is needed.

Desktop builds are experimental. Find prereleases under [all releases](https://github.com/MohamedElashri/marsd/releases); GitHub's `latest` route selects stable releases. Check release notes for platform requirements and signing status; verify downloads against `SHA256SUMS`.

### Local browser app

To try the workbench from source, install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run:

```sh
git clone https://github.com/MohamedElashri/marsd.git
cd marsd
uv sync --locked
uv run marsd fixtures generate --output ./demo --seed 42
uv run marsd open ./demo
```

Enter the terminal's one-use launch code and select `run_042.h5` to explore synthetic data. If needed, open the printed local URL manually. Running from source does not require Node.js. Fixture generation requires a new destination.

See [usage](docs/usage.md) for your own data, CLI examples and recovery; [development](docs/development.md) covers contributing, builds and releases.

## License

[Apache License 2.0](LICENSE). Distributed dependencies retain their own licenses and notices.
