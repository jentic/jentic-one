# Upgrade notes

Operator-facing changes per release, newest first. Read every section between
the version you run and the one you are moving to; the
[upgrade contract](upgrades.md#the-contract) (snapshot, pin, migrate, restart)
applies to every one of them. The full change list is in
[`CHANGELOG.md`](../../CHANGELOG.md); active deprecation windows are in
[Deprecations](#deprecations).

Upgrading from **0.39.x or older**: go through **0.40.x** and then **0.41.1**
before moving on — see [0.41.0](#0410) for why.

## Unreleased

Changes on `main` since 0.44.0; they ship in the next release.

- **Breaking: `jentic run` and local agent accounts are removed.** The CLI no
  longer creates a dedicated `<you>-local-agent` Unix user (with its home-dir
  ACL grants, passwordless-launch sudoers entry, and Seatbelt/bwrap session
  confinement), and `jentic setup` no longer offers that step or the MCP
  sudo-shim isolation (`_jentic-<runtime>` service users and the root-owned
  `/usr/local/libexec/jentic/jentic` copy). `jentic run` is now an unknown
  command; `jentic setup` registers the agent, installs the skill, and writes
  the plain `jentic mcp` entry. A machine that already has an agent account or
  MCP service account keeps it until you run **`jentic reset`**, which still
  deletes the users, revokes the ACL grants, removes the sudoers lines and the
  root-owned copy, and then wipes your local jentic identity state (re-run
  `jentic setup` afterwards). `jenticctl doctor` warns about any that remain,
  and about a sudo-shim MCP entry left in a runtime config. To keep a coding
  agent away from the instance's files, run it in a container or VM, or run
  the instance on another machine — see
  [same-host options](../security/same-host/README.md) and
  [hardening same-host MCP](../security/same-host/mcp-same-host-hardening.md).
- **The connect-session rejection cooldown is off by default.**
  `control.connect.rejection_cooldown_hours` defaults to `0` (it was `24`),
  so an agent whose request a human rejected may ask again at once instead of
  getting `429 recently_rejected`. Its open requests are still capped by
  `control.connect.max_open_sessions_per_agent` (default `10`; `429
  too_many_open_sessions` past it). A value you set is kept; set one to keep
  the hold (it also covers an agent's access-change requests for the same
  binding).
- **A server-host change on an API with bound credentials needs `org:admin`.**
  Promoting a draft whose server hosts differ from the API's live ones (or
  move to plaintext `http`), pinning such a draft with `Jentic-Revision`, and
  letting a catalog re-import with new hosts go current all need `org:admin`;
  `credentials:write` no longer approves them. For anyone else the refusal
  depends on the path, so alert on all three rather than on one error type:
  - **Promote** is refused with `403 host_change_requires_operator`.
  - **A catalog re-import** is kept as a draft and the live revision stays
    served; nothing is refused.
  - **A `Jentic-Revision` pin** to a held *catalog* draft (one that keeps its
    `origin`) is refused with `403 unauthorized_revision_pin`, because the
    draft is not the caller's own. In URL discovery that pin is skipped
    instead, so the call answers `404 operation_not_found`. A pin to the
    caller's *own* held draft is the `host_change_requires_operator` case.
- **`submitted_by` on `POST /apis` is honoured only for `org:admin`.** For
  anyone else the resulting revision is attributed to the caller.
- **JWT-bearer `jti` replay records move to the admin database.** The admin
  migration adds the `assertion_jtis` table, applied with the release's other
  migrations. A `jti` is single-use per agent across every auth replica and
  restart. Nothing to configure; the table prunes itself.
- **The remaining in-process counters use the shared-state backend.** The
  broker's auth-failure event counters, the circuit-open event dedupe and the
  MCP session-event dedupe and per-client caps live on `state.backend`, so on
  a registered shared backend they are deployment-wide. On `memory` they stay
  per process.
- **A surface assembled without its shared-state wiring fails loudly.** The
  auth routers and the control and MCP connect limiters raise a
  `RuntimeError` naming the missing step (the MCP mount at startup, the others
  on first use) when an app is put together without the auth surface's
  `install_on_app` or the control surface's `install_connect_limiters`. The
  shipped app factories are unaffected; a custom assembly must call those
  install functions.

## 0.44.0

- **Breaking: `toolkit_id` is no longer a filter on `GET /executions` or
  `GET /monitoring/usage` (#1745).** A request that still sends
  `?toolkit_id=` is served normally (`200`) and the parameter is ignored, so
  the result is unfiltered: a dashboard or script that narrowed by toolkit
  gets the whole tenant's executions or totals. `toolkit_id` /
  `toolkit_name` stay on execution responses as historical attribution.
  Filter by `api` / `actor_id` (executions) or `api_id` / `agent_id`
  (monitoring), or filter client-side on the response field. Each ignored
  request logs one `removed_query_param_ignored` INFO line with
  `param="toolkit_id"`, the route and the caller's `actor_id` (never the
  value); use it to find the callers to update. The generated UI and Go
  clients no longer expose the parameter.
- **The shared-state config moves to a top-level `state:` section (#1871).**
  `broker.resilience.backend.*` becomes `state.*` (same keys: `backend`,
  `memory.*`, `redis_url`, `redis_key_prefix`), and the env vars
  `JENTIC__BROKER__RESILIENCE__BACKEND__*` become `JENTIC__STATE__*`. The old
  section and env vars are still read into `state`, with one
  `config_deprecated_setting` WARNING per process (its `setting` field is
  `broker.resilience.backend`). Keys may be split between the two paths, but a
  key set under both to **different** values fails config loading, naming
  the field — check Helm `extraEnv` and config files together when moving.
- **One shared-state backend per process.** A process builds a single backend
  shared by every surface it serves. `state.memory.max_total_bytes` (192 MiB)
  caps the whole process's shared state; the defaults fit the shipped memory
  limits.
- **One process per surface is the supported shape.** The auth, control and
  broker surfaces keep their rate limits, circuit state, `Idempotency-Key`
  records and auth anti-replay markers in the process, so a second replica or
  uvicorn worker of one gets its own copy. Keep `replicas: 1` on every surface
  but the gateway; the Helm chart's install notes warn when another value is
  set. Multi-process shared state comes from a backend registered with
  `register_state_backend` and selected by `state.backend`
  ([extending-jentic-one.md](../development/extending-jentic-one.md)); an
  unregistered name fails startup listing the registered ones.
- **The built-in `redis` backend is deprecated.** It still works and logs a
  `state_backend_deprecated` WARNING at startup; it is removed in a later
  release (see [Deprecations](#deprecations)).

## 0.43.0

- **`owner:access-requests:read` maps onto `credentials:connect` (#1612).**
  The agent baseline carries `credentials:connect` where it carried the
  retired `owner:access-requests:read`, but rows written before the swap keep
  the retired string. A DCR client registered then (Claude, Cursor,
  `mcp-remote`) gets `invalid_scope` from `/authorize` as soon as it asks for
  the advertised `credentials:connect` (Claude Desktop shows "couldn't
  connect"), and an agent created then cannot start a connect session. The
  admin migration `6ae76df5ece5` gives `credentials:connect` to every agent
  and every OAuth client allowlist or consent grant that still carries the
  retired scope, and strips the retired scope everywhere else (user grants,
  token snapshots, authorization codes). Allowlists without it are untouched,
  and an agent or client whose audit log shows `credentials:connect` removed
  by a human is not granted it again. The migration prints one `==> WARNING
  (access-request scope retirement)` line naming the agents and OAuth clients
  it changed (and any it withheld); revoke `credentials:connect` per agent or
  per client to opt out. No window is needed.

## 0.42.0

0.42.0 renames internal authorization from "scope" to "permission", so the
upgrade needs a maintenance window. Take the pre-upgrade snapshot of every
database first ([the contract](upgrades.md#the-contract)).

- **Schedule a maintenance window (#1399).** The admin migration
  `e3f4a5b6c7d8` renames the table `actor_scope_grants` →
  `actor_permission_grants` and its column `scope` → `permission` (plus the
  unique constraint, primary key, and indexes). Stored values are unchanged
  (`agents:write` stays `agents:write`), and so are the `asg_…` row ids. The
  previous release reads `actor_scope_grants` almost everywhere (every grant
  read and write, the broker's execution gate, token minting, API-key and
  opaque-token resolution), so from the moment the migration runs until the
  last old pod is replaced, old app and broker pods fail authentication,
  execution and every grant change. Schedule a window, or scale the app and
  broker to zero before the migration and back up after it. With Helm, the
  migration runs as a `pre-upgrade` hook, so the window lasts from the hook
  until the rollout completes.
- **Permission renames on the HTTP API.** There are no compatibility aliases.
  OAuth2/OIDC names (`scope`, `allowed_scopes`, `scopes_supported`,
  `insufficient_scope`) are unchanged.
  - `GET|PUT /agents/{id}/scopes` is `/agents/{id}/permissions`, with
    `{"permissions": […]}` bodies.
  - `POST /agents` takes its initial grants as `permissions` (was `scopes`).
    Both `POST /agents` and `PUT /agents/{id}/permissions` reject unknown body
    fields with `422`, so a client still sending `scopes` fails loudly instead
    of the key being ignored.
  - `GET /me` reports `permissions` / `token_permissions` instead of
    `scopes` / `token_scopes`.
  - The agent permission-ceiling problem types are
    `agent_permission_not_grantable` (`403`, was `scope_not_grantable`) and
    `unknown_agent_permission` (`422`, was `unknown_scope`) — distinct from
    the admin user-permission types `permission_not_grantable` /
    `unknown_permission`.
  - The endpoint reference emits `required_permissions` under schema
    `jentic.endpoint-permission-tree/v1`.
- **Audit log.** New agent create/approve/replace/register entries write the
  `permissions` payload key and the `replace_permissions` /
  `default_permissions` reason strings. Entries already in the log keep the
  `scopes` key and `replace_scopes` / `default_scopes` reasons, so external
  `GET /audit` consumers must read both spellings across the upgrade.
- **Upgrade every CLI with the server.** `jentic endpoints --scope` is
  `--permission`. A new CLI talking to an old server refuses `/me` and the
  endpoint reference with a `SERVER_INCOMPATIBLE` error, but an older CLI
  talking to a new server fails silently — `jentic doctor` reports "scopes:
  none", `jentic endpoints` shows every route as open to any authenticated
  caller, and `--scope` matches nothing. Go importers of
  `github.com/jentic/jentic-one/cli` get these breaking changes in the
  generated control client:
  - `AgentScopesRequest`/`Response` are `AgentPermissionsRequest`/`Response`,
    and `MeAgent` exposes `Permissions`/`TokenPermissions`.
  - `ConfirmSessionRequest` is gone. The `:confirm` body is
    `ConfirmConnectSessionJSONBody`, a `oneOf` union of
    `OAuthConfirmSessionRequest`, `ApiKeyConfirmSessionRequest`,
    `BearerConfirmSessionRequest`, `BasicConfirmSessionRequest`,
    `OwnOAuthClientConfirmSessionRequest`,
    `ExistingCredentialConfirmSessionRequest` and
    `ReauthorizeConfirmSessionRequest`, built with the `From…` / `As…`
    helpers. The `200` response stays untyped.
  - `IntegrationsConnectRequest.Vendor` is a `*string` (exactly one of
    `vendor` or the new `api`), and `PollToken` in
    `GetConnectSessionParams`, `PollConnectSessionStatusParams`,
    `CancelConnectSessionParams` and `ConfirmConnectSessionParams` is a
    `*string`.
  - `EventAcknowledgeRequest`, the `AcknowledgeEvent` operation and the
    `Acknowledged*` fields of `EventResponse` are removed.
- **The UI opens in a light theme (#1564).** Light is the default for new and
  existing users alike (the OS appearance is not consulted); **Dark theme** in
  the user menu switches back, remembered per browser (`localStorage` key
  `jentic-one.theme`).
- **The agent detail page is removed (#1475).** `/app/agents/<id>` redirects
  to `/app/agents?agent=<id>`, which opens that agent on the Agents page. Any
  other query parameter, `?tab=` included, is dropped, so a bookmark or
  runbook link to a tab lands on the agent, not the tab.
- **Event acknowledgement is removed (#1400).** `PATCH /events/{event_id}` is
  gone, event responses drop `acknowledged`, `acknowledged_at` and
  `acknowledged_by`, and `GET /events` drops its `acknowledged` filter. An
  unknown query parameter is ignored, so a client still sending
  `acknowledged=false` gets every matching event; filter on `requires_action`
  instead. The admin migration `d2e3f4a5b6c7` drops the four acknowledgement
  columns; its downgrade re-adds them with every event unacknowledged.
- **Connect sessions (#1561, #1562, #1565, #1566, #1567, #1568, #1569,
  #1574).**
  - **Approval links carry no poll token.** `approval_url` is
    `<auth base URL>/app/agents?approve=<session id>`. The target agent's
    owner (holding `credentials:write` and `agents:write`) or an `org:admin`
    reviews, confirms, rejects or cancels it signed in; any other caller gets
    `403`. The poll token stays with the agent, and an older link that still
    carries `poll_token` keeps working for its holder. A confirmed credential
    is attributed (`created_by`) to the approver.
  - **`provisioning_url` only for an open session.** The broker's `403
    no_credential_binding` and `424 credential_not_provisioned` carry
    `provisioning_url` only when the denied agent already has an open
    (`created`, `awaiting_app` or `polling`) connect session for the API; it
    is that session's approval link, and the directive then drops
    `suggested_command`.
  - **`broker.account_linking_base_url` is retired.** A leftover value (or
    `JENTIC__BROKER__ACCOUNT_LINKING_BASE_URL`) is ignored with one
    `config_retired_setting_ignored` WARNING per process. Remove it.
  - **Agents can ask for a credential for any registry API.**
    `control.connect.manual_flows_enabled` (default `true`, upgrades
    included) lets an agent start a connect for a registry API (`jentic
    connect --api`, MCP `request_connection` with `api`) that a human approves
    by entering the credential or bringing an OAuth app. Set it to `false` to
    refuse API targets with `manual_flows_disabled`. The migration run prints
    one `==> WARNING (connect sessions)` line while the setting is on. On a
    rolling upgrade, an older control replica answers such a `:connect` with
    `422` until it is replaced. `control.connect.manual_flows_ttl_hours`
    (default `72`) bounds those sessions, and `max_open_sessions_per_agent`
    (`10`) / `max_open_sessions_per_owner` (`50`) cap open sessions (`429
    too_many_open_sessions`).
  - **Rejections and repeats.** `POST /connect-sessions/{session_id}:reject`
    ends a session as rejected; the same agent's next `:connect` for that
    target gets `429 recently_rejected` for
    `control.connect.rejection_cooldown_hours` (default `24` in 0.42.0–0.44.0;
    `0` after, see Unreleased above). An agent asking again for the same OAuth
    vendor, app and scopes gets its open session back with a fresh poll
    token, so the earlier token's poll answers `403`.
  - **Outcomes.** Every ended session leaves a row in the control table
    `connect_session_outcomes` (kept 30 days), and `/status` answers from it
    once the session row is gone. `connect_sessions.target_kind` is `vendor`
    or `api`; existing rows are `vendor`.
  - **Vendor-connect credentials take the API's registered identity
    (#1574).** A vendor connect stamps the credential with the `(vendor,
    name)` the registry gives the API, so executes through it match instead
    of failing with `credential_identity_mismatch`.
- **Catalog imports name an API by its sub segment (#1067).** A first import
  of the catalog entry `posthog.com/posthog-api` registers as
  `posthog-com/posthog-api` (0.41 registered
  `posthog-com/posthog-com-posthog-api`); entries whose sub segments would
  collide keep the full id. APIs already imported keep their names: a
  re-import or a catalog update lands on the stored identity (looked up by
  the API's catalog id, then its spec URL). Two cases get the new name and
  stop matching credentials scoped to the old one
  (`credential_identity_mismatch` at execute): an API **deleted and imported
  again** after the upgrade, and an API **first imported** after the upgrade
  for which a credential was created on 0.41 under the doubled name. Re-scope
  those credentials to the new identity. Creating a credential whose API
  scope matches no imported API answers with `warnings` and records a
  `credential.unmatched_api` event; it never blocks the create.
- **Access logs mask query-string values (#1572).** uvicorn's access line
  keeps the method, path, parameter names and status, and replaces every
  query value with `***REDACTED***`. Log queries that matched on query values
  need another key.
- **Pinning a draft that moves server hosts is held (#1573).** A
  `Jentic-Revision` pin to the caller's own draft whose server hosts differ
  from the API's live hosts (or move to plaintext `http`), on an API with
  bound credentials, is held like a promote: a caller without
  `credentials:write` gets `403 host_change_requires_operator`. From the next
  release the approver is `org:admin` (see Unreleased above).
- **Reads and verbs are scoped to the caller (#1507, #1508, #1518, #1522,
  #1525, #1528, #1532, #1534).** Without `org:admin`, a caller sees only its
  own and its agents' executions and events (both already in 0.41.1);
  updates or deletes only notes it can see; lists only the bound agents it can
  see on `GET /credentials/{id}/agents` and its binding-permission reads
  (unless it created the credential); approves or denies only the agents it
  owns (an ownerless agent needs `org:admin`); and gets `404` for agents it
  cannot see on `GET /agents/{id}/oauth-grants`. An out-of-scope id answers
  `404`, as if it did not exist. A shared permission rule set is attachable
  only by its creator or an `org:admin`, unless it is curated (anyone else
  gets `403 rule_set_attach_denied`); existing attachments stay and are
  reported (see
  [Cross-owner rule set attachments](upgrades.md#cross-owner-rule-set-attachments)).
  Integrations that relied on seeing other users' data need `org:admin`.
- **Migrations.** None is irreversible in schema, but some `down` bodies do
  not restore data; roll back by restoring the snapshot.
  - Admin: `0679072d60eb` adds nullable `operation_path` and
    `operation_method` to `execution_records` (no backfill, #1381);
    `e3f4a5b6c7d8` renames `actor_scope_grants` to
    `actor_permission_grants` (#1399); `d2e3f4a5b6c7` drops the event
    acknowledgement columns and rebuilds `ix_events_requires_action` on
    `requires_action` alone (#1400).
  - Control: `g4d5e6f7a8b9` adds `permission_rule_sets.curated` (#1528);
    `aa1b2c3d4e5f` adds the shared OAuth app registration tables,
    `credentials.oauth_app_registration_id` and
    `connect_sessions.pkce_code_verifier` (#1435); `bb2c3d4e5f6a` adds the
    connect-session target kinds, the open-API dedupe index and
    `connect_session_outcomes` (#1566), and its downgrade deletes `api`
    target sessions and the pending credentials of live ones;
    `cc3d4e5f6a7b` adds `vendor_key`, `dedupe_key`, the OAuth dedupe index
    and the outcome `credential_id` (#1567). Before creating the index it
    expires every older open duplicate still in `created` (an `expired`
    outcome is written and its pending credential deleted); one already in
    `polling` keeps running to its 30-minute TTL.
  - Registry: no new revision. `c2d3e4f5a6b7` leaves the `vector` extension
    in place (#1519), so an install that has not run it yet keeps pgvector
    and its dependents.
  - The repeatable `rule_sets_mark_curated` upgrade step runs on every full
    upgrade and marks the rule sets an `org:admin` or a system job created
    as curated (#1536). `migrations.run --check` reports a step that has not
    run as `OVERALL pending` (exit `3`), and `jenticctl start` refuses to
    start until it has.

## 0.41.0

**On PostgreSQL, use 0.41.1, not 0.41.0.** In 0.41.0 the admin scope sweeps
(`d1e2f3a4b5c6`, `e2f3a4b5c6d7`) fail with `column "allowed_scopes" is of
type character varying[] but expression is of type jsonb` when an OAuth
client's `allowed_scopes` still lists a retired `toolkits:*` or
`service-accounts:*` scope. The failing revision rolls back and nothing is
half-applied; re-running the migration on the 0.41.1 image continues from
where it stopped. 0.41.1 also scopes execution and event reads to the caller
(see [0.42.0](#0420)). Everything below applies to both.

0.41.0 drops the toolkit tables and the service-account tables. Both drops
are gated, and the service-account one is irreversible. Before upgrading:

1. **Be on 0.40.x.** 0.40.x runs the toolkit-key retirement and the one-shot
   toolkit flattening that 0.41 no longer carries. Skipping straight from an
   older release leaves unmigrated `jntc_live_` keys (they stop
   authenticating) and a toolkit graph with no direct bindings.
2. **Export the toolkit tables** on 0.40.x:
   `jentic_one export-toolkits --out toolkit-export.json`. The file captures
   all five toolkit tables (`toolkits`, `toolkit_keys`,
   `toolkit_credential_bindings`, `toolkit_permission_rules`,
   `agent_toolkit_bindings`) with row counts and dialect-neutral values. It
   embeds key hash digests — store it like a secrets backup. A
   [rollback](#rollback-from-041) that stopped before the service-account
   drop needs it.
3. **Resolve every live, unmigrated toolkit key** on 0.40.x: each ownerless
   key the 0.40 migration run warned about needs
   `jentic_one retire-toolkit-keys --owner <admin-email>` (0.40.x only) —
   or, if the key must not get a successor, set `revoked = true` on its
   `toolkit_keys` row in SQL. The toolkit drop refuses while such a key
   remains.
4. **Move `sak_` callers to `jak_` keys.** **Breaking:** every `sak_`
   service-account key is refused from 0.41.0, with no grace period. For
   zero downtime, do it on 0.40.x, where the successor agents already exist,
   named `service-account:<sva_ id>`: for each one, mint a key
   (`POST /agents/{agent_id}:generate-api-key`, or the agent's **Keys** panel
   in the UI), switch the callers to it, and only then upgrade. Converted
   `jntc_live_` toolkit keys are not affected.
5. **Snapshot both the admin and the control databases.** The
   service-account retirement writes to both (the service-account tables in
   admin are dropped, the `sva_`-keyed inline permission rules in control are
   deleted), and only the snapshots bring that data back.
6. **Drain 0.40.x replicas before the migration, or finish the rollout
   promptly.** Once the service-account tables are dropped, a `sak_` or
   `jntc_live_` request to a 0.40.x replica returns `500`, and a 0.40.x
   replica that restarts logs `service_account_migration_startup_failed`
   (the server still starts).

Then, on the 0.41.1 image, run
[flatten, verify and acknowledge](#flatten-verify-and-acknowledge) and the
full migration. An acknowledgement written by 0.40 does not qualify.

### Flatten, verify and acknowledge

Run these on the 0.41.1 image against production data (they read and write
the live control and admin databases configured for the process). Order
matters.

1. **Apply the evidence columns:** `python -m jentic_one.migrations.run --db
   control --target f2b3c4d5e6a7` (a full migration run stops there too,
   refusing the drop).
2. *(Optional)* **Preview:** `jentic_one flatten-toolkits --diff-only
   --report preview.jsonl` writes the report without touching either
   database.
3. **Flatten:** `jentic_one flatten-toolkits --report flatten.jsonl`. Every
   `(agent, credential)` pair reachable through a toolkit gains a direct
   binding carrying the pair's rules, and historical execution records get
   their toolkit name. Read the report:
   - `rule_conflict` — the pair's toolkit paths disagreed; it was bound
     **default-deny** and needs a rules decision from you;
   - `inactive_toolkit_binding` — live access you may have believed disabled
     (an inactive toolkit never gated bound agents); it was migrated, review
     it;
   - `pooled_rule_drift` — the pair's effective rules narrowed from
     vendor-pooled to per-pair;
   - `active_toolkit_key` — a live key with no successor agent; resolve it as
     in step 3 above;
   - `cross_owner_binding` — see
     [Reviewing grants and bindings](#reviewing-grants-and-bindings-carried-over-by-the-upgrade).
4. **Re-run until nothing is created:** run `jentic_one flatten-toolkits`
   again and confirm it reports **zero creations** (binds racing step 3 are
   possible, since the bind endpoints stay open). Repeat until a run creates
   nothing.
5. **Verify:** `jentic_one flatten-toolkits --verify` recomputes the toolkit
   pair set and fails unless every pair exists as a direct binding (extra
   hand-created bindings are fine), every execution name is backfilled, and
   no live unmigrated toolkit key remains (`verify_live_toolkit_keys`).
   Rule-list divergence is reported, not fatal.
6. **Acknowledge:** `jentic_one flatten-toolkits --verify --acknowledge`
   records the sentinel row (`toolkit_flattening_acks`, control DB) that the
   drop migrations require. It is refused unless the verification passes in
   that same invocation.
7. **Migrate:** `python -m jentic_one.migrations.run`.

### Toolkit table drop

- **The gate.** The control migration drops `toolkit_permission_rules`,
  `toolkit_credential_bindings`, `toolkit_keys` and `toolkits`. It proceeds
  only when every one of those tables is empty (fresh installs), or a
  qualifying `toolkit_flattening_acks` row exists: written by 0.41's
  `flatten-toolkits --verify --acknowledge` (control migration
  `f2b3c4d5e6a7` marks earlier rows non-qualifying), and not stale — toolkit
  rows added or removed after the acknowledgement (for example edits served
  by a 0.40 replica during a rolling upgrade) refuse the drop. It also
  refuses while any `toolkit_keys` row is live and unmigrated (`revoked =
  false` and no `migrated_actor_id`). On refusal it names the reason, leaves
  the toolkit tables untouched, and the runner exits `1` with a traceback
  whose last line is the `RuntimeError` message; complete
  [flatten, verify and acknowledge](#flatten-verify-and-acknowledge) and
  re-run. To use `retire-toolkit-keys` after a refusal, downgrade control to
  `e1a2b3c4d5f6` on the 0.41 image (`python -m jentic_one.migrations.run --db
  control --direction down --target e1a2b3c4d5f6`), run the command on
  0.40.x, then upgrade again.
- **Enterprise deployments: apply the overlay migration first.** On
  PostgreSQL the drop also refuses while a table outside the toolkit set
  still holds a foreign key into `toolkits` (the enterprise
  `toolkit_user_grants` FK). Apply the jentic-one-enterprise migration that
  drops that table (`d47c3a91be02`) first; the error message names it.
- **Admin DB.** `agent_toolkit_bindings` is dropped behind an analogous gate.
  When the migration's connection can read the control schema's
  `toolkit_flattening_acks` (PostgreSQL, one database, a role with access to
  both schemas), it applies the same acknowledgement rule. Otherwise (the
  default per-surface roles, separate databases, or SQLite) it requires at
  least one row in `agent_credential_bindings` and relies on the control
  drop, which runs first, for the strict check. The retired `toolkits:read`
  / `toolkits:write` / `owner:toolkits:read` permission strings are swept
  from every stored grant and token surface; they granted nothing.
- **Toolkit key retirement ends.** The `jentic_one retire-toolkit-keys`
  command, the control-plane boot retirement task and the
  `theme5_retire_toolkit_keys` / `theme5_flatten_toolkits` upgrade steps are
  gone. `--skip-upgrade-step` still accepts those two names so an old Helm
  `migrate.extraArgs` keeps parsing, but they do nothing. A `jntc_live_` key
  migrated before the upgrade keeps authenticating as its successor agent
  until the [deprecation window](#deprecations) closes; rotate holders
  flagged by the `deprecated_toolkit_key_used` WARNING to a `jak_` key minted
  for their successor agent.
- **The `Jentic-Toolkit-Id` header is gone** on both sides. A request that
  still sends it is served normally; the broker strips the header rather
  than forwarding it upstream. Attribution rides `Jentic-Credential-Id` /
  `Jentic-Credential-Name`. The `tracestate` vendor value keeps its
  five-field shape; the second (toolkit) segment is always `_`.
- **The `broker.direct_bindings_enabled` config key is deleted.** A leftover
  `true` (the 0.40 default) is ignored; a leftover `false` — in the config
  file or as `JENTIC__BROKER__DIRECT_BINDINGS_ENABLED` — fails config
  validation at boot with a message naming the key. Remove it.
- **Historical executions keep their toolkit names.** `toolkit_name` is
  denormalized onto `execution_records` (backfilled by 0.41's
  `flatten-toolkits`). Records whose toolkit was deleted before the backfill
  show `null`.

### Service-account table drop

0.41.0 drops the `service_accounts`, `service_account_credentials` and
`service_account_migration_acks` tables (admin migration `e2f3a4b5c6d7`).

- **The retirement is automatic.** On a full upgrade (`python -m
  jentic_one.migrations.run` with no `--db` or `--target`), the runner
  migrates control, brings admin to `d1e2f3a4b5c6` (just before the drop),
  and then, under a lock:
  1. **Migrates** every service account not yet migrated: its permission
     grants, credential bindings, control-DB inline permission rules and
     API-key digest are copied to a successor agent named
     `service-account:<sva_ id>`, and outstanding service-account sessions
     are revoked. Non-active service accounts are stamped without a
     successor. For a service account 0.40 already migrated, grants and
     bindings added to it **after** its migration are copied to the
     successor too, unless the successor is archived or gone, or the audit
     log shows the successor had that permission removed or that binding
     purged. Nothing is ever copied onto an existing successor grant,
     binding or rule list.
  2. **Verifies** before touching anything: no failed migration, nothing
     left unmigrated, every grant and binding present on the successor
     (except the withheld ones above), and, for the service accounts
     migrated in this run, the same inline rules per binding.
  3. **Sweeps** the service-account side (grants, bindings, key digests,
     sessions, `sva_`-keyed inline rules), then applies the drop.

  It logs one `service_account_migration` line per service account and a
  `service_account_retirement_done` summary. When it found service accounts,
  it prints a WARNING summary and logs `service_account_keys_retired`: each
  service account id and its successor agent id (never a key). Every grant,
  binding or inline rule list it deliberately did **not** copy is printed as
  a `==> WARNING (service-account retirement, not copied)` line and logged as
  `service_account_retirement_not_copied`; review each and re-grant on the
  successor agent whatever it still needs. A second run finds nothing to do.
  There is no command to run and no acknowledgement.
- **Targeted or partial upgrades skip the retirement.** With `--db` or
  `--target` (on the runner or `scripts/migrate.sh`), the drop migration's
  own gate refuses while any service account is unmigrated or unswept,
  naming the ids; nothing is dropped. A fresh install passes.
- **`sak_` keys stop working.** Every `sak_` key is refused before any
  lookup with a `401` whose detail names the retirement and the `jak_`
  replacement (admin, auth and broker surfaces; MCP answers with its usual
  `401` challenge). Each refusal logs `retired_service_account_key_refused`
  at INFO with the successor agent id while that agent still holds the
  retired key's digest (`null` once you mint a new `jak_` key for it).
- **Converted `jntc_live_` keys keep working** until the
  [deprecation window](#deprecations) closes, as their successor agent. A key
  that matches no agent is refused (`401`, `retired_key_unresolved` INFO),
  and so is one whose successor is disabled or whose digest was rotated away
  (`migrated_key_fail_closed`).
- **Leftover service-account rows fail closed.** Revoked or expired
  service-account token rows are kept, and every token path refuses them.
  Historical `sva_` ids in audit, event, execution and control-DB rows are
  labelled "retired service account"; a catalog auto-import initiated by one
  is skipped.
- **Retired permission strings are swept.** `service-accounts:read`,
  `service-accounts:write` and `owner:service-accounts:read` are removed from
  every stored grant and token surface.
- **Removed:** the `jentic_one migrate-service-accounts` command and the
  control-plane boot migration job; the
  `services.service_account_sweep_min_stamp_age_hours` config key (ignored
  with one `config_retired_setting_ignored` WARNING — remove it and
  `JENTIC__SERVICES__SERVICE_ACCOUNT_SWEEP_MIN_STAMP_AGE_HOURS`); the
  `service_account_fallback_resolve` WARNING and the
  `auth_service_account_fallback_resolves` OTel counter; the
  `service_account` value of `ActorType` (`MeServiceAccount` is gone from
  `GET /me`); and the CLI's `service-account` mode alias (`--mode
  service-account`, `JENTIC_MODE=service-account` and a persisted `mode:
  service-account` are an unknown mode; use `agent`).

### If the service-account retirement refuses

The runner exits `4` and prints `Refusing to retire the service accounts …`,
listing each service account id with the reason (for example, its key digest
already belongs to another agent, or the successor lacks a grant). A
`service_account_retirement_refused` ERROR log carries the same ids.
**Nothing was swept or dropped**: admin stays at `d1e2f3a4b5c6` and every
service-account row is intact. Fix the named rows — typically restore the
missing grant or binding on the successor agent, or clear the conflicting
credential — then re-run the migration. Re-running is always safe.

### Rollback from 0.41

Check how far the upgrade got with `python -m jentic_one.migrations.run
--check` (the `STATUS admin` line).

- **Admin is past `e2f3a4b5c6d7`** (the upgrade completed): the
  service-account drop is irreversible — its downgrade refuses with
  `e2f3a4b5c6d7 is irreversible: …` and changes nothing. Restore **both**
  the admin and the control databases from the pre-upgrade snapshots, then
  roll the code back to 0.40.x. The snapshots hold the 0.40.x schema, so
  there is nothing to downgrade or re-import; changes written since the
  upgrade are lost. Restoring admin alone is not a rollback: the retirement
  deleted `sva_`-keyed inline permission rules from control. Do not `alembic
  stamp` past the drop to force a downgrade.
- **Admin stopped at `d1e2f3a4b5c6` or below** (for example, the
  retirement refused): downgrade on the 0.41 image first — 0.40.x does not
  ship 0.41's revision scripts (control `f2b3c4d5e6a7` / `f3c4d5e6a7b8`,
  admin `c0e1f2a3b4c5` / `d1e2f3a4b5c6`) and cannot start against a database
  stamped with them:
  1. **Downgrade, on the 0.41 image**, to the last revisions 0.40.x knows:
     `python -m jentic_one.migrations.run --db control --direction down
     --target e1a2b3c4d5f6` and `python -m jentic_one.migrations.run --db
     admin --direction down --target 3e7a91c4b2d8`. The drops' downgrades
     recreate the five toolkit tables **empty**; the admin one also drops
     `execution_records.toolkit_name`, and the control one the
     acknowledgement evidence columns.
  2. **Re-import, on the 0.41 image:** `jentic_one export-toolkits --import
     toolkit-export.json` with the export from step 2 of [0.41.0](#0410).
     Never skip it: the toolkit path with an empty
     `toolkit_permission_rules` is a total default-deny outage for every
     agent on it. The import is additive and idempotent by primary key.
  3. **Only then roll the code back** to 0.40.x.

The permission-string sweeps are not reversed; the swept strings granted
nothing.

## 0.40.0

0.40.0 replaces toolkits with direct agent↔credential bindings and migrates
service accounts to agents. The toolkit and service-account tables survive
0.40.x; [0.41.0](#0410) drops them.

### Toolkit flattening

- **The migration run performs the toolkit → direct-binding cutover.** The
  broker authorizes on direct agent↔credential bindings, so every
  toolkit-bound agent needs its direct bindings before 0.40 serves traffic.
  On the 0.40.x image, `python -m jentic_one.migrations.run` runs two
  one-shot upgrade steps once every database is at head, printing one
  `==> upgrade step <name>: <action>` line (plus a JSON summary) for each:
  1. `theme5_retire_toolkit_keys` migrates every resolvable `jntc_live_` key
     to a successor agent;
  2. `theme5_flatten_toolkits` derives a direct binding, carrying the pair's
     rules, for every `(agent, credential)` pair reachable through a
     toolkit. It runs once per install (recorded in the control DB's
     `upgrade_steps` table).

  Nothing extra to run on Helm, `jenticctl update`, or a hand-run migration.
  **Only a failed flatten blocks the upgrade**: the runner exits `4`; fix the
  logged cause and re-run. A key-retirement problem prints a `==> WARNING`
  line naming the recovery command, and the 0.40.x control plane retries it
  at every boot. `created_default_deny` in the summary counts pairs bound
  with no allow rule (conflicting toolkit rules, or a rule-less pair); run
  `jentic_one flatten-toolkits --diff-only --report report.jsonl` for the
  full lines. These two steps exist only on 0.40.x images; later images
  accept their names in `--skip-upgrade-step` and ignore them.
- **Toolkit changes during a rolling upgrade are not flattened.** A toolkit,
  toolkit binding, or agent–toolkit binding created on the old version after
  the migration's flatten has no direct binding. Freeze toolkit edits for the
  rollout, or re-run `jentic_one flatten-toolkits` once 0.40 is live (it only
  adds the missing pairs).
- **`jntc_live_` toolkit keys are retired.** No new keys are issued. The
  migration run (and every 0.40.x control-plane boot) migrates each key
  digest to a successor agent, and the unchanged plaintext keeps
  authenticating as that agent for the [deprecation window](#deprecations).
  Keys with no resolvable owner are skipped and reported with a `==>
  WARNING` line: **their holders stop authenticating** until you run
  `jentic_one retire-toolkit-keys --owner <admin-email>`. Watch
  `deprecated_toolkit_key_used` WARNING logs and rotate holders to the
  successor agent's `jak_` key.
- **Docker images run as uid `10001`** (was `999`). `jenticctl` re-owns its
  SQLite data volume on `update` and `start`; for a hand-run `docker run`
  with a SQLite volume, run `docker run --rm --user 0:0 --entrypoint chown -v
  <volume>:/data <image> -R 10001:10001 /data` once before starting 0.40
  (otherwise: `attempt to write a readonly database`). Postgres installs are
  unaffected.
- **The toolkit management surface is gone.** All `/toolkits/*` and
  `/agents/{id}/toolkits*` routes return `404`. The `toolkits:read`,
  `toolkits:write` and `owner:toolkits:read` permissions grant nothing and
  are tolerated in stored grants. Access is managed on the agent↔credential
  axis: the agent's **Access** tab in the UI, or
  `POST /agents/{agent_id}/credentials`.
- **CLI: the access-request `jentic access` commands are removed.** Agents
  ask for access with `jentic connect <vendor>` (`POST /integrations:connect`),
  which a human approves. (The `jentic access` group in later releases is a
  different command: it shows and changes what a credential binding allows.)
- **Broker headers.** Requests are disambiguated with `Jentic-Credential-Name`
  or `Jentic-Credential-Id` (the id is authoritative); responses attribute
  the credential used via the same two headers. `Jentic-Toolkit-Id` is gone
  from 0.41.0.

### Service-account migration

- **Migration is automatic.** The 0.40.x combined/control server copies every
  service account to a successor agent at startup — stored permission
  grants, credential bindings, their inline permission rules, and the API-key
  digest — revokes its outstanding sessions, and stamps the row
  (`migrated_to_actor_id`). Non-active service accounts are stamped without a
  successor; disabled ones get a disabled successor. `jentic_one
  migrate-service-accounts --diff-only --report preview.jsonl` (0.40.x only)
  previews the result; 0.41.0 migrates and sweeps anything left over.
- **The service-account surface is removed.** Every `/service-accounts…`
  route and `POST /oauth/mint` return `404`; `grant_type=client_credentials`
  on `POST /oauth/token` returns `400 unsupported_grant_type` and is gone from
  `grant_types_supported`. Outstanding service-account access and refresh
  tokens introspect inactive and are refused at the broker. Move
  client-credentials holders to the successor agent's `jak_` key (or the
  jwt-bearer grant). `service-accounts:read`, `service-accounts:write` and
  `owner:service-accounts:read` grant nothing; `GET /actors` lists users and
  agents only; `jentic api endpoints --actor service_account` matches
  nothing (use `--actor agent`).
- **API keys keep working as the successor agent.** Migrated `sak_` and
  `jntc_live_` plaintexts authenticate as the successor agent on 0.40.x
  (`sak_` stops working in 0.41.0). As agents, they cannot pass `agent_id` in
  a `POST /integrations:connect` body, and an agent-initiated connect session
  needs a human on the review page. Rotating or revoking the successor's key
  ends the old plaintext.
- **To cut a migrated key, disable the successor agent** (or revoke its API
  key). The key then fails closed: it never falls back to the service-account
  row (`migrated_key_fail_closed` WARNING).
- **Ownership and visibility shift.** The successor is created with
  `parent_actor_id` = the service account's owner, so it is visible to
  `owner:agents:read` holders, and a copied `owner:*` permission widens to
  the **owner's** resources — review service accounts holding `owner:*`
  grants. Control-DB objects `created_by` the `sva_` id (credentials) are not
  re-attributed, so the successor loses owner-scoped access to them;
  re-create or re-assign them if needed.
- **Broker JWTs may only assert `actor_type=agent`.** A trusted-issuer JWT
  claiming `service_account` is refused (uniform `401`; `jwt_refused` WARNING
  with `jwt_actor_type_not_allowed`). Re-issue such tokens for the successor
  agent.

## Reviewing grants and bindings carried over by the upgrade

The toolkit flattening and the service-account migration preserve access
exactly: nothing is stripped. Two kinds of carried-over access are worth a
deliberate review afterwards:

- **Admin-level permissions on successor agents.** A migrated service
  account's grants are copied verbatim onto its `service-account:<sva_ id>`
  successor agent, including `org:admin`, `users:write`, `agents:write`,
  `credentials:write`, `config:write` and `oauth-clients:write`.
- **Cross-owner credential bindings.** A binding created by flattening or
  key retirement where the credential's creator is neither the agent's owner
  nor the agent itself (or where either side is unrecorded).

Where the upgrade reports them (all informational; no secret material is
logged):

| Source | What it emits |
| ------ | ------------- |
| Service-account retirement (0.41 migration runner; the 0.40.x boot job) | `copied_scopes` and `admin_level_scopes` per service account on the `service_account_migration` log line, one `service_account_migration_admin_scope_copied` WARNING per admin-level grant, and the copied permission names in the migration's grant audit row. |
| Toolkit flattening (0.40.x upgrade step, or `flatten-toolkits`) | `cross_owner_binding` findings in the report (run and `--verify`), a `toolkit_flattening_cross_owner_binding` WARNING per binding, and an `==> WARNING` line from the upgrade step. |
| Toolkit key retirement (0.40.x upgrade step or boot) | `cross_owner_credential_ids` in the step outcome, a `toolkit_key_retirement_cross_owner_binding` WARNING per binding, and an `==> WARNING` line from the upgrade step. |

The same state can be listed at any time with read-only queries.

**Successor agents holding admin-level grants** (admin DB; on Postgres run it
with the admin connection's `schema_name` — `admin` in the shipped configs —
on the `search_path`, or prefix the tables with it):

```sql
SELECT a.id, a.name, a.owner_id, a.status, g.permission, g.granted_by
FROM actor_permission_grants g
JOIN agents a ON a.id = g.actor_id
WHERE g.actor_type = 'agent'
  AND a.name LIKE 'service-account:%'
  AND g.permission IN ('org:admin', 'users:write', 'agents:write',
                  'credentials:write', 'config:write', 'oauth-clients:write')
ORDER BY a.id, g.permission;
```

`granted_by = 'system:theme8-sa-migration'` marks a grant the migration
copied; any other grantor means someone has granted it since.

**Bindings whose credential creator differs from the agent owner.** The
bindings live in the admin DB and the credential creators in the control
DB, so on SQLite (one file per surface) this is two queries. On Postgres,
when the admin and control connections share one database (the shipped
configs, schemas `admin` and `control` — substitute your `schema_name`
values), it is one join; if they point at separate databases, use the
two-query form.

```sql
-- Postgres, shared database: one query across the admin and control schemas
SELECT b.agent_id, a.name, a.owner_id, b.credential_id, c.created_by
FROM admin.agent_credential_bindings b
JOIN admin.agents a ON a.id = b.agent_id
LEFT JOIN control.credentials c ON c.id = b.credential_id
WHERE c.created_by IS NULL
   OR a.owner_id IS NULL
   OR c.created_by NOT IN (a.owner_id, a.id)
ORDER BY b.agent_id, b.credential_id;
```

```sql
-- step 1 (admin DB): every binding with its agent's owner
SELECT b.agent_id, a.name, a.owner_id, b.credential_id
FROM agent_credential_bindings b
JOIN agents a ON a.id = b.agent_id
ORDER BY b.agent_id;

-- step 2 (control DB): the creators of those credentials
SELECT id, created_by FROM credentials WHERE id IN ('cred_…', …);
```

Flag a step-1 row when its credential's `created_by` (step 2) is missing or
is neither the row's `owner_id` nor its `agent_id`.

**What to do.** Expected rows need no action — the upgrade kept the access
the toolkit model already allowed. For an unexpected one:

- narrow the agent's permissions with `PUT /agents/{agent_id}/permissions`
  (the body is the full permission set to keep), or disable the agent while
  you decide;
- remove a binding with
  `DELETE /agents/{agent_id}/credentials/{credential_id}`.

Both are ordinary, audited mutations.

## Deprecations

Active deprecation windows are registered here and repeated in the GitHub
Release notes of the release that opens each window. An entry names what is
deprecated, the release that opened the window, the runtime signal an
operator can watch, and the earliest removal point.

| Deprecated | Since | Runtime signal | Removal |
| ---------- | ----- | -------------- | ------- |
| `jntc_live_` toolkit API keys. No new keys are issued; a key migrated to a successor agent (on 0.40.x) authenticates as that agent. A key not migrated before 0.41.0 no longer authenticates. Rotate holders to the successor agent's `jak_` key. | 0.40.0 | `deprecated_toolkit_key_used` WARNING log lines — one per resolve, naming the successor agent. | Plaintext acceptance ends no earlier than **2026-12-01**. |
| The `broker.resilience.backend` config section and its `JENTIC__BROKER__RESILIENCE__BACKEND__*` env vars. Still read into the top-level `state` section; a key set under both paths to different values fails config loading. Move to `state.*` / `JENTIC__STATE__*`. | 0.44.0 | One `config_deprecated_setting` WARNING per process with `setting=broker.resilience.backend`. | The next minor release after 0.44.0. |
| The built-in `redis` shared-state backend (`state.backend: redis`). Run one process per surface on `memory`, or register a shared backend with `register_state_backend`. | 0.44.0 | One `state_backend_deprecated` WARNING per process at startup. | No earlier than the next minor release after 0.44.0. |
