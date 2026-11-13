<p align="center">
  <img src="desktop/icon.svg" width="72" height="72" alt="Marsd mark">
</p>
<h1 align="center">Marsd · مرصد</h1>
<p align="center"><strong>Scientific data. Clear evidence.</strong></p>
<p align="center">Explore files, check contracts and trace recorded work — on your own machine.</p>
<p align="center">
  <a href="https://github.com/MohamedElashri/marsd/releases/latest">Latest release</a> ·
  <a href="docs/usage.md">Usage</a> ·
  <a href="docs/development.md">Contributing</a> ·
  <a href="LICENSE">Apache-2.0</a>
</p>

Marsd is a local scientific data workbench for **HDF5, Parquet and CSV**. Files stay in place; inspection registers them by reference. The browser workspace and cross-platform desktop share the same services and project format.

| Capability | What it gives you |
|---|---|
| **Explore** | Hierarchies, schemas, numeric slices and plots with visible coverage |
| **Compare** | Explicit alignment, exact integers, tolerances and optional unit conversion |
| **Validate** | Strict YAML contracts and explainable JSON/HTML reports |
| **Trace** | Trusted Python run recording, readiness checks and evidence-labeled dependencies |

![Marsd Explorer showing a synthetic HDF5 transmission map, its hierarchy and selection metadata](docs/assets/workspace.png)

*Entirely synthetic data. No account, telemetry or hosted service is required for ordinary use.*

## Get started

Choose a package from the [latest release](https://github.com/MohamedElashri/marsd/releases/latest). GitHub's latest route selects stable releases; experimental prereleases appear under [all releases](https://github.com/MohamedElashri/marsd/releases). Read the release notes for platform requirements and signing status. Desktop builds remain experimental.

To run from source, install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run:

```sh
git clone https://github.com/MohamedElashri/marsd.git
cd marsd
uv sync --locked
uv run marsd fixtures generate --output ./demo --seed 42
uv run marsd open ./demo
```

Open the printed local URL and enter the one-use launch code. Select `run_042.h5` to explore the sample array. The checked-in workspace runs without Node.js. Fixture generation requires a new destination.

See [usage](docs/usage.md) for CLI examples, data limits and recovery, or [development](docs/development.md) for contributing, builds and releases. Inspection is read-only; explicitly recorded scripts must be trusted and are not sandboxed.

## License

[Apache License 2.0](LICENSE). Distributed dependencies retain their own licenses and notices.
