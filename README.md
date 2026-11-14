<p align="center">
  <img src="desktop/icon.svg" width="72" height="72" alt="Marsd mark">
</p>
<h1 align="center">Marsd · مرصد</h1>
<p align="center"><strong>Scientific data. Clear evidence.</strong></p>
<p align="center">A desktop and local browser app for exploring, comparing and
validating scientific data.</p>
<p align="center">
  <a href="https://github.com/MohamedElashri/marsd/releases/latest">Latest release</a> ·
  <a href="docs/usage.md">Usage</a> ·
  <a href="docs/development.md">Contributing</a> ·
  <a href="CHANGELOG.md">Changelog</a> ·
  <a href="LICENSE">Apache-2.0</a>
</p>

Marsd helps researchers and engineers inspect **HDF5, Parquet and CSV** files,
compare experimental outputs, and check results against explicit data rules.

Use it as **a desktop app or a local browser application**, with a CLI and
Python SDK for scripted workflows. Datasets stay in their original folders;
metadata and findings live in a local project.

![Marsd workbench showing a synthetic HDF5 transmission map, its hierarchy and selection metadata](docs/assets/workspace.png)

*The workbench above shows entirely synthetic data.*

## How it runs

**One application, three interfaces:** all use the same Python engine and local
project format. Desktop and browser display the same graphical workbench; the
CLI runs operations directly without a graphical interface or running server.

| Interface | When to use it | How it runs |
|---|---|---|
| **Desktop app** | Interactive work in a standalone application window. | Available for macOS, Windows and Linux. Python is bundled; Marsd starts and stops its local backend automatically. |
| **Local browser app** | The same interactive workbench from your Python environment. | `marsd open PROJECT` starts a server on `127.0.0.1` and opens your browser. Keep the terminal running. |
| **CLI and Python SDK** | Repeatable checks and scripted workflows. | The CLI inspects, compares and validates data. The SDK adds explicit hooks to Python scripts to record selected inputs, outputs and execution evidence. |

For example, explore a project in the desktop app, close the project, run
validation with the CLI, then reopen it to review the saved report. SDK-recorded
runs are also visible in the workbench. No import or conversion is needed
between interfaces; only one writer can own a project at a time. See
[usage](docs/usage.md) for commands and project ownership.

Everything runs locally. No account, telemetry or hosted service is required for
ordinary use.

## What you can do

- **Look inside a file:** browse HDF5 hierarchies, inspect table schemas, and
  view numeric slices as plots or values.
- **Compare results:** align tables by explicit keys or row position, compare
  array slices, and control numeric tolerances and unit conversion.
- **Check data quality:** apply YAML rules for required fields, types, shapes,
  ranges and units; export findings as JSON or standalone HTML.
- **Trace recorded work:** review selected Python runs, failures and documented
  dependencies between inputs and outputs.

Marsd keeps source identity, scope and coverage with each result, so you can
review the evidence behind a check. Inspection is read-only; recorded Python
scripts must be trusted and are not sandboxed.

## Get started

### Desktop

Download the package for your computer from the
[latest release](https://github.com/MohamedElashri/marsd/releases/latest), or
[all releases](https://github.com/MohamedElashri/marsd/releases) for
experimental prereleases:

| Computer | Package | Install |
|---|---|---|
| macOS, Apple Silicon | `.dmg` | Open the disk image and copy Marsd to Applications. |
| Windows, x86_64 | `-setup.exe` | Run the installer, then launch Marsd. |
| Ubuntu Linux, x86_64 | `.deb` | From the download folder, run `sudo apt install ./marsd-*-linux-x86_64.deb`. |

Python is bundled; no separate Python, Node.js or server command is needed.
Packages are experimental and unsigned/ad-hoc. Check the release notes for
compatibility and signing status, and verify downloads against `SHA256SUMS`.

1. Launch Marsd and choose **Create project…**.
2. Choose a **Project folder** for metadata and reports. If your data is
   elsewhere, expand **Additional source folders** and choose
   **Add source folder** to approve that location. Source files stay in place.
3. Choose **Create project**, then **Add file** to select an HDF5, Parquet or
   CSV file from an approved folder.
4. Open the file in the navigator. For HDF5, select a numeric dataset; use
   **Plot** or **Values** to explore it.

### Browser and CLI with sample data

To try Marsd without your own data, use the synthetic example project. Install
Git and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
git clone https://github.com/MohamedElashri/marsd.git
cd marsd
uv sync --locked
uv run marsd fixtures generate --output ./demo --seed 42
uv run marsd open ./demo
```

Use a new or empty `demo` folder. Node.js and a frontend build are not required.

1. Keep the terminal running. In the browser, enter the one-use launch code
   printed in the terminal. If the browser does not open, use the printed URL.
2. Open `run_042.h5` and select `/measurement/transmission` in the navigator.
   You should see a 64 × 64 heatmap. Switch to **Values** to inspect its
   numbers.

To use the CLI, stop the browser server with Ctrl-C, then inspect the same file:

```sh
uv run marsd inspect ./demo/data/run_042.h5 --project ./demo
```

This prints the file's hierarchy and metadata. You can also open `demo` in the
desktop app through **Open project…** after stopping the browser server.

See [usage](docs/usage.md) for your own files, comparisons, validation, SDK run
recording and recovery; [development](docs/development.md) covers contributing,
builds and releases.

## License

[Apache License 2.0](LICENSE). Distributed dependencies retain their own
licenses and notices.
