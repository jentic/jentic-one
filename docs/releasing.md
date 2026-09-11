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
   - **smoke** — the full Helm smoke matrix (combined / parts / broker / +obs)
     on a kind cluster, reusing `smoke-helm.yml`. Unlike the post-merge run on
     `main`, this one blocks: a release cannot ship while any deployment mode
     is red.
   - **publish-image** — builds the `app` container image and pushes it to GHCR
     as `ghcr.io/<owner>/jentic-one-app` (tagged `X.Y.Z` — the `v` is stripped
     — and the short SHA; `latest` moves only on stable releases). One image
     serves every surface via `JENTIC__APPS`; this is the image self-hosters
     pull — see
     [`deploy/README.md`](../deploy/README.md#self-hosted-containers--external-postgres).
   - **release** — GoReleaser builds the signed, checksummed `jenticctl` +
     `jentic` binaries (cosign keyless + syft SBOMs) and pushes the Homebrew cask.

The pre-1.0 baseline is the restored `v0.1.0`…`v0.13.2` tag line; the next
release is `v0.14.0` (we continue the `0.x` line — see `VERSIONING.md`).

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

Release-As: 0.14.3
```

release-please then opens a `chore(main): release 0.14.3` PR; merging it cuts the
tag and re-runs `release.yml` (now from the fixed workflow on `main`), producing
a complete set of signed binaries + the Homebrew cask. A failed release version
is simply superseded by the next one — every release rebuilds all artifacts from
scratch, so nothing is lost by skipping it.


## Upgrading to the first theme-5 release

Operator-facing changes shipped by the theme-5 (toolkit removal) release —
read this before rolling it out. Each active window below is also tracked in
the Deprecations table.

- **`jntc_live_` toolkit keys are retired; migration to `sak_` is automatic.**
  No new keys are issued. Run `jentic_one retire-toolkit-keys` once: every
  existing key digest is migrated to a service account, and the **unchanged
  plaintext keeps authenticating** — as that service account — for the
  deprecation window. Watch `deprecated_toolkit_key_used` WARNING logs to find
  holders still presenting the old key form, and rotate them to `sak_` keys.
- **The toolkit management surface is gone.** All `/toolkits/*` and
  `/agents/{id}/toolkits*` routes now return `404`. The `toolkits:read`,
  `toolkits:write`, and `owner:toolkits:read` scopes are retired: no route
  requires them and they grant nothing, but they are **tolerated in stored
  grants** — re-submitting a permission row or access request that predates
  the retirement never fails validation. Access is managed on the
  agent↔credential axis instead: the agent detail **Access** tab ("Bound
  credentials") in the UI, or `POST /agents/{agent_id}/credentials`.
- **CLI: `--toolkit` → `--api`.** `jentic access request --api <vendor/name>`
  is the verb (`--provision` when nothing serves the API yet). `--toolkit`
  survives as a hidden, deprecated alias for `--api`.
- **Broker headers.** Requests are disambiguated with `Jentic-Credential-Name`
  or `Jentic-Credential-Id` (the id is authoritative); responses attribute the
  credential used via the same two headers. The `Jentic-Toolkit-Id` response
  header is emitted only on the legacy flag-off toolkit path
  (`broker.direct_bindings_enabled: false`) and is removed in Phase 6b —
  adopt the credential headers now.
- **Toolkit tables are still present.** The stored toolkit rows (bindings,
  keys) survive this release; they are dropped in Phase 6b. The Phase 6a
  export/acknowledge runbook — how to export the legacy toolkit data and
  acknowledge the drop — lands in a release **before** the tables are removed.

## Deprecations

Active deprecation windows are registered here (the named channel) and
repeated in the GitHub Release notes of the release that opens each window.
An entry names what is deprecated, the release that opened the window, the
runtime signal an operator can watch, and the earliest removal point.

| Deprecated | Since | Runtime signal | Removal |
| ---------- | ----- | -------------- | ------- |
| `jntc_live_` toolkit API keys (theme-5 Phase 4). No new keys are issued (`POST /toolkits/{id}/keys` → `410 toolkit_keys_retired`); run `jentic_one retire-toolkit-keys` so existing plaintexts keep authenticating as their migrated service accounts, then rotate holders to `sak_` keys. | The first release carrying theme-5 Phase 4 (opened 2026-09-11). | `deprecated_toolkit_key_used` WARNING log lines — one per resolve, naming the service account still presenting the retired key form. | The theme-5 toolkit-surface deletion release (Phase 5b), no earlier than **2026-12-01**. |


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
with an SBOM attestation; the verify commands live in `deploy/README.md`
("Verify the image signature").

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
