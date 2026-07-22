> Issues amended: jentic/jentic-one#619, #662, #683, #684, #614, #629, #606, #613, #615, #621, #603, #656 (prerequisite); improves #609, #670; fast-follow #641 (eases #638)

# Provisioning-Plan Access Request

A single access request that carries a full **provisioning plan** — create
toolkit, provision credential, bind credential to toolkit (with agent-proposed
rules), and bind the agent to the toolkit — which a human fulfils through a
guided wizard that reuses the existing creation dialogs and endpoints.

This turns the access-request endpoint from "approve a single last-mile binding"
into "approve the whole path to first execution", without moving credential
secrets through the agent and without turning the effect applicator into a
provisioning engine.

## Locked decisions

- **Secret is operator-entered at approval.** The agent's request never carries a
  credential secret. The agent declares *"a credential of type X for API Y is
  needed here"*; the operator fills the secret in the existing credential dialog
  during approval. (Preserves the "agent never holds keys" model — #623, #625.)
- **Orchestration lives in the fulfilment wizard, not the effect applicator.**
  Creation reuses the existing `POST /toolkits` / `POST /credentials` endpoints
  and dialogs; the access request stays a coordination/approval artifact.
- **Orphans are acceptable for now.** Partial fulfilment (toolkit created, request
  abandoned) leaves real objects; the client handles discard/resume. No backend
  all-or-nothing transaction runner.
- **The agent proposes the rules** as a first pass, read from the API spec (#621).
- **Reusable in the frontend.** The wizard is built against a *plan model*, so an
  operator can launch the same flow by clicking, with no agent-filed request.
- **`amend` authorization is widened to the approver/owner** for v1 (fallback: a
  dedicated `:fulfil` verb only if widening `amend` proves unsafe).
- **No-auth support is full (#603):** a real `NO_AUTH` provisioning path so the
  `credential:provision` step is genuinely skippable (e.g. open-meteo).
- **#656 (vendor normalization) is a blocking prerequisite; #641 (region) is a
  fast-follow.**
- **Rollout is additive.** The existing single-item requests and the manual
  5-step flow remain. The broker directive and skill *prefer* steering to the
  plan when nothing serves the API, but nothing existing is removed.

## Background: how the flow works today

Making one imported API executable by an agent requires **five artifacts**, none
auto-linked, four of which are outside the access-request endpoint and therefore
outside an agent's reach: create a toolkit, create a credential, bind the
credential to the toolkit, attach permission rules to that binding, and bind the
toolkit to the agent (#684).

An access request today is an envelope of independent line items. The valid
`(resource_type, action)` combinations are enforced in two places:

- Web boundary: `src/jentic_one/control/web/schemas/access_requests.py:64`
  (`valid = {("credential", "bind"), ("toolkit", "bind"), ("scope", "grant")}`).
- Effect dispatch: `src/jentic_one/control/services/access_requests/effects.py:59-63`
  (`_EFFECT_PHASES`).

Each approved item maps to exactly one enforced effect
(`EffectApplicator.apply`, `effects.py:87-128`):

- `credential:bind` → `_apply_credential_bind` (`effects.py:200-238`): inserts a
  `toolkit_credential_bindings` row and — only here — writes enforceable
  `toolkit_permission_rules` via `ToolkitPermissionRepository.replace_user_rules`.
- `toolkit:bind` → `_apply_toolkit_bind` (`effects.py:268-317`): inserts an
  `agent_toolkit_bindings` row (admin DB).
- `scope:grant` → `_apply_scope_grant` (`effects.py:376-409`): inserts an
  `actor_scope_grants` row (admin DB).

`decide()` (`src/jentic_one/control/services/access_requests/service.py:308-457`)
applies control-DB effects (credential bind) inside the decision transaction and
admin-DB effects afterward, idempotently (un-acked marker = `applied_effects IS
NULL`). It is dependency-blind: items are applied independently with no ordering
between "create the toolkit" and "bind to that toolkit".

The problem this plan solves: the broker's `no_toolkit_binding` directive
steers the agent to file a `toolkit:bind` request first
(`src/jentic_one/broker/core/exceptions.py:236-256`), but approving that binding
fails because no credential-bearing toolkit serves the API yet — the denial
reason "No toolkit serves API ...; provision and bind a credential for it first"
is raised by `ToolkitReferenceUnresolvedError`
(`src/jentic_one/control/services/access_requests/errors.py:109-122`) and
converted to a deny-with-reason at decide time
(`service.py:74-79`, `service.py:384-390`). The directive and the denial
contradict each other (#662, #683, #684).

## Core concept

Extend the access request into a **provisioning plan**: a request that carries
two new **fulfilment-intent** item types — `toolkit:create` and
`credential:provision` — which the backend applicator **does not** execute.
A **guided fulfilment wizard** (UI, and the operator side of the CLI flow) walks
the plan, calls the existing creation endpoints, and `amend`s the freshly-minted
IDs onto the downstream bind items. A single `decide(approve-all)` then lets the
**existing, unchanged** `credential:bind` and `toolkit:bind` effects do the real
wiring.

```text
AGENT files plan (no secrets, no real IDs for new things):
  [1] toolkit:create       (resource_reference: {serves_api})              intent, not applied by backend
  [2] credential:provision (resource_reference: {api, security_scheme})    intent, not applied by backend (skipped when no-auth)
  [3] credential:bind      (rules = agent's proposed first-pass rules)     real effect; target filled at approval
  [4] toolkit:bind         (resource_reference: {vendor,name[,version]})   real effect (agent -> toolkit)

OPERATOR wizard (reuses existing dialogs), per pending request:
  Step 1  create toolkit    -> POST /toolkits             -> tk_...   -> amend item[3].to_id
  Step 2  create credential -> POST /credentials (secret) -> cred_... -> amend item[3].resource_id   (skipped if no-auth)
  Step 3  bind cred->tk      (encoded in item[3]; display only once amended)
  Step 4  confirm rules     -> PermissionRuleEditor prefilled from item[3].rules -> amend item[3].rules
  Step 5  bind agent->tk     (encoded in item[4])
  Finish  -> POST :decide (approve all) -> existing effects wire cred->tk(+rules) and agent->tk
```

Why this shape: the two new item types are **inert** on the backend (never in
`_EFFECT_PHASES`), so **no new provisioning logic touches the two-phase
applicator** — the riskiest area is untouched. All real mutation still flows
through the four already-audited paths (`POST /toolkits`, `POST /credentials`,
`credential:bind`, `toolkit:bind`).

## Phase 0 (blocking prerequisite): #656 vendor normalization

**Problem.** The registry normalizes vendor/name on import via `_slugify`
(`src/jentic_one/registry/ingest/api_identifier.py:13-16`, applied at
`api_identifier.py:52-53`): `httpbin.org` -> `httpbin-org`. But `POST /credentials`
stores the vendor **verbatim** (`src/jentic_one/control/services/credentials/service.py:106`;
no validator on `APIReference`). The broker then matches credential vendor
against the discovered (normalized) vendor by **raw SQL equality** with no
normalization on either side (`src/jentic_one/broker/repos/rule_evaluator.py:29-38`
and `src/jentic_one/broker/repos/toolkit_binding_resolver.py:34-41`). A dot-vs-dash
mismatch yields zero rows -> the toolkit "doesn't serve" the API -> silent
default-deny 403.

**Why blocking.** Without it the wizard finishes "green" and the first `execute`
still 403s for any dotted-vendor API — reintroducing the exact hollow-yes this
plan removes.

**Change.**
- Hoist `_slugify` into a shared helper (e.g. `src/jentic_one/shared/`) so
  registry and control share one implementation and cannot drift.
- Normalize `api.vendor` and `api.name` in
  `CredentialService.create` before persisting
  (`control/services/credentials/service.py:106-108`).
- Tests: create-with-dotted-vendor -> stored slugified -> broker join matches ->
  `execute` reaches the rule gate rather than silent default-deny.

## Phase 1 (backend): intents, amend write-back, no-auth, dedup

1. **New inert item types** `("toolkit","create")` and `("credential","provision")`:
   - Widen the `Literal` types and the `valid` set in
     `control/web/schemas/access_requests.py:46-47,64`.
   - `effects.py`: keep them **out of** `_EFFECT_PHASES` (`effects.py:59-63`), but
     add explicit fulfilment-only handling so an approved-but-unfulfilled intent
     records a clear skipped/`decision_reason` instead of a silent no-op — the
     guardrail against a new "hollow yes" (#619).
   - `effects.py:validate()` (`effects.py:130-169`): decide-time consistency — a
     `credential:bind` must by then carry a real `resource_id` + `to_id`, else
     deny-with-reason via the existing `_UNFULFILLABLE_BIND_TARGET` path
     (`service.py:74-79`, `service.py:384-390`) so `--wait` closes cleanly.

2. **`amend` write-back of resolved targets:**
   - `AccessRequestRepository.amend_item`
     (`control/repos/access_request_repo.py:237-258`) today updates only `rules`
     and `resource_id`; add `to_id` (and `to_type` / `resource_reference` as
     needed) so the wizard can write the new toolkit id onto the
     `credential:bind` item's bind target.
   - Thread `to_id` through `AccessRequestService.amend`
     (`service.py:557-625`) and `AmendItemSchema`
     (`control/web/schemas/access_requests.py:94-99`).
   - Widen `amend` authorization so the approver/owner (not only the filer) can
     amend a pending request they own. **Finding during build:** no widening is
     needed — `amend` scopes visibility via `build_access_filters(identity,
     AccessRequest)` (`service.py:565`), and the `AccessRequest` filter already
     matches on `filer_owner_id` (`control/scoping/filters.py:98-115`). When an
     agent files the plan, `filer_owner_id` is set to the agent's owner
     (`service.py:173`), so the owning operator already passes the filter. The
     confused-deputy guards to preserve are the file/amend-time
     `_reject_unsupported_rules` and `_validate_grantable_scope`
     (`service.py:582-600`). (The `owns_filer` / `agents:write` reviewer gate
     lives on `decide` via `_compute_evaluation`, `service.py:766-799`, and
     continues to gate the final approval.)

3. **Full NO_AUTH provisioning path (#603):**
   - `StoredCredentialType.NO_AUTH` exists
     (`src/jentic_one/shared/models/credentials.py:16`) but has no create path and
     `to_wire` refuses it (`src/jentic_one/control/services/credentials/mapping.py:40-41`).
   - Add a `NO_AUTH` branch to credential create
     (`control/services/credentials/service.py`) and to the wire mapping, and a
     broker injector no-op for no-auth (broker already short-circuits an empty
     vendor in `broker/services/credentials/orchestrator.py`, but a stored
     `NO_AUTH` credential reaching `to_wire` must not raise).

4. **Plan dedup (#615):** give the intent items a stable `resource_reference`
   (the `serves_api` vendor/name/version triple) so the reference-normalization
   dedup path in `find_pending_duplicate`
   (`control/repos/access_request_repo.py:219-232`) catches a re-filed identical
   plan and reuses the pending request via `DuplicatePendingError`
   (`service.py:187-194`) instead of spawning duplicates.

### Prerequisite guard note (no change, add a test)

`_check_prerequisite` (`service.py:116-124`) only fires for a `credential` item
**when `to_id is not None`** (`service.py:165-167`). In the plan, item [3]
(`credential:bind`) is filed with a blank `to_id` (resolved later by the wizard),
so the guard is naturally sidestepped at file time and re-asserted at decide time
via `validate()`. Add a test locking this so a future change does not reintroduce
the "credential requires a pre-existing binding" dead-end (#684 root).

## Phase 2 (CLI): plan builder, directive, skill

The wire types already support multi-item plans — `FileRequest.Items []Item` and
the `Item` struct with `ResourceReference`, `ToID`, `Rules`
(`cli/internal/accessclient/client.go:73-86`). Only the builder emits one item
today.

1. **Plan builder** — `accessRequestOptions.item()`
   (`cli/internal/cmd/access.go:187-214`) currently returns a single item. Add a
   plan mode (e.g. `jentic access request --provision <vendor/name>`) that builds
   the 4-item plan; keep the existing single-item `--toolkit` / `--toolkit-id` /
   `--scope` modes for back-compat. `--wait` polling is unchanged
   (`pollAccessRequest`, `access.go:433-461`).

2. **Agent proposes rules (#621):** before filing, the agent reads the spec (it
   holds `apis:read`) and emits a first-pass `allow/deny + methods/path` rules
   array onto the `credential:bind` item. The CLI carries whatever rules the
   agent supplies verbatim.

3. **Broker directive rewrite (#662, #683, #684):** in
   `no_toolkit_binding_directive`
   (`broker/core/exceptions.py:236-256`), when nothing serves the API, change the
   `suggested_command` from the auto-denying `jentic access request --toolkit ...`
   to the new provisioning-plan command. The broker already knows it is the
   no-candidate case (`broker/web/routers/execute.py:322-330`).

4. **Skill** — rewrite section 2 and the pitfalls of
   `cli/internal/skillgen/content/jentic.md`. Today it teaches the last-mile
   `--toolkit ... --wait` and says "you cannot fix this yourself" when no toolkit
   exists (`jentic.md:98-107`). Replace with: file a **provisioning plan** with
   blanks for the operator — a worked 4-item example, the instruction to propose
   rules from the spec (translate the user's plain-English intent into
   `allow/deny + methods/path`, mark them a first-pass the human can edit), and
   the reinforcement that the agent never holds keys and never self-approves.

## Phase 3 (UI): the fulfilment wizard

A new `ProvisioningRequestDialog` composes existing components against a plan
model:

- Step 1 — create toolkit (small form or auto-name from `serves_api`) ->
  `POST /toolkits`.
- Step 2 — embed `CreateCredentialDialog`
  (`ui/src/modules/credentials/components/CreateCredentialDialog.tsx`), pre-seeded
  with the API ref + detected auth type from the `credential:provision` item;
  operator enters the secret -> `POST /credentials`. Skipped when the plan marks
  the API no-auth.
- Step 3 — bind cred -> toolkit: display only; the wizard `amend`s
  `to_id` + `resource_id` onto the `credential:bind` item.
- Step 4 — confirm rules: embed `PermissionRuleEditor`
  (`ui/src/modules/toolkits/components/PermissionRuleEditor.tsx`) prefilled with
  the agent's proposed rules -> `amend` rules.
- Step 5 — bind agent -> toolkit: display the `toolkit:bind` item.
- Finish -> `POST /access-requests/{id}:decide` approve-all -> existing effects
  wire everything.

This reuses the exact dialogs requested and slots into the existing
`AccessRequestDialog` review/confirm scaffolding
(`ui/src/shared/app/rail/AccessRequestDialog.tsx`).

## Phase 4 (UI): reusable operator entry + orphan control

- **Operator-initiated flow:** an "Add API to an agent" entry point synthesizes
  the same plan client-side and runs the identical wizard — no agent-filed
  request required. Build the wizard against the plan model, not against "an
  agent request", so both callers share it.
- **Orphan control (client-side, per decision):** the wizard tracks created
  `tk_...` / `cred_...` within its session; on abandon it offers discard (existing
  `DELETE /toolkits/{id}` / `DELETE /credentials/{id}`) or resume (leave the
  request pending with whatever was amended).

## Fast-follow: #641 region persistence

Fix the form -> record -> broker chain for server variables so region-split APIs
(PostHog, Mixpanel) route to the selected regional host instead of the spec
default. Ship right after the wizard. This also eases #638 (region mismatch
surfaced as a misleading "invalid key").

## Issues amended (traceability)

**Resolved / substantially amended**

- #619 — hollow "approve access to an API": approval now provisions.
- #662, #683 — directive points at bind before credential: directive files a
  provisioning plan instead.
- #684 — five manual silently-failing steps: one plan, one guided approval.
- #614 — approval unclear/multi-step-with-failure: wizard names each concrete step.
- #629 — unclear what the request is for / toolkit unnamed: plan is explicit.
- #606 — didn't know rules needed setting: rules are Step 4, pre-filled.
- #613 — "No rules — all ops blocked": default proposed rules + guided path.
- #615 — agent files per-API requests then revokes: single plan + dedup.
- #621 — agent proposes rules from spec: the load-bearing primitive here.
- #603 — no-auth APIs still ask for credentials: full NO_AUTH path.
- #656 — vendor mismatch silent default-deny: fixed as blocking prerequisite.

**Improved / touched**

- #609 — API<->credential navigation friction: creation happens inline in the wizard.
- #670 — decided request still shows live View/Deny: addressed if Phase 4 aligns
  the rail with request state (optional scope).

**Fast-follow**

- #641 — region not persisted (eases #638).

**Inherited from reused endpoints (NOT fixed by this plan)**

Because the wizard reuses `CreateCredentialDialog` / `POST /credentials` / the
broker join, these ride along until separately fixed:

- #589 — editable "Field name" corrupts binding; PATCH drops `details`.
- #655 — `Toolkit.permissions` blob ignored by the broker.
- #630, #605, #633 — no save-time validation / status / secret masking.
- #690 — `api_version varchar(50)` overflow.

## Phasing summary

1. Phase 0 — #656 vendor normalization (blocking).
2. Phase 1 — backend intents, amend write-back, full NO_AUTH, plan dedup.
3. Phase 2 — CLI plan builder, broker directive rewrite, skill.
4. Phase 3 — UI fulfilment wizard (reuses existing dialogs).
5. Phase 4 — reusable operator entry + orphan control.
6. Fast-follow — #641 region persistence.

Build Phase 0 + Phase 1 behind the additive path first, to de-risk the
schema/applicator edges before any UI work.

## Open risks

- **amend authorization scope.** If widening `amend` to the approver/owner leaks
  anything, fall back to a dedicated `POST /access-requests/{id}:fulfil` verb.
- **Partial fulfilment orphans.** Accepted for v1; client-side discard/resume.
  No backend rollback of a created toolkit when a later step is abandoned.
- **NO_AUTH breadth.** The full path touches credential create, the wire mapping,
  and the broker injector — scoped in Phase 1, not a free toggle.
- **Reused-endpoint bugs.** The inherited-issues list above can make a
  "green" plan still fail at first execute for affected APIs; #656 is fixed here,
  #641 is fast-follow, the rest are tracked separately.
