# Credentials Branch — Audit Round 2 Fixes Plan

Branch: `ia/credentials-on-monitor` (30 commits ahead of `origin/main`, ~108 files / ~12.5k lines) · PR #499

## Context

A second five-agent audit (security review, backend code quality, frontend
credentials UI, frontend toolkit/workspace/shared UI, test coverage) ran over the
now-expanded branch after the first review round shipped. **No blockers.** 48
backend tests are green (`test_credential_health` + `test_credentials_tier1` →
24 passed; the other four new/changed suites → 24 passed). Migration 0008
idempotency, audit-log SQL parameterization (no secrets in `payload_json`),
tri-state coercion, broker health-write status mapping, route ordering,
reserved-auth rejection, kill-switch enforcement, and the `PermissionRuleEditor`
focus fix were all verified clean.

This plan captures every actionable finding, grouped by priority. Each item lists
the `file:line`, the fix, and how to verify. Items are independently shippable.

Severity legend: **P0** = merge blocker · **P1** = high user-visible / should-fix ·
**P2** = polish · **P3** = nit / optional.

> **Cross-cutting theme.** Three independent audits converged on the *same* class
> of bug: mutations that change credential/toolkit state don't invalidate the
> React Query keys that render it. This is the same stale-pill bug already fixed
> for `TestConnectionButton`, surviving in several other paths. P1-1 / P1-2 / P1-3
> all belong to this family and should be fixed together.

---

## P0 — Merge blockers

None. (The branch is mergeable; everything below is should-fix or polish.)

---

## P1 — High / should-fix

### P1-1. Manual-edit + OAuth-reconnect leave the open sheet showing stale health
**Files:** `ui/src/components/credentials/form/CredentialFormFields.tsx:254-261`
(`updateMutation.onSuccess`); `ui/src/components/credentials/OAuthConnectionDetailSheet.tsx:141-153`
(`reconnectMutation.onSuccess`).

Both invalidate `['credentials']` but **not** `['credential', id]`. The edit sheet
(`CredentialEditSheet.tsx:73`), form page (`CredentialFormPage.tsx:86`), and the
OAuth detail sheet's own `cred` query (`OAuthConnectionDetailSheet.tsx:78`) all read
the credential via `useQuery(['credential', id])`, so the StatusDot / broken-callout
stays stale after a save until staleTime/refetch. This is the **same bug**
`TestConnectionButton` was just fixed for — it survives in these two paths.

**Fix:** add `queryClient.invalidateQueries({ queryKey: ['credential', editId] })` to
`updateMutation.onSuccess` (and the created id in `createMutation.onSuccess`), and add
`queryClient.invalidateQueries({ queryKey: ['credential', credentialId] })` to
`reconnectMutation.onSuccess`. (The label/description mutations at `:113-128` already
do this — match them.)

**Verify:** edit a credential's health-relevant field → StatusDot in the open sheet
flips without a manual refresh; reconnect an OAuth grant → broken-callout clears.

### P1-2. Toolkit delete doesn't invalidate the surfaces its cascade mutates
**File:** `ui/src/components/toolkits/ToolkitDetailBody.tsx:368-372`
(`deleteMutation.onSuccess`).

Only `['toolkits']` is invalidated. The backend cascade also deletes API keys and
agent grants, so `['agents']`, `['toolkit-card-enrichment']`, `['workspace']`, and
`['access-requests']` go stale — the Agents page lists a grant to a deleted toolkit,
Workspace cards linger.

**Fix:** call the existing `invalidateToolkitSurfaces()` plus
`queryClient.invalidateQueries({ queryKey: ['agents'] })` before `onRequestClose()`.

**Verify:** delete a toolkit with keys + agent grants → Agents page and Workspace
grid update immediately.

### P1-3. Key + agent mutations leave toolkit card counts stale
**File:** `ui/src/components/toolkits/ToolkitDetailBody.tsx:306-309` (agent revoke),
`:321-326` / `:329-334` (key create/revoke).

Key mutations invalidate only `['toolkit-keys', toolkitId]`; agent revoke invalidates
`['toolkit-agents', toolkitId]` + `['agents']`. But `ToolkitCard` renders `key_count`
from `['toolkits']` and `agentCount` from `['toolkit-card-enrichment', id]`, neither of
which refreshes until the 30s poll.

**Fix:** add `['toolkit', toolkitId]` + `['toolkits']` to the key mutations and
`['toolkit-card-enrichment']` to the agent revoke (or route all three through a single
`invalidateToolkitSurfaces()` helper — see P2-8).

**Verify:** create/revoke a key and revoke an agent → card counts update at once.

### P1-4. `/test` SSRF guard is vulnerable to DNS-rebinding (TOCTOU)
**Files:** `src/routers/credentials.py:453` (guard call) → `:468-469` (httpx GET);
guard impl `src/routers/apis.py:180-214`.

