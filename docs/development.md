# Development

[Back to README](../README.md)

## Setup

Use Python 3.12 or 3.13 and Node within the range declared in `web/package.json` (22.12 or newer, below 27). Run each command separately; they also work in PowerShell:

```sh
uv sync --locked --extra dev
npm --prefix web ci --ignore-scripts
npm --prefix web run build
```

Static UI assets are checked in so the Python distribution works without Node at runtime. Rebuild them after frontend changes. `uv.lock`, npm and Cargo lockfiles pin tested dependencies. Product versions start at **0.1.0**; SQLite/API schemas and contract revisions have independent compatibility meaning.

## Architecture

| Location | Responsibility |
|---|---|
| `src/marsd/` | Shared Python services, CLI/SDK/API, scientific adapters and persistence |
| `src/marsd/migrations/` | Numbered SQLite migrations with backup/recovery |
| `src/marsd/schemas/` | Generated request/contract/run/lineage schemas |
| `web/` | React/TypeScript workspace and browser tests |
| `desktop/` | Tauri shell, frozen-runtime recipe, icons and native license supplements |
| `scripts/` | Build, licensing, documentation and platform verification |
| `tests/` | Unit, integration, security and controlled performance cases |

CLI, browser and desktop use one service layer and project format. SQLite has one writer per project; long scientific reads run in explicit spawned workers. Source access is read-only through adapters; selected trusted scripts remain outside a sandbox guarantee. FastAPI binds only to loopback and protects read/write data APIs with session, Host and Origin checks.

The native shell loads one packaged UI. Rust retains credentials and proxies bounded same-owner requests; JavaScript receives no general shell/filesystem capability. Native-owner death and control-pipe EOF tear down owned workers and release the project lock.

Interactive requests default to 30 s, at most one active scan/eight waiting jobs, 256 MiB decoded batches and an 800 MiB monitored process-tree memory budget. JSON requests/responses cap at 1 MiB/8 MiB. Cancellation has a two-second cooperative grace and five-second cleanup target. Native libraries can overshoot before monitoring reacts; these are supervised limits, not hard OS isolation.

## Verification

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src/marsd
uv run python scripts/generate_api.py --check
uv run python scripts/check_docs.py
uv run pytest -q -m "not performance"
uv run pytest -q -m performance -s
npm --prefix web run lint
npm --prefix web exec -- playwright install chromium
npm --prefix web run test:e2e
uv build
uv run python scripts/check_wheel.py
uv run python scripts/check_installed.py
```

`generate_api.py --check` also verifies product versions across Python/npm/lock/Cargo/Tauri metadata. `check_docs.py` checks local documentation links. Wheel checks select the exact configured version. `check_installed.py --wheelhouse DIRECTORY` verifies installation without network access as well as offline runtime and source-preserving uninstall.

The browser test uses an isolated synthetic project and blocks external requests. CI runs Python 3.12/3.13 on macOS ARM64, Ubuntu x86_64 and native Windows x86_64, including 100-artifact browser startup budgets. Performance cases cover unfavorable HDF5 chunks, physically populated versus logical large arrays, metadata traversal and a 10,000-node/30,000-edge graph. Generated large fixtures are never committed.

[Core checks](https://github.com/MohamedElashri/marsd/actions/workflows/ci.yml) and [native desktop checks](https://github.com/MohamedElashri/marsd/actions/workflows/desktop.yml) retain platform/interpreter evidence, source fingerprints, test reports, license inventories and applicable screenshots/build artifacts. A green runner verifies that environment, not every user-machine configuration.

## Native builds

Build on the target OS; Python runtimes are not cross-compiled. CI uses Rust 1.99.1, portable CPython 3.12.16 build 20261110, and target-specific compilers and packaging tools. The bootstrap script pins and checksums each interpreter archive before executing it.

```sh
python scripts/prepare_desktop_python.py --directory NEW_DIRECTORY
uv sync --locked --all-extras --python INTERPRETER_PATH
uv run python scripts/build_desktop.py
```

Use the interpreter path reported by the first command. Build prerequisites are Xcode command-line tools on macOS, MSVC on Windows, and the GTK/WebKitGTK development stack plus packaging tools on Ubuntu. The exact installation recipe is in `.github/workflows/desktop.yml`.

| Target | Artifact | Runtime baseline |
|---|---|---|
| macOS ARM64 | `.app` / `.dmg` | macOS 15+, system WKWebView |
| Windows x86_64 | Per-user NSIS | Windows 11, WebView2 |
| Linux x86_64 | `.deb` | Ubuntu 24.04, glibc 2.39, GTK 3, WebKitGTK 4.1 |

`build_desktop.py` builds shared assets, freezes the backend, audits macOS deployment minima and native paths, collects notices and invokes Tauri. `--backend-only` freezes just the runtime; `--skip-frontend` assumes assets are already rebuilt. Generated runtime/build/target/schema directories are ignored and excluded from source archives.

Run `scripts/check_desktop_runtime.py --backend ABSOLUTE_EXECUTABLE --output EVIDENCE.json` to verify a copied Unicode/spaces installation with developer tools removed from PATH. Native Rust tests include an explicitly selected frozen lifecycle/proxy integration, using `MARSD_TEST_BACKEND` to identify the executable. CI tests installed Windows WebView2/Linux WebKitGTK through test-only platform drivers, including same-version replacement and project-preserving uninstall. Drivers are not shipped in the application.

Native builds bundle Python/CPython/application notices, JS notices, target Rust inventory and exact-revision supplemental license texts. Missing Rust license declarations or notice files fail packaging. Inventory declarations do not replace a complete native-library/license review.

## Contributing

Keep changes focused on an observed need. Add regression coverage for meaningful behavior changes and run applicable checks. Preserve exact numeric semantics, explicit coverage, typed evidence and source-data safety. Include commands, environment and relevant results in the pull request. Use synthetic fixtures; omit credentials and confidential scientific data.

Keep user documentation concise and current. Update the usage guide when behavior changes and regenerate schemas/assets when required. Runtime data, build outputs, test reports and local planning notes do not belong in Git or source packages.

## Release requirements

Desktop distribution remains experimental. Before a supported release:

- Validate macOS 15 and Windows 11 standard-user/quarantined installs, native chooser/confirmation dialogs, offline prerequisites and existing projects. Rehearse Ubuntu 24.04 desktop/Wayland and offline dependencies. Windows Server/X11 CI is narrower evidence.
- Assign a release maintainer and distribution identity; sign/notarize Mac artifacts and review Windows signing/SmartScreen and Linux package trust.
- Complete independent scientist-user walkthroughs and distribution-license/native-library review; recheck advisories and package-name availability.
- Verify applicable native, recovery, install/removal and performance checks on the release revision. Confirm application replacement/uninstall preserves project/source/output files.

No public release or package-registry publication is implied by successful CI. Automatic updates, additional architectures and replay execution require separate design and qualification.
