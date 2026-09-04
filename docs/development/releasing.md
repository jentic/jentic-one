# Releasing Jentic One

Operational runbook for cutting a release. The *why* (versioning policy, the
decisions behind this setup) lives in [`VERSIONING.md`](../../VERSIONING.md); this
is the *how*.

## Cutting a release

Releases are automated with [release-please](https://github.com/googleapis/release-please)
(config: [`release-please-config.json`](../../release-please-config.json)):

1. Merge feature/fix PRs to `main` as usual (Conventional Commits, squash-merge).
2. release-please keeps a standing **Release PR** titled `chore(main): release X.Y.Z`.
   Its diff bumps the version in lockstep across `pyproject.toml`, `uv.lock`, and
   every Helm `Chart.yaml`, and updates `CHANGELOG.md`. You may edit the
   changelog directly in that PR (optional).
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
   - **publish-image** — builds the `app` container image and pushes it to GHCR
     as `ghcr.io/<owner>/jentic-one-app` (tagged `X.Y.Z` — the `v` is stripped
     — and the short SHA; `latest` moves only on stable releases). One image
     serves every surface via `JENTIC__APPS`; this is the image self-hosters
     pull — see
     [`docs/installation/docker.md`](../installation/docker.md).
   - **release** — GoReleaser builds the signed, checksummed `jenticctl` +
     `jentic` binaries (cosign keyless + syft SBOMs) and publishes the package
     channels: the Homebrew cask, a winget manifest PR against
     `microsoft/winget-pkgs`, and the scoop bucket manifest. The winget/scoop
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