The guard validates the host via `socket.getaddrinfo` then `httpx.get(probe_url)`
**re-resolves the name independently**. A host whose DNS the caller controls can answer
public during the check and private (e.g. `169.254.169.254`) during the request. Only
the status/hint is returned (not the body), so this is medium not high — but it can
leak the caller's own decrypted secret to an internal endpoint and probe metadata
status codes.

**Fix:** resolve the host once, verify all addresses are public, then connect to the
**pinned IP** (httpx transport with a fixed resolver, preserving the `Host` header), or
re-validate inside an httpx event hook on the actual connected peer. Eliminate the
validate-then-reconnect gap.

**Verify:** a test with a stub resolver returning public-then-private addresses is
blocked; happy path still probes the real host.

### P1-5. Headline fix (`TestConnectionButton` invalidation) and health write-back are untested
**Files (tests to add):** `ui/src/components/credentials/TestConnectionButton.test.tsx`
(new); `tests/test_credentials_tier1.py` / `tests/test_credential_health.py` (extend).

The behavior the branch is named for rests on static correctness only:
- No test for `TestConnectionButton.onSuccess` → `invalidateQueries(['credentials'])`
  + `['credential', id]` (`TestConnectionButton.tsx:58-59`).
- `/test` success → `healthy=true` (`credentials.py:505-507`) and 401/403 →
  `healthy=false` (`:508-509`) write paths are never driven by a real 2xx/401 probe.
- The broker write path (`broker.py:1308/1310/1441/1443`) has **zero** coverage.

**Fix:** add the three tests (frontend spy on `queryClient.invalidateQueries` mirroring
the `ToolkitDetailPage` unbind test; backend probes against 2xx/401 stubs asserting
`GET /credentials` reflects the verdict; a proxied broker call writing manual
`credentials.healthy`).

**Verify:** the new tests pass and fail if the invalidation / write-back is removed.

---

## P2 — Polish

### P2-1. Blocking `open()` + `yaml.safe_load` on the async `/test` path
**File:** `src/routers/credentials.py` `_load_api_spec` (~`:781+`).

Synchronous `open().read()` + parse of a potentially large spec inside an `async def`
on the `POST /credentials/{id}/test` path blocks the event loop.

**Fix:** wrap the read + parse in `await asyncio.to_thread(...)` (existing pattern in
`apis.py`).

### P2-2. Unbounded `description` field
**Files:** `src/models.py:23` (`CredentialCreate`), `:95` (`CredentialPatch`).

Plain `str | None`, no `max_length`; flows into `vault.create_credential` /
`patch_credential` and is echoed in `CredentialOut`.

**Fix:** `description: str | None = Field(default=None, max_length=2000)` on both.

### P2-3. Pipedream "never used" shows green, not unknown — doc/data contradiction
**Files:** `src/routers/credentials.py:783-790`; default `alembic/.../0001_baseline.py:307`;
docstring `src/models.py:374`.

Because `oauth_broker_accounts.healthy DEFAULT 1`, a freshly-synced Pipedream cred is
never NULL, so it reads green before any call — but `CredentialOut.healthy`'s docstring
claims "None = an OAuth account that hasn't been used yet."

**Fix:** either change the column default to NULL via a follow-up migration, or correct
the docstring to state Pipedream creds default to `healthy=True`. (Doc fix is lower
risk; pick one and make data + doc agree.)

### P2-4. `deriveCredentialStatus` can never return `unknown`; amber tone is untested
**Files:** `ui/src/components/credentials/credentialStatus.ts:38-41`;
`ui/src/components/credentials/TestConnectionButton.tsx` `pillTone` `:119-225`.

The shared helper only yields `ok`/`broken`/`neutral` (the test even asserts
`not.toBe('unknown')`). The amber `unknown` tone lives **only** in `pillTone` and is not
unit-tested.

**Fix:** add a `pillTone` unit test covering each hint → tone (incl. amber
`rate_limited`/`upstream_error`/`timeout`), and either document that
`deriveCredentialStatus` is intentionally 3-tone or unify the vocabulary.

### P2-5. Health-write amplification on the manual path
**Files:** `src/vault.py:560+` (`mark_credential_health`); compare `src/routers/broker.py:1152`
(Pipedream guards `AND healthy IS NOT 1`).

Manual `mark_credential_health` writes `healthy` + `health_checked_at` + `updated_at`
on **every** <400 call → a DB write per request, where the Pipedream path short-circuits.

**Fix:** add the same `healthy IS NOT 1` short-circuit (or only stamp
`health_checked_at` when the flag is unchanged) in `mark_credential_health`.

