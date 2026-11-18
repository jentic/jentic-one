# Releasing Jentic One

Operational runbook for cutting a release. The *why* (versioning policy, the
decisions behind this setup) lives in [`VERSIONING.md`](../../VERSIONING.md); this
is the *how*.

## Cutting a release

Releases are automated with [release-please](https://github.com/googleapis/release-please)
(config: [`release-please-config.json`](../../release-please-config.json)):

1. Merge feature/fix PRs to `main` as usual (Conventional Commits, squash-merge).
2. release-please keeps a standing **Release PR** titled `chore(main): release X.Y.Z`.
   Its diff bumps the version in lockstep across `pyproject.toml`, `uv.lock`,
   every Helm `Chart.yaml`, and the hand-curated broker OpenAPI spec
   (`openapi/broker/broker.openapi.yaml`, via its `x-release-please-version`
   markers), and updates `CHANGELOG.md`. The release-please workflow then
   regenerates the derived artifacts on the PR branch (control spec, UI and Go
   CLI clients, and the docs-SPA broker JSON) so the PR passes the drift gates.
   You may edit the changelog directly in that PR (optional).
3. **Merging the Release PR is the release.** release-please then tags `vX.Y.Z`,
   creates the GitHub Release, and (because the tag is pushed with the release
   App token) triggers [`release.yml`](../../.github/workflows/release.yml):
   - **gate** — builds the app, runs every migration on a fresh ephemeral
     SQLite DB, asserts each DB reached an Alembic head, and checks `/health`
     serves the tag version. Nothing publishes if this fails.
   - **smoke** — the full Helm smoke matrix (combined / parts / broker / +obs)
     on a kind cluster, reusing `smoke-helm.yml`. Unlike the post-merge run on
     `main`, this one blocks: a release cannot ship while any deployment mode
     is red.
   - **ci (including `upgrade-test`)** — runs the full
     [`ci.yml`](../../.github/workflows/ci.yml) matrix (`full: true`) on the
     tagged commit before `publish-image`. This includes
     `build-candidate-image` and `upgrade-test`, which boots each enabled
     published release from
     [`tests/upgrade/supported-upgrades.yaml`](../../tests/upgrade/supported-upgrades.yaml),
     seeds live state, upgrades in place to the candidate image, and runs the
     post-upgrade verifier (see
     [Supported upgrades](../operations/supported-upgrades.md)). The same
     `upgrade-test` job also runs pre-tag on every `release-please--*` Release
     PR (for example `release-please--branches--main--components--jentic-one`
     on Release PR `#2007`, matched by `startsWith(github.head_ref, 'release-please--')`)
     so upgrade regressions are caught before a tag is cut.
   - **publish-image** — builds the `app` container image and pushes it to GHCR
     as `ghcr.io/<owner>/jentic-one-app` (tagged `X.Y.Z` — the `v` is stripped
     — and the short SHA; `latest` moves only on stable releases). One image
     serves every surface via `JENTIC__APPS`; this is the image self-hosters
     pull — see
     [`docs/installation/docker.md`](../installation/docker.md).
   - **release** — first builds the host `jentic` binary and asserts its
     `--version` equals the tag (before anything is uploaded), then GoReleaser
     builds the signed, checksummed `jenticctl` +
     `jentic` binaries (cosign keyless + syft SBOMs) and publishes the package
     channels: the Homebrew cask, a winget manifest PR against
     `microsoft/winget-pkgs`, and the scoop bucket manifest. It also attaches
     `.deb` / `.rpm` / `.apk` packages per binary to the release (no package
     repository, so nothing to provision). The packages are listed in the
     cosign-signed `checksums.txt` but are not signed individually and have no
     SBOM of their own (the syft SBOMs cover the archives). The winget/scoop
     publishes are token-gated: with `WINGET_TOKEN` / `SCOOP_BUCKET_TOKEN`
     unset the entry is skipped (logged, never fails the release) — see the
     one-time setup below.

Releases continue the pre-1.0 `0.x` line — see `VERSIONING.md` for the
versioning policy.

### Forcing or recovering a release

For a **partially failed run** (e.g. `publish-image` succeeded but GoReleaser
failed, or cosign/Sigstore hiccuped after the image pushed), the first move is
**"Re-run failed jobs"** on that run in the Actions UI: it re-executes only the
red jobs, leaving the already-pushed image (and its signature) untouched.
Two states worth knowing by name:

- **Published-but-unsigned**: the image push succeeded but the sign/attest
  step failed. `:latest` has *not* moved (it only moves after signing), but
  the `X.Y.Z`/SHA tags are live unsigned. Re-run the failed jobs — signing
  targets the already-pushed digest, so it converges.
- **Full re-run**: re-running *all* jobs rebuilds the image and `docker push`
  **overwrites** the existing `X.Y.Z`/SHA tags with a **new digest** (builds
  aren't bit-reproducible; the old digest stays pullable but untagged). The
  digest echoed by the *first* run then no longer matches the tag — anyone
  who pinned it keeps the old (still-signed) image. Prefer "Re-run failed
  jobs" precisely to avoid this.
- **Re-running the `release` (GoReleaser) job**: the GitHub Release is created
  by release-please before any gate runs, and GoReleaser attaches assets to it
  (`mode: append`). `replace_existing_artifacts` is enabled, so a re-run uploads
  the same asset names over the existing ones instead of failing on the first
  duplicate. The binary version check now runs before GoReleaser, so a
  mismatched binary stops the job before anything is uploaded or any package
  channel is touched. Not covered: re-running after the Homebrew cask, scoop
  or winget publish steps already ran has not been exercised (the cask and
  scoop pushes are commits to separate repositories; the winget publish opens a
  pull request and pushes a branch that already exists on a re-run). If a re-run
  fails in one of those steps, recover with a superseding release
  (`Release-As`, below) instead of repeating it. The Release is public from the
  moment it is created (`draft: false`), and `jentic update` follows git tags, so
  a failed release run is visible to users until a good one supersedes it.

When the run can't be recovered in place (the workflow itself needs a fix),
force a fresh release instead. release-please only opens a Release PR when
there are user-facing commits since the last release (`ci`, `chore`, `test`
and other hidden types don't trigger a bump) — to force one anyway, land a
commit on `main` whose footer sets the version explicitly:

```
ci(release): force patch release to republish artifacts

Release-As: 0.38.3
```

release-please then opens a `chore(main): release 0.38.3` PR; merging it cuts the
tag and re-runs `release.yml` (now from the fixed workflow on `main`), producing
a complete set of signed binaries + the package channels (cask, winget, scoop). A failed release version
is superseded by the next one — every release rebuilds all artifacts from
scratch, so nothing is lost by skipping it.

## Upgrade notes

Operator-facing changes for each release live in
[`docs/operations/upgrade-notes.md`](../operations/upgrade-notes.md). Notes
for changes not yet released go under its `## Unreleased` heading; in the
Release PR, rename that heading to the release version (e.g. `## 0.45.0`).
Don't link to `#unreleased`: the anchor goes away at release.

### Upgrade test gate and failing-leg runbook

The `upgrade-test` job in [`ci.yml`](../../.github/workflows/ci.yml) tests
in-place upgrades from the previous two minor series (`N-1` and `N-2`,
collapsed to their latest patch) defined in
[`tests/upgrade/supported-upgrades.yaml`](../../tests/upgrade/supported-upgrades.yaml)
and documented in [Supported upgrades](../operations/supported-upgrades.md).

**Promoting `upgrade-test` from report-only to blocking:** during initial
rollout (first two releases), `upgrade-matrix`, `build-candidate-image`, and
`upgrade-test` are listed in `ci-status.needs` (with step summaries and
`::warning::` annotations emitted on failure) but excluded from the failure
check so flaky container pulls cannot burn a release tag. To make the upgrade
pipeline a blocking gate on PRs, the Release PR, and `release.yml`, make this
one-line change in the `ci-status` job of
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml):

```bash
# Report-only (initial two releases):
results=$(echo "$NEEDS_JSON" | jq -r 'del(.coverage, ."upgrade-matrix", ."build-candidate-image", ."upgrade-test") | .[].result')

# Blocking:
results=$(echo "$NEEDS_JSON" | jq -r 'del(.coverage) | .[].result')
```

**Rolling the support window on a new minor release:** when a new minor
release `v0.N.0` is published to GHCR, update
[`tests/upgrade/supported-upgrades.yaml`](../../tests/upgrade/supported-upgrades.yaml)
for the next target series `0.(N+1)` so `0.N.0` becomes `n-1` (pinned to its
published multi-arch manifest digest) and `0.(N-1).x` becomes `n-2`.
[`tests/unit/test_supported_upgrades.py`](../../tests/unit/test_supported_upgrades.py)
and [`tests/upgrade/matrix.py`](../../tests/upgrade/matrix.py) enforce that
both `N-1` and `N-2` minor series are present with valid `sha256:` digests, and
`python -m tests.upgrade.matrix --verify-git-tags` (run in the `upgrade-matrix`
CI job and in `tests/upgrade/run.sh`) verifies that every source tag matches a
real `v*` release tag.

**Triaging a failing `upgrade-test` leg:**

1. Download the `upgrade-test-<source>-<backend>` artifact from the workflow
   run (contains `compose.log`, `migrate.log`, `migrate-check.log`, and
   `seed-manifest.json`), or reproduce locally:
   ```bash
   make test-upgrade OLD=<source> BACKEND=<backend>
   ```
2. Classify the failure into one of three categories (never add blanket
   `continue-on-error` or delete a required source minor):
   - **(a) Real migration or upgrade-step regression:** `migrate.log` or
     `migrate-check.log` shows a failed Alembic revision, an unapplied upgrade step
     (exit `3`/`4`/`5`), startup `ERROR` logs, or unreadable pre-upgrade
     data in `test_post_upgrade.py`. Fix the migration or upgrade step before
     merging the Release PR (or recover post-tag via `Release-As` above).
   - **(b) Intentional breaking change:** the release deliberately refuses an
     in-place upgrade without operator action or an intermediate waypoint.
     Document the requirement under the release heading in
     [`docs/operations/upgrade-notes.md`](../operations/upgrade-notes.md),
     add a `known_breaks` entry (`source`, `reason`, `upgrade_notes_link`, and
     `expires`) to
     [`tests/upgrade/supported-upgrades.yaml`](../../tests/upgrade/supported-upgrades.yaml),
     and update the harness to assert the documented refusal/route rather than
     skipping the leg.
   - **(c) Seeder drift against an older API shape:** the failure occurs
     during the `seed` phase against the unmodified old container image. Fix
     the pinned seeder for `<source>` under `tests/upgrade/seed/`.

## One-time setup (repo/org admin)

The automation is inert until these are provisioned:

- **A scoped GitHub App** for the release trigger (a tag/release made with the
  default `GITHUB_TOKEN` does not trigger downstream workflows). Install it on
  this repo with repository permissions **Contents: RW, Issues: RW, Pull
  requests: RW** (Issues is required — release-please creates its `autorelease:*`
  labels via the Issues API). Add secrets `RELEASE_PLEASE_APP_ID` and
  `RELEASE_PLEASE_APP_PRIVATE_KEY`.
- **`HOMEBREW_TAP_TOKEN`** — a fine-grained token with `contents: write` on
  `jentic/homebrew-tap` only (for the cross-repo cask push).
- **`SCOOP_BUCKET_TOKEN`** — same shape: a fine-grained token with
  `contents: write` on `jentic/scoop-bucket` only. Create that repo (public,
  empty is fine — GoReleaser commits `jentic.json` to its root on each
  release) before setting the secret.
- **`WINGET_TOKEN`** — a **classic** PAT with `public_repo` scope
  (fine-grained tokens cannot open cross-repo PRs against
  `microsoft/winget-pkgs`). Fork `microsoft/winget-pkgs` into the `jentic`
  org first; each release then pushes a manifest branch to the fork and opens
  the upstream PR. **Keep the fork's `master` synced** (GitHub's "Sync fork"
  button, or a scheduled sync) — a stale fork makes the generated PR conflict
  at tag time. The **first** submission goes through Microsoft's human
  review (typically days); later versions are auto-validated by bots. Until
  the first manifest lands, `winget install Jentic.Jentic` resolves nothing —
  the scoop bucket is the immediate Windows channel in the meantime.

Both Windows-channel secrets are **optional**: while unset, GoReleaser skips
that publisher with a log line and the release stays green (the
`skip_upload` templates in [`cli/.goreleaser.yaml`](../../cli/.goreleaser.yaml)). Provisioning the secret
is what turns the channel on.

cosign signing needs no secret — it uses the release job's OIDC token (keyless,
via Sigstore/Fulcio).

