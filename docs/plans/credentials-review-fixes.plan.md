# Credentials Branch — Review Fixes Plan

Branch: `ia/credentials-on-monitor` (14 commits ahead of `origin/main`)

## Context

A five-agent review (backend security/auth, backend credentials/vault, frontend
credentials UI, frontend toolkit/workspace UI, tests & API contract) audited the
~98 files / ~9.8k lines on this branch. **No CRITICAL issues.** The kill switch
(agent + human), permission-dedup fix, secret non-leakage, SQL parameterization,
and header hygiene were all verified clean.

This plan captures every actionable finding, grouped by priority. Each item lists
the file:line, the fix, and how to verify. Items are independently shippable.

Severity legend: **P0** = merge blocker · **P1** = high user-visible / should-fix ·
**P2** = polish · **P3** = nit / optional.

---

## P0 — Merge blockers (backend security)

### P0-1. Gate `POST /credentials/{id}/test` + add SSRF guard
**Files:** `src/routers/credentials.py:354` (handler), `:380-415` (probe), `_pick_probe_url` `:755-810`; helper in `src/routers/apis.py:166`.

Two agents flagged the same endpoint. It currently takes only `cid` — no `request`,
no auth dependency, no grant check — and issues an authenticated outbound GET to a
URL derived from user-controlled `api_id`/`routes`/spec, with the credential's auth
header attached.

**Fix (two parts):**
1. **Authorization.** Add `request: Request` and gate it: allow human session, else
   require the same credential-policy/grant check the write endpoints use
   (`_agent_has_credential_write_permission` pattern, `credentials.py:578-585`), so an
   agent can only probe a credential bound to a toolkit it was granted. Reject
   otherwise with 403.
2. **SSRF guard.** Before the `httpx` GET (`:414`), resolve the probe host and reject
   private/loopback/link-local/reserved destinations. Reuse `is_private_server_url`
   (`apis.py:166`) **after hardening it** (see P2-5) — it currently misses
   `169.254.169.254` (cloud metadata), IPv6 (`::1`, `fc00::/7`, `fe80::/10`),
   `100.64.0.0/10`, and DNS names that resolve to private IPs.

**Verify:** new tests — (a) agent key without grant → 403; (b) probe URL resolving to
`169.254.169.254` / `127.0.0.1` / `10.x` → rejected (no outbound call); (c) human
session happy path still returns the structured probe result.

### P0-2. Reject reserved `auth_type` values at create/patch
**Files:** `src/models.py:50-52` & `:100-102` (Literal includes `pipedream_oauth`,
`JenticApiKey`); `src/routers/credentials.py` create (`:110-347`) and patch handlers.

Docs (`models.py:67-68`, `credentials.py:139`) and a test comment
(`tests/test_credentials_tier1.py:108`) claim these are rejected, but the handler
never rejects them and Pydantic accepts them — an agent/human can self-assign the
reserved Pipedream/internal-admin markers, creating unsupported states with no
backing `oauth_broker_accounts` row.

**Fix:** In the create and patch handlers, explicitly reject the reserved values:
`if scheme_name in ("pipedream_oauth", "JenticApiKey"): raise HTTPException(400, ...)`.
(Keep them in the output/serialization `Literal` for Pipedream-sync-written rows, but
reject on the write path.)

**Verify:** update `test_credentials_tier1.py` to assert `POST /credentials` with
`auth_type="pipedream_oauth"` → 400 (currently it works around this via a direct
vault write, falsely implying rejection). Add the same assertion for `JenticApiKey`.

---

## P1 — Should-fix (frontend staleness + silent failures)

### P1-1. Broaden cross-surface query invalidation
**Files:** `ui/src/components/toolkits/ToolkitDetailBody.tsx:330-342` (`unbindMutation`),
the permission `updateMutation`, `ui/src/components/credentials/BindExistingCredentialDialog.tsx:79-86`,
`ui/src/components/credentials/AddCredentialDialog.tsx:81-86`,
`ui/src/components/credentials/CredentialsList.tsx:116-127` (delete).

Mutations only invalidate their local scope, leaving host surfaces stale:
- Sheet bind/unbind/permission-save → host `ApiDetailPage` keys
  (`['credentials']`, `['toolkit-api-bindings']`), `['toolkit-card-enrichment']`,
  `['workspace']` not refreshed → stale counts/lists.
- Credential bind dialogs → `['credential-bindings', credentialId]` not invalidated →
  "Used by" chips stale up to 30s.

**Fix:** In each `onSuccess`, add prefix invalidations for the affected keys
(`['credentials']`, `['credential-bindings', credentialId]`, `['toolkit-api-bindings']`,
`['toolkit-card-enrichment']`, `['workspace']`). Prefer prefix matching (default).

**Verify:** extend `ToolkitDetailPage.test.tsx` / `WorkspacePage.test.tsx` (or RTL
tests with a spy on `invalidateQueries`) to assert the expanded key set after
bind/unbind/save.

