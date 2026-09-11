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
  keys) survive this release; they are dropped in Phase 6b. Before that
  release you must run the Phase 6a export/flatten/acknowledge runbook below
  — the Phase-6b drop migrations refuse to run until the acknowledgement is
  on record.

### Phase 6a runbook: export, flatten, verify, acknowledge

Run these against **production data** (all commands read/write the live
control + admin databases configured for the process). Order matters.

1. **Export first**: `jentic_one export-toolkits --out toolkit-export.json`.
   The file captures all five legacy tables (`toolkits`, `toolkit_keys`,
   `toolkit_credential_bindings`, `toolkit_permission_rules`,
   `agent_toolkit_bindings`) with row counts and dialect-neutral values. It
   embeds key hash digests — store it like a secrets backup.
2. *(Optional)* preview: `jentic_one flatten-toolkits --diff-only --report
   preview.jsonl` writes the report without touching either database.
3. **Flatten**: `jentic_one flatten-toolkits --report flatten.jsonl`. Every
   `(agent, credential)` pair reachable through a toolkit gains a direct
   binding carrying the pair's rules. Read the report: `rule_conflict` lines
   are pairs whose toolkit paths disagreed — they were bound **default-deny**
   (the safer outcome) and need a rules decision from you;
   `inactive_toolkit_binding` lines are live access you may have believed
   disabled (an inactive toolkit never gated bound agents) — they were
   migrated, review them; `pooled_rule_drift` lines are pairs whose effective
   rules narrowed from vendor-pooled to per-pair; `active_toolkit_key` lines
   want `retire-toolkit-keys` (or a revoke) before 6b.
4. **Double-run-and-diff** (the concurrency check — binds racing step 3 are
   possible since OSS cannot quiesce the bind endpoints): run
   `jentic_one flatten-toolkits` again and confirm it reports **zero
   creations**. If it created rows, repeat until a run creates nothing.
5. **Verify**: `jentic_one flatten-toolkits --verify` — recomputes the legacy
   pair set and fails unless every pair exists as a direct binding (extra
   hand-created bindings are fine). Rule-list divergence is reported, not
   fatal.
6. **Acknowledge**: `jentic_one flatten-toolkits --verify --acknowledge` —
   records the sentinel row (`toolkit_flattening_acks`, control DB) that the
   Phase-6b drop migrations require. It is refused unless the verification
   passes in that same invocation.

**Rollback after Phase 6b** is two steps, not one — see the Phase 6b
section below.

## Upgrading to the theme-5 Phase 6b release (the drops)

This is the release that deletes the toolkit data model. It refuses to
migrate until the Phase 6a runbook above has been completed and
acknowledged. Read this **before** running migrations.

- **Prerequisite: the Phase 6a acknowledgement.** The control-DB migration
  drops `toolkit_permission_rules`, `toolkit_credential_bindings`,
  `toolkit_keys`, and `toolkits` (children first). It is gated
  guard-and-raise: it proceeds only when either every one of those tables is
  empty (fresh installs, CI), or a `toolkit_flattening_acks` row exists —
  written only by `jentic_one flatten-toolkits --verify --acknowledge`. On
  any other state it raises with the runbook steps and leaves the database
  untouched; complete Phase 6a and re-run `migrations.run`.
- **Enterprise deployments: apply the overlay migration first.** On
  PostgreSQL the drop also refuses to run while any table outside the
  toolkit set still holds a foreign key into `toolkits` (the enterprise
  `toolkit_user_grants` FK). Apply the jentic-one-enterprise migration that
  drops that table (`d47c3a91be02`) before this release's migrations; the
  error message names it.
- **Admin DB**: `agent_toolkit_bindings` is dropped behind an analogous gate
  (empty, or at least one direct binding in `agent_credential_bindings` —
  the in-DB evidence that the flattening ran; the strict acknowledgement
  gate lives on the control chain). The retired `toolkits:read` /
  `toolkits:write` / `owner:toolkits:read` scope strings are also swept from
  every stored grant and token surface — they have granted nothing since
  Phase 5b, and after the sweep they no longer appear in `/me` or token
  introspection output.
- **`jntc_live_` keys stop authenticating.** The deprecation window opened
  in Phase 4 closes: a presented `jntc_live_` plaintext is now a plain
  invalid key (`401`), and the `jentic_one retire-toolkit-keys` command is
  gone with the `toolkit_keys` table. Before upgrading, confirm the
  `deprecated_toolkit_key_used` WARNING log is silent; any holder still
  presenting the old form must switch to the `sak_` key of the service
  account its key was migrated to (created by the retirement run;
  `POST /service-accounts/{id}/keys` mints one if it was never issued).
- **The `Jentic-Toolkit-Id` header is gone**, on both sides: it is no longer
  consumed on requests (it was already ignored on the default path) and no
  longer emitted on responses. Attribution rides `Jentic-Credential-Id` /
  `Jentic-Credential-Name`. The `tracestate` vendor value keeps its
  five-field shape; the second (toolkit) segment is now always `_`.
- **The `broker.direct_bindings_enabled` config key is deleted.** Direct
  agent↔credential bindings are the only authorization path. Remove the key
  from your config if you had set it (unknown keys fail config validation);
  deployments that had it `false` **must** complete Phase 6a first — the
  legacy toolkit path no longer exists to fall back to.
- **Historical executions keep their toolkit names.** Execution list/detail
  responses still show `toolkit_name` for pre-flattening records: the name
  is denormalized onto `execution_records` (backfilled by the Phase-6a
  flattening job) instead of resolved from the dropped `toolkits` table.
  Records whose toolkit was deleted before the backfill show `null`, exactly
  as before. Monitoring `group_by=toolkit` keeps working off the surviving
  `toolkit_id` attribution column.

### Rollback (Phase 6b → 6a)

Rollback is two steps, not one: downgrade the drop migrations **and then**
re-import the Phase 6a export:

1. Roll back to the previous release's code (the toolkit code paths no
   longer exist in this release).
2. Downgrade the drop migrations (control `v3d4e5f6a7b8`, admin
   `d1e2f3a4b5c6`). A `downgrade()` recreates the five tables **empty** —
   it cannot restore rows.
3. `jentic_one export-toolkits --import toolkit-export.json` with the file
   from Phase 6a step 1. Restoring the toolkit path with an empty
   `toolkit_permission_rules` is a **total default-deny authorization
   outage** for every agent on the legacy path — never skip the import. The
   import is additive and idempotent by primary key, so re-running it (or
   importing over rows created after the downgrade) is safe.

The scope sweep and the `execution_records.toolkit_name` backfill are not
reversed on rollback: the swept scopes granted nothing, and the denormalized
name column is additive (the older release simply ignores it).

## Deprecations

Active deprecation windows are registered here (the named channel) and
repeated in the GitHub Release notes of the release that opens each window.
An entry names what is deprecated, the release that opened the window, the
runtime signal an operator can watch, and the earliest removal point.

| Deprecated | Since | Runtime signal | Removal |
| ---------- | ----- | -------------- | ------- |
| `jntc_live_` toolkit API keys (theme-5 Phase 4). No new keys are issued; run `jentic_one retire-toolkit-keys` so existing plaintexts keep authenticating as their migrated service accounts, then rotate holders to `sak_` keys. | The first release carrying theme-5 Phase 4 (opened 2026-09-11). | `deprecated_toolkit_key_used` WARNING log lines — one per resolve, naming the service account still presenting the retired key form. | **Closed** — the theme-5 Phase 6b release ends acceptance: a presented `jntc_live_` plaintext is a plain `401` and the retirement command is gone. |


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