The **`publish-image`** stage needs no extra secret either — it pushes to GHCR
with the built-in `GITHUB_TOKEN` (the job grants it `packages: write`).

**First-release checklist:** the first push creates the `jentic-one-app`
package under the repo owner **as private**. After the first release, a
maintainer must set its visibility to **public** in the package settings —
until then self-hosters cannot `docker pull` without authenticating. GHCR's
**immutable tags** option is a trade-off, not a default: it hardens tags
against re-pushes, but breaks the full-re-run recovery path above (a full
re-run cannot overwrite `X.Y.Z`) — enable it only if you accept recovering
via "Re-run failed jobs" or `Release-As` instead. The image is cosign-signed
with an SBOM attestation; the verify commands live in [`deploy/README.md`](../../deploy/README.md)
("Verify the signature").

Also consider a **repository ruleset restricting `v*` tag creation** to the
release App and admins: the workflow trusts any pushed tag, and while the
gate's version assertion bounds what a rogue tag can ship, a signed release
should only ever be release-please-initiated.

## Verifying a release (supply chain)

GoReleaser signs `checksums.txt` with cosign keyless. To verify a downloaded
release:

```bash
# 1. verify the checksum file's cosign signature (keyless / Sigstore).
# Replace vX.Y.Z with the release tag being verified.
cosign verify-blob \
  --certificate checksums.txt.pem \
  --signature   checksums.txt.sig \
  --certificate-identity 'https://github.com/jentic/jentic-one/.github/workflows/release.yml@refs/tags/vX.Y.Z' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  checksums.txt

# 2. verify the artifact against the (now-trusted) checksum file.
sha256sum --check --ignore-missing checksums.txt
```

The **certificate identity** is the workflow that produced the signature:
`https://github.com/jentic/jentic-one/.github/workflows/release.yml@refs/tags/vX.Y.Z`,
issued by GitHub Actions OIDC (`https://token.actions.githubusercontent.com`).
Always pin both the exact `--certificate-identity` (matching the target release tag)
and `--certificate-oidc-issuer` so a signature from a different release tag or
workflow is not accepted.

Each archive also ships a syft SBOM (`*.sbom.json`) listing its contents.

> Note: the `brew install` path relies on the SHA-256 that Homebrew embeds in
> the cask (tamper-evident). The cosign signature above is for the direct-download
> / CI verification path.