### P1-2. Add `onError` to silent mutations
**Files:** `CredentialsList.tsx:116-127` (delete — no `onError`),
`OAuthBrokerFields.tsx:46-59` (connect-link).

A failed delete currently stops the spinner, keeps the dialog open, and shows nothing.

**Fix:** add `onError` toasts (matching the existing toolkit-mutation pattern), decide
dialog-open behavior on failure.

**Verify:** test that a 403/network rejection on delete surfaces a toast.

### P1-3. `CredentialEditSheet` — remount form per credential
**File:** `ui/src/components/credentials/CredentialEditSheet.tsx:138`.

`<CredentialFormFields>` has no `key`, so switching from editing row A to row B while
the sheet stays open can briefly render A's local state — including a half-typed
secret — before the hydrate effect fires.

**Fix:** add `key={credentialId}` to `<CredentialFormFields>` (forces remount), or
hard-reset all local state in a `[credentialId]` effect inside `CredentialFormFields`.

**Verify:** RTL test — open edit for A, type into secret, switch to B without closing,
assert B's secret field is empty.

### P1-4. Wire or remove the `n` keyboard shortcut
**File:** `ui/src/pages/CredentialsPage.tsx:115-118` (PageHelp) & `:154`
(KeyboardShortcutsBar) advertise `n` → "Add credential", but no handler binds it.

**Fix:** either add a `keydown` listener calling `addDialog.openWorkspace()` (with the
standard "skip while typing in an input" guard), or remove `n` from both arrays.

### P1-5. De-duplicate `useToolkitCardEnrichment` fan-out
**Files:** `ui/src/hooks/useToolkitCardEnrichment.ts:26-60`,
`ui/src/components/workspace/WorkspaceView.tsx:275-303`.

The hook fires 2·N requests per list render (JSDoc wrongly says "batched/deduped"),
its sorted-id-string cache key prevents per-toolkit invalidation, and on Workspace it
double-fetches `listToolkitCredentials` (the hook + the existing
`['workspace','toolkit-credentials']` query).

