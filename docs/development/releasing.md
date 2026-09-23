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


## Upgrading to the first theme-5 release

Operator-facing changes shipped by the theme-5 (toolkit removal) release —
read this before rolling it out. Each active window below is also tracked in
the Deprecations table.

- **`jntc_live_` toolkit keys are retired; migration to `sak_` is automatic.**
  No new keys are issued. Run `jentic_one retire-toolkit-keys` once: every
  existing key digest is migrated to a service account, and the **unchanged
  plaintext keeps authenticating** for the deprecation window — as that
  service account, and as its successor agent once the theme-8 migration
  runs. Watch `deprecated_toolkit_key_used` WARNING logs to find holders still
  presenting the old key form, and rotate them to the successor agent's `jak_`
  key (`sak_` keys can no longer be issued).
- **The toolkit management surface is gone.** All `/toolkits/*` and
  `/agents/{id}/toolkits*` routes now return `404`. The `toolkits:read`,
  `toolkits:write`, and `owner:toolkits:read` scopes are retired: no route
  requires them and they grant nothing, but they are **tolerated in stored
  grants** — re-submitting a permission row that predates
  the retirement never fails validation. Access is managed on the
  agent↔credential axis instead: the agent detail **Access** tab ("Bound
  credentials") in the UI, or `POST /agents/{agent_id}/credentials`.
- **CLI: `--toolkit` → `--api`.** At this release, `jentic access request
  --api <vendor/name>` was the verb (`--provision` when nothing served the
  API yet), with `--toolkit` surviving as a hidden, deprecated alias for
  `--api`. *(Superseded: the theme-7 release —
  [epic #1374](https://github.com/jentic/jentic-one/issues/1374) — removes
  the access-request flow and the `jentic access` group entirely; the
  agent-driven connect flow, `jentic connect <vendor>` over
  `POST /integrations:connect`, is the replacement.)*
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

**Rollback after Phase 6b** is two steps, not one: downgrade the drop
migrations **and then** `jentic_one export-toolkits --import
toolkit-export.json`. A migration `downgrade()` recreates empty tables — it
cannot restore rows — and restoring the toolkit path with empty
`toolkit_permission_rules` is a **total default-deny authorization outage**
for every agent on the legacy path. Do not flip
`broker.direct_bindings_enabled` back off without re-importing.

## Upgrading to the first theme-8 release

Operator-facing changes shipped by the theme-8 (service-account removal)
Phase-1 release. Service accounts are migrated to successor **agents**; the
SA surface survives this release (Phase 2 removes it) but is stamp-guarded.

- **Migration is automatic and idempotent.** The combined/control server runs
  `migrate-service-accounts` once at startup (best-effort; the CLI is the
  recovery path). Every service account is copied to a successor agent —
  stored permission grants (empty stays empty; never the default agent
  permission set), toolkit/credential bindings, the per-binding inline permission
  rules (control DB), and the API-key digest — its outstanding opaque
  sessions are revoked, and the row is stamped (`migrated_to_actor_id`).
  **API-key callers keep authenticating**: the resolver is now agent-first,
  so migrated `sak_`/`jntc_live_` plaintexts keep working without a key
  change — but they now authenticate **as the successor agent**, which
  changes behaviour on a few endpoints (next bullet). Non-active SAs
  (pending/rejected/archived) are skipped-but-stamped; disabled SAs get a
  disabled successor.
- **Breaking for migrated `sak_` callers** (they are agents now):
  `POST /oauth/mint` returns `403` (it requires a service-account actor);
  `POST /integrations:connect` rejects `agent_id` in the request body (an
  agent caller *is* the agent); and an agent-initiated connect session can
  no longer be confirmed by the same caller — agent-initiated sessions need
  a human on the review page. Move these flows to a user/owner identity
  before upgrading.
- **Ownership and visibility shift.** The successor is created with
  `parent_actor_id` = the SA's owner, so (a) it becomes visible to
  `owner:agents:read` holders, and (b) any copied `owner:*` delegation permission
  now widens to the **owner's** resources — review SAs holding `owner:*`
  grants in the report. (c) Control-DB objects `created_by` the `sva_` id
  (credentials and access requests, whose owner-scoped reads key on
  `created_by`) are **not** re-attributed, so the successor loses
  owner-scoped access to them; re-create or re-assign them if the caller
  needs them. The JSONL report's `owner_visibility_note` repeats this per
  migrated SA.
- **Broker JWTs may only assert `actor_type=agent`.** A trusted-issuer JWT
  claiming `service_account` is now refused (uniform 401; `jwt_refused`
  WARNING with `jwt_actor_type_not_allowed`) — the JWT path reads no DB and
  would bypass the migration entirely. Re-issue such tokens against the
  successor agent. **Breaking** for issuers minting SA claims.
- **Rotating a successor agent's API key ends the old plaintext.** The
  migrated `sak_`/`jntc_live_` plaintext authenticates via the copied
  digest; rotating or revoking the successor's key replaces that digest —
  deliberate, audited, irreversible.

### Theme-8 Phase 1 runbook: snapshot, migrate, sweep, verify, acknowledge

Run against **production data**; order matters.

1. **Snapshot first**: take admin-DB and control-DB snapshots before the
   first production run. Pre-sweep reversal is stamp-based and lossless
   (delete successor agents + their credential/grant/binding rows and their
   control-DB inline permission rules by stamp, clear the stamps — the SA
   originals are still live); **post-sweep reversal requires the
   snapshots**. Token revocation is acceptable-irreversible in both stages.
2. *(Optional)* preview: `jentic_one migrate-service-accounts --diff-only
   --report preview.jsonl` — evaluates dispositions without writing. Review
   the JSONL: `had_client_secret` names every client-credentials holder
   (that grant channel dies in Phase 2), and the owner-visibility notes tell
   you which successors become visible to `owner:agents:read` holders.
3. **Migrate**: `jentic_one migrate-service-accounts --report run.jsonl`
   (or let the boot job do it). Re-runs are cheap no-ops via the stamp and
   pick up SAs created during the window.
4. **Dual-kill note (the window)**: disabling a migrated SA is refused
   (`409 service_account_migrated`) and would not cut its key anyway — new
   pods resolve agent-first and never consult SA status. **To cut a key,
   disable the successor agent** (the stamp gives the `sva_ → agnt_`
   mapping) or revoke its API key; new pods then **fail closed** — a
   disabled successor (or a stamped SA whose successor digest is gone)
   never falls back to the SA row (`migrated_key_fail_closed` WARNING).
   Old-image pods still honour the SA status until the fleet rollout
   completes. **Client-credentials holders** are not cut by either lever —
   a pre-sweep `client_credentials` login still mints an SA session (step
   7); the kill lever for them is the sweep: `jentic_one
   migrate-service-accounts --sweep-migrated` archives the SA (the grant
   then refuses it) and revokes every SA session it minted since the
   migration, in one transaction.
5. **Watch the fallback signal**: every key still resolving through the SA
   fallback logs a `service_account_fallback_resolve` WARNING and bumps the
   `auth_service_account_fallback_resolves` OTel counter
   (`auth_service_account_fallback_resolves_total` on the Prometheus
   exporter; needs `metrics.exporter` configured — it is an operator
   metric, not a phone-home telemetry event). Trending to zero is the sweep-readiness signal;
   sustained hits after the fleet rollout mean unmigrated stragglers —
   re-run the job.
6. **Sweep**: the boot job's automatic sweep archives migrated SAs (deleting
   the SA-keyed grant/binding rows and the `sva_`-keyed inline permission
   rules, NULLing the SA-side digest, and revoking any SA sessions minted
   since the migration) only once
   a stamp is older than
   `services.service_account_sweep_min_stamp_age_hours` (default 24 — the
   full-fleet-rollout proxy; `0` disables the gate, a negative value
   disables the automatic sweep entirely). Run `jentic_one
   migrate-service-accounts --sweep-migrated` to sweep immediately — only
   when no old-image pods remain (their SA arm needs the SA-keyed rows).
7. **Window semantics, stated**: outstanding SA tokens die at migration;
   API-key callers keep authenticating as the successor agent (per-request
   digest re-resolve) with the agent-caller behaviour changes listed above;
   a pre-sweep client-credentials login still mints a fully-scoped SA
   session (bounded to pre-existing `client_secret_hash` holders — the
   step-2 report names them; new client secrets cannot be registered, the
   endpoint is unrouted) until the sweep revokes those sessions and
   archives the SA (step 4).
8. **Verify**: `jentic_one migrate-service-accounts --verify` — zero
   unstamped rows, grant-twin parity, zero unrevoked live SA tokens, digest
   parity, no post-stamp mutation, and inline-rule parity (every `sva_`
   binding still holding control-DB rules has a successor twin with the same
   rule count; swept rows pass). Live SA sessions minted by
   client-credentials holders fail criterion 3 until the sweep revokes them.
9. **Acknowledge**: `jentic_one migrate-service-accounts --verify
   --acknowledge` — records the sentinel row
   (`service_account_migration_acks`, admin DB) that the theme-8 Phase-4
   drop migrations require. Refused unless the verification passes in that
   same invocation.

## Upgrading to the theme-8 Phase-2 release (service-account surface removed)

**Breaking.** The service-account management and token surface is deleted;
run the Phase-1 migration (above) first — the boot job still does it.

- **Routes removed:** every `/service-accounts…` route (create, list, get,
  approve/deny/disable/enable/archive, scopes, `:generate-api-key`) and
  `POST /oauth/mint` now return `404`. The gateway
  chart no longer routes `/service-accounts`.
- **`client_credentials` grant removed:** `POST /oauth/token` with
  `grant_type=client_credentials` returns `400 unsupported_grant_type`, and
  `client_credentials` is gone from `grant_types_supported` in the
  authorization-server metadata. Move holders to the successor agent's
  `jak_` API key (or the jwt-bearer grant).
- **SA sessions are dead:** an outstanding `service_account` access or
  refresh token introspects inactive, cannot be refreshed, and is refused at
  the broker.
- **API keys keep working:** migrated `sak_`/`jntc_live_` plaintexts keep
  authenticating as the successor agent; an unmigrated `sak_` key still
  resolves through the SA fallback (and `GET /me` / MCP `me` still answer
  for it) until Phase 4.
- **Scopes retired:** `service-accounts:read`, `service-accounts:write` and
  `owner:service-accounts:read` are no longer granted, listed, or implied by
  `org:admin`; stored grants of them are tolerated (a re-submitted scope
  set containing them is not a 422) and simply grant nothing.
- **Actor directory:** `GET /actors` lists users and agents only.
- **CLI:** the generated control client drops the service-account
  operations, and `jentic api endpoints --actor service_account` no longer
  matches any endpoint (agents are the only machine actor — filter with
  `--actor agent`).

## Upgrading to the permissions-rename release

Internal authorization is spelled "permission" on every surface (OAuth2/OIDC
names — `scope`, `allowed_scopes`, `scopes_supported`, `insufficient_scope` —
are unchanged). There are no compatibility aliases; read this before rolling
it out.

- **Schema.** The admin migration `d1e2f3a4b5c6` renames the table
  `actor_scope_grants` → `actor_permission_grants` and its column `scope` →
  `permission` (plus the unique constraint, primary key, and indexes). Stored
  values are unchanged (`agents:write` stays `agents:write`), and so are the
  `asg_…` row ids.
- **Expect an authentication gap during a rolling upgrade.** The previous
  release reads `actor_scope_grants` directly when it resolves API keys and
  opaque tokens for agents (and for unmigrated service-account keys). Once the
  migration has run, pods still on the previous release fail those lookups
  until they are replaced. The [upgrade contract](../operations/upgrades.md#the-contract)
  already treats old code on a new schema as unsupported — here it is an
  observable outage, so schedule the upgrade in a maintenance window, or scale
  the app and broker to zero before the migration and back up after it. With
  Helm, the migration runs as a `pre-upgrade` hook, so the window lasts from
  the hook until the rollout completes.
- **HTTP API.** `GET|PUT /agents/{id}/scopes` is now
  `/agents/{id}/permissions`, with `{"permissions": […]}` bodies; `GET /me`
  (including the service-account variant) reports `permissions` /
  `token_permissions` instead of `scopes` / `token_scopes`. The endpoint
  reference emits `required_permissions` under schema
  `jentic.endpoint-permission-tree/v1`. Audit rows keep their `scopes` payload
  key and `reason` strings.
- **CLI and Go SDK.** `jentic endpoints --scope` is now `--permission`.
  The generated control client renames `AgentScopesRequest`/`Response` to
  `AgentPermissionsRequest`/`Response`, and `MeAgent` exposes
  `Permissions`/`TokenPermissions` — a breaking change for Go importers of
  `github.com/jentic/jentic-one/cli`. Upgrade the CLI with the
  server: a mismatched CLI refuses `/me` and the endpoint reference with an
  error naming the version skew, rather than reporting an empty permission
  set.

## Deprecations

Active deprecation windows are registered here (the named channel) and
repeated in the GitHub Release notes of the release that opens each window.
An entry names what is deprecated, the release that opened the window, the
runtime signal an operator can watch, and the earliest removal point.

| Deprecated | Since | Runtime signal | Removal |
| ---------- | ----- | -------------- | ------- |
| `jntc_live_` toolkit API keys (theme-5 Phase 4). No new keys are issued (`POST /toolkits/{id}/keys` → `410 toolkit_keys_retired`); run `jentic_one retire-toolkit-keys` so existing plaintexts keep authenticating as their migrated service accounts (as their successor **agents** once theme-8 Phase 1 migrates them), then rotate holders to the successor's key. | The first release carrying theme-5 Phase 4 (opened 2026-09-11). | `deprecated_toolkit_key_used` WARNING log lines — one per resolve, naming the actor (service account, or successor agent after theme-8 migration) still presenting the retired key form. | The theme-5 toolkit-surface deletion release (Phase 5b), no earlier than **2026-12-01**. |
| Service accounts (theme-8 Phase 1). Every SA is auto-migrated to a successor agent; the migrated `sak_`/`jntc_live_` plaintext keeps authenticating — as that agent. The SA management surface, `POST /oauth/mint` and the `client_credentials` grant were removed in theme-8 Phase 2, and broker JWTs may no longer assert `actor_type=service_account`. Rotate holders to the successor agent's `jak_` key. | The first release carrying theme-8 Phase 1. | `service_account_fallback_resolve` WARNING log lines and the `auth_service_account_fallback_resolves` OTel counter — one per resolve still served by the SA fallback arm. | Surface removed in theme-8 Phase 2; Phase 4 drops the tables (gated on the `--verify --acknowledge` sentinel). |


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
