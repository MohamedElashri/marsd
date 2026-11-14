# Development and releases

[README](../README.md) · [Usage](usage.md)

## Setup

Python requirements live in [project metadata](../pyproject.toml), Node requirements in [the frontend package](../web/package.json), and native toolchains/runner targets in [desktop CI](../.github/workflows/desktop.yml). Lockfiles and the [runtime bootstrap](../scripts/prepare_desktop_python.py) define tested dependency versions and interpreter checksums.

```sh
uv sync --locked --extra dev
npm --prefix web ci --ignore-scripts
npm --prefix web run build
```

Rebuild and commit static assets after frontend changes. CLI, browser and desktop share the Python service layer and project format. SQLite has one writer per project; bounded scientific reads use spawned workers. Tauri loads the packaged UI and keeps credentials in Rust, exposing a scoped bridge rather than general shell/filesystem access.

## Checks and contributions

```sh
uv run ruff check .
uv run mypy src/marsd
uv run python scripts/generate_api.py --check
uv run python scripts/check_docs.py
uv run pytest -q -m "not performance"
npm --prefix web run lint
npm --prefix web exec -- playwright install chromium
npm --prefix web run test:e2e
```

[Core CI](../.github/workflows/ci.yml) is the full verification/packaging recipe, including controlled performance and clean installed-wheel checks. [Native CI](../.github/workflows/desktop.yml) verifies frozen runtimes, installers and lifecycle on every build target. Reports, inventories, screenshots and source fingerprints are retained as run artifacts. Passing CI verifies its runner environments; it does not certify every user machine.

Use synthetic fixtures and regression tests for behavior changes. Describe assumptions, compatibility impacts and verification. Regenerate changed schemas/assets. Keep user guidance in [usage](usage.md) and link to configuration for requirements. Keep build outputs and local planning notes out of Git and source packages.

## Native builds

Build on the target OS with its native compiler/packaging tools; Python runtimes are not cross-compiled. Follow the prerequisite recipe in desktop CI, then:

```sh
uv run python scripts/prepare_desktop_python.py --directory NEW_DIRECTORY
uv sync --locked --all-extras --python INTERPRETER_PATH
uv run python scripts/build_desktop.py
```

Use the interpreter path reported by the bootstrap. The builder freezes Python/scientific libraries, audits macOS deployment paths/minima, gathers notices and invokes Tauri. Generated directories are ignored. Native test drivers are never shipped; inventories are not a complete native-library SBOM or legal certification.

The Linux GTK dependency tree has an unresolved [GLib iterator safety advisory](https://rustsec.org/advisories/RUSTSEC-2024-0429.html) and an [unmaintained build-time macro dependency](https://rustsec.org/advisories/RUSTSEC-2024-0370.html). No affected iterator references were found outside GLib in the reviewed locked sources; that is not a safety proof. The current stable Tauri stack has no compatible patched GLib upgrade. Reassess before publication; retain experimental status while these constraints remain.

## Actions dependencies

Use the [latest Sanad release](https://github.com/MohamedElashri/sanad/releases/latest). [Sanad policy](../.sanad.toml) and the [native Actions lockfile](../.github/workflows/actions.lock) are authoritative for source approvals and execution pins. After editing workflows:

```sh
sanad sync
sanad sync --write
sanad check --base origin/main
```

Review the preview before writing and use the actual fetched trusted base. Commit every reported managed file; never edit lockfile pins by hand. New source approvals must reach the trusted base before a dependency PR introduces them. External reusable workflows use their defining repository's lockfile; review Sanad's coverage warnings before enabling the updater. Action pins do not cover runner images, runtime downloads or package dependencies.

## Releases

[Prepare release](../.github/workflows/release.yml) runs for a version tag matching project metadata on the default branch. It requires core, native and online Sanad checks, verifies artifact/source hashes, then assembles the tested Python distributions and all desktop targets into an experimental draft. The draft includes a manifest, checksums, notices, inventories and verification evidence. Existing releases are not overwritten and publication is manual.

1. Update [the changelog](../CHANGELOG.md), review the source and checks, then create and push the matching version tag.
2. Inspect the draft's source identity, complete target set, evidence and `SHA256SUMS`.
3. Enable [immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) before publication. Attach everything first; publishing locks the tag/assets.
4. Publish the intended draft, preserving experimental status until supported-release requirements are met. Verify its release attestation/assets and rehearse installation from downloaded packages.

To retry preparation, use the specific candidate tag:

```sh
gh workflow run release.yml --ref TAG
```

After the workflow succeeds, review the complete draft and check that immutable releases are enabled (`enabled: true`):

```sh
gh api 'repos/{owner}/{repo}/immutable-releases'
```

Once that review is complete, publish the experimental draft:

```sh
gh release edit TAG --draft=false --prerelease
```

If upload leaves a partial draft, inspect/delete that draft before retrying, preserving the tag. Never replace published assets or reuse an immutable tag. See [installation](../README.md#get-started) for published releases.

| Optional automation | Required setup |
|---|---|
| Build provenance | Confirm [GitHub attestation entitlement](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations), then set `MARSD_BUILD_ATTESTATIONS=true`. Failure blocks draft creation. |
| Scheduled dependency PRs | Review external-workflow coverage; supply repository-scoped `SANAD_TOKEN` with Contents, Workflows and Pull requests write permissions, then set `SANAD_UPDATES_ENABLED=true`. The built-in token cannot push workflow edits. |

Updater automation stays disabled until configured and never auto-merges. PyPI publication, repository visibility and signing credentials are separate maintainer decisions. Before calling desktop builds supported, complete minimum-OS/offline/native-dialog qualification, signing/notarization/SmartScreen/package-trust review, independent scientist-user walkthroughs and distribution-license review. Keep release-specific requirements and compatibility results in the release notes.
