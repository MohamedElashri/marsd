# Releases

[Back to README](../README.md)

GitHub Actions prepares an **experimental draft release** from a version tag. A maintainer reviews and publishes it separately. The first product version is **0.1.0**, tagged **v0.1.0**; verification fixes do not cause automatic version bumps. Repository visibility and PyPI publication are separate decisions.

## Release flow

The design follows [Sanad's release workflow](https://github.com/MohamedElashri/sanad/blob/main/.github/workflows/release.yml): validate versions, build/test every target, stage a draft with checksums/inventories, establish provenance and verify the published release. Marsd uses its native Python/Tauri builders and existing test workflows.

| Step | GitHub Actions behavior |
|---|---|
| Validate | Require a complete matching `vX.Y.Z` tag, exact triggering commit and ancestry on the default branch. Branch dispatches fail before building. |
| Core | Call `ci.yml`: macOS ARM64, Windows x86_64 and Ubuntu x86_64 × Python 3.12/3.13, correctness/security/performance, browser workflow and clean installed-wheel checks. |
| Desktop | Call `desktop.yml`: three native builders, portable CPython, frozen runtime/Rust checks, Windows/Linux installed webviews and copied macOS DMG runtime verification. |
| Dependencies | Call `sanad.yml`: required online verification of the frozen native dependency graph. |
| Assemble | Require all jobs, all six matching source fingerprints and package evidence. Collect the exact tested Python distributions and installers; refuse missing targets, skipped tests, changed bytes or incomplete native notices. |
| Attest | Optionally run GitHub build-provenance attestation when repository entitlement has been confirmed. Failure blocks the draft; there is no silent fallback when requested. |
| Draft | Upload all assets together as an experimental draft/prerelease. An existing release is not overwritten. |
| Review/publish | Maintainer reviews assets, limitations and setup, then publishes an immutable release. Publication is never automatic. |

`release.yml` runs on version-tag pushes. Ordinary branch CI does not also run on tags, avoiding duplicate native builds. Manual retries must target the tag with `--ref`, not the default branch. Per-tag concurrency prevents competing release attempts.

## Assets and evidence

The draft includes:

- `marsd-0.1.0-py3-none-any.whl` and `marsd-0.1.0.tar.gz` from the tested Ubuntu/Python 3.12 cell.
- `marsd-0.1.0-macos-aarch64.dmg`, `marsd-0.1.0-windows-x86_64-setup.exe` and `marsd-0.1.0-linux-x86_64.deb`.
- Versioned manifest, verification evidence, combined dependency inventory and target Rust notices.
- `RELEASE_NOTES.txt` describing experimental status, installation and remaining limits.
- `SHA256SUMS`, covering every attached payload except itself, including the manifest and notes.

The manifest records the tag, source commit/fingerprint, workflow run, native-locking state, requested build attestation and payload hashes. Core reports record hashes after distribution checks; installer/DMG reports identify the tested package hash. Assets are collected from the same workflow run, never fetched from another branch or a pull-request build. Promotion does not rebuild them.

Inventories include declared Python/npm and target Rust dependencies, including build tools. They are **not a complete native-library SBOM**, vulnerability assessment or legal certification. Native-library licenses remain inside the installers. The local ignored planning file is excluded from distributions.

## Sanad dependency policy

The integration uses **Sanad 1.1.0** at its released commit and the reviewed reusable updater revision used by Sanad itself. Native lockfiles pin direct and transitive action dependencies; approved numeric publisher/repository identities follow the source policy used by Sanad. External reusable workflows retain full SHA references.

GitHub native Actions lockfile support has been confirmed for this private repository. The checked-in `.sanad.toml` records that confirmation; `.github/workflows/actions.lock` is generated and online-verified by Sanad. The release workflow requires an online check before collecting artifacts.

For a new repository, confirm native support first, then review and apply a baseline:

```sh
sanad init --confirm-native
sanad init --confirm-native --write
sanad check --base origin/main
```

Commit every reported managed file, including `.sanad.toml`, `.github/workflows/actions.lock` and any normalized workflow references. After future workflow changes, preview `sanad sync`, then use `sanad sync --write` and `sanad check --base origin/main` on the reviewed diff. Use the actual fetched trusted base for checks. New source approvals must reach that base before a dependency PR introduces them. Never hand-edit the native lockfile.

The scheduled updater is disabled by default. To enable it, supply a repository-scoped App/PAT as `SANAD_TOKEN` with Contents, Workflows and Pull requests write permissions, then set repository variable `SANAD_UPDATES_ENABLED=true`. Before enabling it, review the reusable workflow boundary: Sanad currently reports that complete callee lock coverage was not established at the pinned updater revision. The caller does not lock those internal dependencies. It runs Mondays at 03:00 UTC, maintains a reviewed PR and never auto-merges. The built-in `GITHUB_TOKEN` cannot push workflow edits. Use one updater for Actions dependencies.

Sanad locks action identity and transitive action resolution; it does not audit code safety or pin runner images, Cargo/Python/npm packages, runtime downloads or external reusable-workflow internals. External reusable workflows remain SHA-pinned and use the defining repository's lockfile. Keep the existing package locks, interpreter checksums, license and runtime checks.

## Repository setup

1. Configure immutable releases before publishing. Inspect `gh api repos/MohamedElashri/marsd/immutable-releases`; an administrator can enable them with `gh api --method PUT repos/MohamedElashri/marsd/immutable-releases`. A draft remains editable until publication.
2. Set `MARSD_BUILD_ATTESTATIONS=true` only after confirming support. [GitHub build attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations) require Enterprise Cloud for private/internal repositories. The private repository stays private; unset means checksums/evidence without a build-attestation claim. GitHub's immutable **release attestation** is distinct from this optional build-provenance action.
3. Assign signing/release ownership and review native prerequisites. No publishing credential beyond the repository-scoped Actions token is needed to prepare a private draft. No PyPI, Homebrew, Nix or external repository publication is configured.

All workflows start with read-only contents permission. Only the draft job receives release-write/OIDC/attestation permissions. Release code runs on trusted version tags, never `pull_request_target` or an untrusted artifact-triggered workflow. The updater's write credential is isolated in its opt-in workflow.

## Prepare v0.1.0

Review the source and applicable tests on the default branch. Confirm product versions agree with `uv run python scripts/generate_api.py --check`. Then, when a release candidate is intended:

```sh
git tag -a v0.1.0 -m "Marsd 0.1.0"
git push origin v0.1.0
gh run list --workflow release.yml
```

For a failed run before draft creation, fix the underlying cause and choose the appropriate corrected source/version rather than moving a published tag. Retry an unchanged tag using `gh workflow run release.yml --ref v0.1.0`. If upload left a partial draft, inspect/delete that **draft only**, preserving the tag, before retrying. The workflow refuses to overwrite an existing draft or published release. Workflow artifacts expire; a retry rebuilds and verifies a new complete run.

## Review and publication

Inspect the draft assets, manifest and checksums. Confirm version/commit identity, all three native targets, licenses, Sanad coverage and optional attestation status. Download the draft assets and verify `SHA256SUMS` with a trusted SHA-256 implementation.

Experimental distribution must visibly retain unsigned/ad-hoc and qualification limits. Before describing desktop builds as supported, complete macOS 15 and Windows 11 standard-user/quarantined installation and dialogs, Ubuntu 24.04/Wayland and offline prerequisite checks, signing/notarization/SmartScreen/package-trust review, independent scientist-user walkthroughs and full distribution-license review. Runner checks alone do not complete these requirements.

With immutable releases enabled and the experimental draft reviewed, a maintainer can publish it using `gh release edit v0.1.0 --draft=false --prerelease`. [Immutability](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) locks the tag/assets after publication, so attach everything first. If immutable settings are unavailable or still disabled, stop before publication.

Verify what users receive:

```sh
gh api repos/MohamedElashri/marsd/releases/tags/v0.1.0 --jq .immutable
gh release verify v0.1.0 --repo MohamedElashri/marsd
gh release download v0.1.0 --repo MohamedElashri/marsd --dir downloaded-release
gh release verify-asset v0.1.0 downloaded-release/marsd-0.1.0-py3-none-any.whl --repo MohamedElashri/marsd
```

Require `.immutable` to be true and repeat `verify-asset` for each downloaded installer/metadata asset. If build attestations were enabled, also run `gh attestation verify FILE --repo MohamedElashri/marsd`. Rehearse installed wheel/runtime and project-preserving replacement/removal on the declared targets using those downloaded bytes. Failures require a corrected release; never replace published assets or reuse an immutable tag.