### P2-6. `mark_credential_health` has no Pipedream guard
**File:** `src/vault.py:560+`.

The UPDATE is keyed on `id` only. In practice the callers are mutually exclusive with
the Pipedream path, but a future caller passing a Pipedream cid would write a
`credentials.healthy` value that the list query then silently shadows (`oba.healthy`
wins).

**Fix:** add `WHERE id=? AND auth_type != 'pipedream_oauth'` to the UPDATE, or assert
the invariant at the callsite.

### P2-7. Duplicate private-host logic will drift
**Files:** hardened `src/routers/apis.py` `is_private_server_url` (IPv6/link-local/metadata
aware) vs the hand-rolled `10.`/`192.168.`/`172.x` string checks in
`src/routers/broker.py:1182-1189`.

**Fix:** have `broker.py` call `apis.is_private_server_url` (or extract to a shared util,
e.g. `src/routers/_shared.py`).

### P2-8. `ToolkitDetailBody` is a ~1100-line component owning 8 queries/mutations + 4 dialogs
**File:** `ui/src/components/toolkits/ToolkitDetailBody.tsx` (whole file).

Works and lints clean, but it's the natural seam for the invalidation gaps in P1-2/P1-3.

**Fix (non-urgent):** extract `KeysSection` / `CredentialsSection` / `AgentsSection` and
a `useToolkitMutations(toolkitId)` hook so invalidation policy lives in one place.

### P2-9. `bindMutation` can transition step after the dialog closed (race)
**File:** `ui/src/components/credentials/AddCredentialDialog.tsx:95-108`.

`bindMutation.onSuccess` calls `onGoToStep('confirm')` with no `state.open` guard; closing
mid-flight (which resets to `INITIAL_STATE`) lets a late success re-drive the reducer.

**Fix:** guard with `if (!stateRef.current.open) return;` or cancel the mutation on close.

### P2-10. `PipedreamCard` resync mutation has no `onError`
**File:** `ui/src/components/credentials/PipedreamCard.tsx:72-78`.

The top-level `syncMutation` (the Resync button at `:181-189`) surfaces failures only as
inline text in the configure state; a thrown sync otherwise silently no-ops.

**Fix:** add `onError` with a toast (use `messageFromApiError`), consistent with the other
mutations in the file.

### P2-11. `useCredentialImportedSync` re-subscribes BroadcastChannel on inline handlers
**File:** `ui/src/hooks/useCredentialImportedSync.ts:78`.

The effect depends on `opts.onImported`; an inline arrow changes identity every render,
tearing down and recreating both event subscriptions (now doubled by the `apiImported`
channel), with a window where events are missed.

**Fix:** capture `onImported` in a ref and depend only on `[queryClient]`.

### P2-12. Migration 0008 `downgrade()` is untested
**File:** `alembic/versions/0008_credentials_health.py` (downgrade); no test references it.

The downgrade drops 4 columns + the `audit_events` table + 3 indexes (relies on SQLite
≥3.35 `DROP COLUMN`); a broken rollback would only surface in production.

**Fix:** add an upgrade→downgrade→upgrade round-trip test against a temp DB asserting
columns/table drop and re-add cleanly.

### P2-13. Backend join-precedence + ambiguous-status coverage gaps
**File:** `tests/test_credentials_tier1.py` / `test_credential_health.py`.

