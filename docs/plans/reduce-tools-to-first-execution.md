> Base: `feat/provisioning-plan-access-request` (PR #757). This work layers on top.
> Issues touched: #684 (fewer steps to first execution), and the AX of the provisioning-plan flow.

# Reduce the tool count to first execution

The provisioning-plan flow works end to end, but a dogfooding run took **9 CLI
commands** to reach the first successful `execute`. Two of those were pure waste
and one (`access refresh`) was unnecessary. This plan removes them, grounded in
the transcript and verified against the code.

## The 9-command baseline (from the dogfooding transcript)

```
1  profile list && whoami        orient
2  search "get values…"          → {"data":[]}   ← WASTE: searched before importing
3  catalog search "google sheets" discover
4  catalog import …/sheets        import
5  search "…" --json              found op_id
6  execute <op>                   → 403, prints directive   ← WASTE: executed only to be denied
7  inspect <op>                   read contract
8  access request --provision … --wait   the plan (operator fulfils via wizard)
9  access refresh && execute      → 200 + data   ← refresh was UNNECESSARY (binding is live)
```

## Root-cause findings (code-verified)

- **`refresh` (cmd 9) is usually unnecessary.** Toolkit bindings are resolved
  *live* by the broker from `agent_toolkit_bindings`
  (`broker/repos/toolkit_binding_resolver.py:19`), not carried in the token. And
  for long-lived agent tokens (`is_ephemeral=False`) even *scopes* resolve live
  from `ActorScopeGrant` at validation (`auth/services/token_service.py:298-309`).
  A `--provision` plan grants **no new scope** (only a binding), so a refresh
  after it is a no-op.
- **`execute`-to-get-denied (cmd 6)** exists only to fetch the directive telling
  the agent to run `--provision`. But `whoami` at cmd 1 already showed empty
  `toolkit_bindings` — the agent had the signal to provision proactively.
- **pre-import `search` (cmd 2)** is the agent's own deviation; the skill's
  discovery order is already `catalog search → import → search`. Weakest lever.
- **`whoami` lists toolkit *ids*, not the APIs they serve** — so an agent can't
  tell "do I already have access to API X?" without a probe. Enriching this
  removes guessing (the strongest structural lever).

## The changes (all five)

### 1. Skill — narrow the `refresh` guidance (removes cmd 9's refresh)
`cli/internal/skillgen/content/jentic.md`. Today the skill tells the agent to
`refresh` after approval. Reword to: **only** refresh after an approved
`scope:grant` **and** only if `whoami` flags the scope as stale on your token
(the CLI already computes this via `StaleScopes`). A `--provision` plan grants a
binding, which is live — no refresh. Update step 2, step 5, and the quick-ref.

### 2. Skill — provision proactively (removes cmd 6)
Invert the reactive framing. New primary instruction: after `inspect`, if
`whoami` shows **no binding that serves this API**, file the `--provision` plan
**before** the first execute — do not execute-to-get-denied. Keep the directive
recovery as the fallback for the non-obvious cases.

### 3. Backend — `whoami` / `GET /me` returns the APIs each binding serves (#3)
So the agent can decide provision-vs-execute without a probe.
- Schema: add `serves: list[ApiRef]` to `ToolkitBindingEntry`
  (`src/jentic_one/auth/web/schemas/identity.py:11-15`); new `ApiRef`
  (`api_vendor`/`api_name`/`api_version`).
- Data: keep listing `toolkit_id`s from the admin DB
  (`AgentToolkitBindingRepository.list_for_agent`), then resolve each toolkit's
  served APIs in a **control**-DB session via a new repo method
  (`ToolkitCredentialBinding` → `credential.api_*`). No cross-DB raw SQL needed
  (the join is entirely within control); the only admin→control hop is the
  toolkit-id list the service already fetches.
- Thread through `AgentService.list_toolkits` / `ToolkitBindingView`
  (`auth/services/schemas/agents.py:38-46`) and populate at
  `auth/web/routers/identity.py:100-102`.
- Regenerate: `make openapi` + `cd ui && npm run codegen` (drift arch-tests
  enforce this). Update `tests/web/auth/test_me.py`.

### 4. Skill — don't search an empty registry (best-effort, marginal; removes cmd 2)
Add: "if the user already named the API/vendor, go straight to
`catalog search`/`import`; don't `search` an empty registry first."

### 5. CLI — `--provision --wait` auto-refreshes ONLY when a scope was granted (#5)
`cli/internal/cmd/access.go` `accessRequestE`. After `pollAccessRequest` returns
an approved/partially-approved request, re-mint the token **conditionally**:
only if an approved item is a `scope:grant` (or `whoami`/`StaleScopes` reports
stale scopes). A pure `--provision` plan grants no scope → no re-mint (matches
finding above; unconditional refresh would be a wasted call). Reuse
`sess.MintFresh(ctx)` (the same force-mint `access refresh` uses); switch
`accessRequestE` to obtain the `*agentauth.Session` (via `agentSessionOpen`) and
apply the same API-key guard. Update `access_test.go`
(`TestAccessRequestWaitPollsUntilTerminal` will need the mint endpoint mocked
only when a scope grant is present — for the binding-only case no mint fires).

## Net effect

Ideal path drops from **9 → ~5**: `catalog import → search → inspect →
provision --wait → execute` (with `whoami`/`profile` folding into orientation and
no refresh). Every remaining command is load-bearing.

## Risk / ordering

- Changes 1, 2, 4 are **skill-only, zero backend risk** — do first.
- Change 3 (whoami) is the one requiring `make openapi` + UI codegen + a schema
  test update; additive field, low risk.
- Change 5 (CLI) is conditional-refresh; the key correctness point is that it
  must **not** fire for a binding-only plan.

## Test plan

- Backend: unit + arch (incl. openapi conformance) + SQLite integration; add a
  `/me`-serves assertion.
- CLI: `go test ./...`; update the wait test; add a "no-refresh on binding-only
  plan" assertion and a "refresh on scope-grant" assertion.
- UI: build + lint + browser suite after codegen.
- E2E-ish: the existing `test_provisioning_plan_e2e.py` still green; optionally
  assert `/me` now reports the served API after a fulfilled plan.
