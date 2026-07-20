# Releasing jentic-one

Operational runbook for cutting a release. The *why* (versioning policy, the
decisions behind this setup) lives in [`VERSIONING.md`](../VERSIONING.md); this
is the *how*.

## Cutting a release

Releases are automated with [release-please](https://github.com/googleapis/release-please)
(config: [`release-please-config.json`](../release-please-config.json)):

1. Merge feature/fix PRs to `main` as usual (Conventional Commits, squash-merge).
2. release-please keeps a standing **Release PR** titled `chore(main): release X.Y.Z`.
   Its diff bumps the version in lockstep across `pyproject.toml`, `uv.lock`, and
   every Helm `Chart.yaml`, and updates `CHANGELOG.md`. You may edit the
   changelog directly in that PR (optional).
3. **Merging the Release PR is the release.** release-please then tags `vX.Y.Z`,
   creates the GitHub Release, and (because the tag is pushed with the release
   App token) triggers [`release.yml`](../.github/workflows/release.yml):
   - **gate** — builds the app, runs every migration on a fresh ephemeral
     SQLite DB, asserts each DB reached an Alembic head, and checks `/health`
     serves the tag version. Nothing publishes if this fails.
   - **release** — GoReleaser builds the signed, checksummed `jenticctl` +
     `jentic` binaries (cosign keyless + syft SBOMs) and pushes the Homebrew cask.

The pre-1.0 baseline is the restored `v0.1.0`…`v0.13.2` tag line; the next
release is `v0.14.0` (we continue the `0.x` line — see `VERSIONING.md`).

### Forcing or recovering a release

release-please only opens a Release PR when there are user-facing commits since
the last release (`ci`, `chore`, `test` and other hidden types don't trigger a
bump). To force a release anyway — e.g. to recover a release whose `release.yml`
run failed before GoReleaser published its assets — land a commit on `main`
whose footer sets the version explicitly:

```
ci(release): force patch release to republish artifacts

Release-As: 0.14.3
```

release-please then opens a `chore(main): release 0.14.3` PR; merging it cuts the
tag and re-runs `release.yml` (now from the fixed workflow on `main`), producing
a complete set of signed binaries + the Homebrew cask. A failed release version
is simply superseded by the next one — every release rebuilds all artifacts from
scratch, so nothing is lost by skipping it.


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

cosign signing needs no secret — it uses the release job's OIDC token (keyless,
via Sigstore/Fulcio).

## Verifying a release (supply chain)

GoReleaser signs `checksums.txt` with cosign keyless. To verify a downloaded
release:

```bash
# 1. verify the checksum file's cosign signature (keyless / Sigstore).
cosign verify-blob \
  --certificate checksums.txt.pem \
  --signature   checksums.txt.sig \
  --certificate-identity-regexp '^https://github\.com/jentic/jentic-one/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  checksums.txt

# 2. verify the artifact against the (now-trusted) checksum file.
sha256sum --check --ignore-missing checksums.txt
```

The **certificate identity** is the workflow that produced the signature:
`https://github.com/jentic/jentic-one/.github/workflows/release.yml@refs/tags/vX.Y.Z`,
issued by GitHub Actions OIDC (`https://token.actions.githubusercontent.com`).
Always pin both `--certificate-identity(-regexp)` and `--certificate-oidc-issuer`
— verifying without them accepts any Sigstore certificate and defeats the point.

Each archive also ships a syft SBOM (`*.sbom.json`) listing its contents.

> Note: the `brew install` path relies on the SHA-256 that Homebrew embeds in
> the cask (tamper-evident). The cosign signature above is for the direct-download
> / CI verification path.