- No test seeds both an `oauth_broker_accounts` row and a conflicting `c.healthy` to
  prove `oba` wins (the file's headline claim).
- The "429/5xx leaves `healthy` untouched" contract and `hint ∈ {rate_limited,
  upstream_error}` is never asserted.

**Fix:** add the two cases.

---

## P3 — Nits / optional

### a11y

- **P3-1. `StatusDot` double-announces / mis-uses `role="status"`.**
  `ui/src/components/credentials/StatusDot.tsx:92-94` — `HoverTooltip` `role="status"`
  (a live region) wraps a span with `role="img"` + `aria-label`. Drop `role="status"`
  (let the tooltip default to `tooltip`); keep the single `role="img"`+`aria-label`.
- **P3-2. Health signaled by color only in the dot.**
  `StatusDot.tsx:55-63,95-97`; label text not rendered beside the dot in rows
  (`CredentialRow.tsx:122-126`). Add a per-tone shape/icon or render the short `label`
  next to the dot in list contexts (WCAG 1.4.1).
- **P3-3. Dangling `aria-describedby` on the credential-delete dialog.**
  `ui/src/components/ui/ConfirmDeleteDialog.tsx:56,135-193` — only the
  `CredentialCascadeInfo` branch isn't passed `descriptionId`. Pass it and set
  `id={descriptionId}` on its lead `<p>` (`:154`).
- **P3-4. Inline confirms lack Escape / focus-restore.**
  `ui/src/components/ui/ConfirmInline.tsx:38-61` and
  `ui/src/components/toolkits/ToolkitKillSwitch.tsx:91-139` — add an Escape→disarm handler
  and restore focus to the trigger on cancel/close.
- **P3-5. `VendorPile` `aria-label` on a role-less `<div>`.**
  `ui/src/components/discovery/VendorPile.tsx:44-52` — add `role="img"`/`role="group"`
  to the container; the inner `VendorIcon`s have no alt text.
- **P3-6. No dedicated `StatusDot` a11y test.** Add `StatusDot.test.tsx` asserting
  `aria-label` + the four tone classes.

### Backend tidy-ups

- **P3-7. `audit_events.id` is only 32-bit.** `src/audit.py:35` `secrets.token_hex(4)`;
  a PK collision raises inside the swallowed `try`, silently dropping the row. Use
  `token_hex(8)`.
- **P3-8. `persist_audit` swallows missing-table as a transient warning.**
  `src/audit.py:103` — detect "no such table" specifically and log at `error` (or a
  one-time startup check that 0008 is applied).
- **P3-9. Dead `audit_log` binding.** `src/routers/credentials.py:23` still binds the
  legacy logger though all callsites now use `persist_audit`. Remove it.
- **P3-10. Reserved-auth guard duplicated.** `credentials.py:171-179` and `:592-599` are
  identical — extract `_reject_reserved_auth_type(auth_type)`.
- **P3-11. `_pick_probe_url` ignores explicit healthcheck when servers are templated /
  is GET-only.** `credentials.py` (`_pick_probe_url` branches) — fall back to
  `https://{fallback_host}{path}` when an operation-level `x-jentic-healthcheck` exists
  but `base is None`, and consider including `head` in the auto-probe scan.
- **P3-12. Redundant post-cascade 404 branch.** `src/vault.py` `delete_credential_cascade` —
  caller already did `get_credential(cid)`, so the second 404 check is dead. Optional.

### Frontend tidy-ups

- **P3-13. `any`-typed scheme/spec plumbing.** `ui/src/hooks/useApiSchemes.ts:34,51,73-83`
  and `useApiServerVarDefs.ts:40-53` — type `spec` as a minimal interface; reuse
  `security_schemes` from `client.ts:121`.
- **P3-14. Server-var `<Select>` uncontrolled→controlled risk.**
  `ui/src/components/credentials/form/ServerVariablesFields.tsx:60` +
  `CredentialFormFields.tsx:194-208` — seed enum vars with no default to `enum[0]` so
  display and state agree.
- **P3-15. `TestConnectionButton` result pill not reset on `credentialId` change.**
  `TestConnectionButton.tsx:46-47` — add `useEffect(() => setResult(null), [credentialId])`
  for host-reuse safety.
- **P3-16. `pillTone` default maps unknown hints to `no_probe_url` copy.**
  `TestConnectionButton.tsx:213-225` — add a generic default distinct from `no_probe_url`.
- **P3-17. Raw `(e as Error).message` in some toasts.**
  `OAuthConnectionDetailSheet.tsx:119-155`, `PipedreamCard.tsx:199,561` — route through
  `messageFromApiError(e)` (`apiError.ts:75`).
- **P3-18. `BindExistingCredentialDialog` query has no `staleTime`.**
  `BindExistingCredentialDialog.tsx:65-76` — refetches full `['credentials']` on every
  open; add a short `staleTime`.
- **P3-19. Local `summary` map in `OAuthConnectionDetailSheet` duplicates shared labels.**
  `:422-438` — fold `summary` into `CredentialStatusInfo` so there's one vocabulary.
- **P3-20. "Add credential" header button no-ops while API loads.**
  `ui/src/pages/ApiDetailPage.tsx:96-101` — add `disabled={!apiData}`.
- **P3-21. `new lib/events/apiImported.ts` is untested** (replaced the deleted
  `credentialImported` toast test). Add coverage.
- **P3-22. WorkspaceView credential-imported sync skips `['toolkit-card-enrichment']`.**
  `ui/src/components/workspace/WorkspaceView.tsx:387-400` — add it so toolkit piles refresh.

---

## Suggested sequencing

1. **Cache correctness** — P1-1, P1-2, P1-3, P3-22 (one PR; same bug family).
2. **Tests for the headline behavior** — P1-5 (+ P2-13).
3. **Security + async hygiene** — P1-4, P2-1, P2-2.
4. **Health-model consistency** — P2-3, P2-4, P2-5, P2-6, P2-7.
5. **a11y batch** — P3-1…P3-6.
6. **Tidy-ups** — remaining P3s, plus the P2-8 refactor that prevents future
   invalidation regressions.

Each numbered group is independently shippable as its own squash-merged PR per the
repo's git conventions.