**Fix (pick one):**
- (a) Key each toolkit's enrichment as its own query `['toolkit-enrichment', id]` so
  they dedupe with `WorkspaceView`'s per-toolkit query and invalidate individually
  (also unblocks P1-1's `toolkit-card-enrichment` invalidation), **or**
- (b) Reuse the existing `['workspace','toolkit-credentials']` result on Workspace
  instead of re-fetching.

Update the misleading JSDoc (P3 N2) as part of this.

**Verify:** assert per-toolkit query keys; confirm no duplicate `/toolkits/:id/credentials`
calls on Workspace (request spy / msw call count).

---

## P2 — Polish

### P2-1. Kill-switch confirm popover a11y
**File:** `ui/src/components/toolkits/ToolkitKillSwitch.tsx:71-83`.
Most consequential a11y gap (destructive control). Add `aria-expanded={confirming}`
and `aria-controls` on the toggle, `role="group"` + `aria-label` on the confirm row,
and move focus to the confirm button when `confirming` flips true.

### P2-2. Settings dialog re-seed clobbers in-progress edits
**File:** `ui/src/components/toolkits/ToolkitDetailBody.tsx:362-367`.
The effect re-seeds `editName`/`editDesc` from server data on the 30s background
refetch while the dialog is open (violates `dialog-state-lifecycle.mdc`). Seed once on
the open transition (track previous `showSettings`/toolkit id with a ref).

### P2-3. Suspended-state pill consistency
**File:** `ui/src/components/toolkits/ToolkitDetailBody.tsx:870-875` vs `:540`.
"Agents blocked" is gated on `agents.length > 0` while "Keys blocked" shows whenever
`toolkit.disabled`. Drop the `agents.length > 0` guard (or apply the same guard to the
keys pill) so suspended messaging is symmetric across all three sections.

### P2-4. Gate `GET /audit` and `GET /credentials/{id}/bindings` to human session
**Files:** `src/routers/credentials.py:816` (`audit_router`), `:457` (`/bindings`).
Both are agent-reachable today (info disclosure: client IPs, lifecycle history,
credential→toolkit topology). Add `dependencies=[Depends(require_human_session)]` to
`audit_router`; gate `/bindings` to human session (or document it as intended like the
list endpoint does).

### P2-5. Harden `is_private_server_url` as an SSRF primitive
**File:** `src/routers/apis.py:166-191`. Parse with `ipaddress` and check
`.is_private or .is_loopback or .is_link_local or .is_reserved`; for hostnames,
resolve and check every returned address. Covers `169.254.0.0/16`, IPv6, CGNAT.
(Prerequisite for P0-1's guard; also hardens existing broker/import callers.)

### P2-6. Make local cascade deletes atomic
**Files:** `src/routers/credentials.py:586-633`, `oauth_brokers.py:985-1048`,
`:1213-1267`, `:751-848`. The Pipedream cascade spans multiple `get_db()` contexts /
commits; a mid-sequence crash can orphan an encrypted credential row. Do the local
deletes in a single transaction (one commit) and run the best-effort upstream revoke
**after** the local commit (as `delete_oauth_broker` partly does). Document the
"local is source of truth" ordering invariant.

### P2-7. Killswitch broker loop: skip redundant N+1 for agent path
**File:** `src/routers/broker.py:681-688`. `auth.py` already filtered disabled toolkits
out of `grant_ids`, so the per-grant `SELECT disabled` loop (new DB conn each
iteration) always passes for agents. Skip the loop when `grant_ids` is set; only check
the single `toolkit_id` for the `tk_` key path.

### P2-8. Attribute denial trace to the suspended toolkit (agent path)
**Files:** `src/routers/broker.py:650-652`, `auth.py:327`. On the all-grants-suspended
path `request.state.toolkit_id` is `None`, so the denial trace isn't filterable by the
suspended toolkit. Pass the known `_tid` into the trace attribution.

---

## P3 — Nits / optional

- **P3-1.** Type-safety: replace `as any` / phantom-typed spots at API boundaries —
  `CredentialFormFields.tsx:248` `(created as any).id`, `ToolkitDetailBody.tsx` pervasive
  `any` (lines 87, 91, 110, 125-126), `useApiSchemes.ts`/`useApiServerVarDefs.ts`
  `spec: any`. Use real `PermissionRule`/`CredentialOut` types where available.
- **P3-2.** Contract drift: remove phantom `scheme_name` from `types.ts CredentialOut`
  (`ui/src/api/types.ts:117`) — backend/openapi don't return it (only `CredentialBindingOut`
  does). Update `CredentialRow.tsx:148` accordingly. Add a comment noting the deliberate
  `auth_type` enum narrowing vs the generated client.
- **P3-3.** Endpoint-count comment (`tests/test_auth_boundary_comprehensive.py:377`):
  baseline is **95**; the comment folds in main's `/traces/usage`. Reword so it isolates
  this branch's endpoints (count is correct/deterministic; only the rationale is fuzzy).
- **P3-4.** `ConfirmInline` (`ui/src/components/ui/ConfirmInline.tsx:24-31`) overwrites the
  child's own `onClick` via `cloneElement` — compose (`children.props.onClick?.(e)` first)
  to avoid a latent footgun.
- **P3-5.** `useCredentialImportedSync.ts:78` re-subscribes on unstable `onImported` dep —
  capture in a ref or document that callers must memoize.
- **P3-6.** Dead code: `CredentialFormFields.tsx:230-231` `_schemeName` (computed then
  `void`-discarded); `CredentialEditSheet.tsx:127` `{!credentialId && null}` no-op.
- **P3-7.** Migration/audit docstrings cite alembic **0007** but `audit_events` is created
  in **0008** (`src/audit.py:14`, `0008_credentials_health.py:12`). Fix the cross-reference.
- **P3-8.** `PermissionRuleEditor.tsx:24` uses `key={i}` index keys — use a stable id per
  rule.
- **P3-9.** `vault._fernet()` (`src/vault.py:147-161`) silently swallows a bad
  `JENTIC_VAULT_KEY` and generates a new key, rendering existing ciphertext
  undecryptable — at least log a warning.
- **P3-10.** `useApiSchemes.ts:53-62` raw `fetch(specUrl)` has no timeout/abort → form can
  hang on a slow spec host. Add an `AbortController`.

---

## Test coverage gaps to close (from tests agent)

1. `GET /toolkits/{id}/agents`: soft-deleted (`deleted_at`) exclusion + grant-time
   ordering (control timestamps to assert deterministically); assert a `disabled`-status
   agent **is** returned.
2. Permission-save `onError` toast (`ToolkitDetailBody.tsx:149`) — no UI test.
3. `TestConnectionButton` component hint states (`pipedream_unsupported`/`timeout`/
   `unauthorized`).
4. `/credentials/{id}/test` priority-1 healthcheck branch (`with_healthcheck=True` helper
   exists but no test sets it).
5. Agent-without-permission **403** on `PATCH`/`DELETE /credentials` (only POST is
   indirectly covered).
6. Positive assertion that the killswitch live-grant routes through (M1 conditional
   assertions can pass vacuously).

---

## Suggested execution order (atomic commits)

1. **`fix(security)`** — P0-1 + P0-2 + P2-4 + P2-5 (+ their tests). One security-focused
   commit; the highest priority and self-contained on the backend.
2. **`fix(ui)`** — P1-1 + P1-2 + P1-3 (invalidation + onError + sheet key) + tests.
3. **`refactor(ui)`** — P1-5 (enrichment de-dup) + P3-2 (contract) + JSDoc.
4. **`fix(a11y/ux)`** — P1-4 + P2-1 + P2-2 + P2-3.
5. **`refactor(backend)`** — P2-6 + P2-7 + P2-8.
6. **`chore`/`test`** — P3 nits + coverage-gap tests.

Then split the existing `chore(credentials): wip safeguard before rebase` commit into
the intended atomic feature commits (separate task).
