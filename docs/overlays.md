# Overlays and the update loop

An **overlay** is an operator/agent-contributed patch to a registered API's OpenAPI
spec — a small JSONPath-based document that fixes a broken or incomplete upstream
spec without forking it. This page documents the **stacking contract** and how
overlays interact with the Flow-3 catalog-update loop, so the behaviour is
predictable rather than surprising. For the endpoint list and scopes, see
[`reference/endpoints.md`](reference/endpoints.md); for the agent-facing authoring
workflow, see the [`contribute-spec-fix`](../skills/contribute-spec-fix/SKILL.md) skill.

## Lifecycle

An overlay moves through three states:

- **pending** — submitted, not yet applied to the served spec. Submitting requires
  only `apis:write` — the *ordinary* contributor/agent scope, deliberately unelevated:
  anyone who can edit APIs can *propose* a fix. The privilege step is at confirm, not
  submit (that is the whole point of the pending→confirmed gate below).
- **confirmed** — materialized: the overlay was applied over a base revision, the
  result was ingested as a new revision, and that revision was promoted to the API's
  current (served) revision. Confirming requires `overlays:confirm` (an operator
  scope carved out of `org:admin`, never an agent default) — the elevation that submit
  intentionally lacks.
- **deprecated** — retired: either rolled back by an operator, or auto-deprecated by
  an authorized catalog re-import that adopted a fresh upstream spec (see below).

## The stacking contract

Overlays **stack cumulatively with last-confirmed-wins semantics**. This is a
deliberate, load-bearing contract:

- **Cumulative.** Confirming overlay B while overlay A is already confirmed applies B
  on top of A's output — the served spec reflects both. Overlays are not independent
  patches layered by priority; each confirm materializes over the *current* served
  revision, which already embodies previously-confirmed overlays.
- **Last-confirmed-wins.** The API's current revision is whatever the most recent
  confirm produced. There is no per-overlay slot or ordering key beyond confirm
  order.
- **No per-overlay unwind.** There is **no** mechanism to surgically remove overlay A
  from the middle of a stack while keeping B. The only reversal primitive is
  **rollback**, which restores the single revision an overlay superseded when it was
  confirmed (recorded as `superseded_revision_id`). Rolling back the most-recent
  overlay is deterministic; unwinding an *earlier* overlay from under later ones is
  explicitly out of scope — re-author instead.

### Why not per-target materialization?

The `target_revision_id` field on an overlay records the base revision it was
**authored against**. It is advisory: load-bearing only at confirm time (it selects
which base to materialize over) and as a drift signal at submit. It is **not** a key
for pinning an overlay to an arbitrary historical revision, and we do not build
per-target materialization until there is a concrete need. Treating
`target_revision_id` as more than advisory is the trap this contract forecloses.

## The update loop (Flow-3)

A background sweep conditionally re-fetches each registered API's upstream spec and,
on a real change, emits an actionable event:

- **`catalog.update_available`** — the upstream spec changed and the served revision
  has no overlay in the way. Resolve by re-importing (one click in the UI /
  `jentic catalog import` in the CLI), which adopts the upstream and settles the
  notification.
- **`catalog.update_conflicts_overlay`** — the upstream changed *and* the served
  revision is a confirmed overlay whose recorded base digest no longer matches
  upstream. Adopting the upstream would supersede the operator's fix, so this is an
  operator decision. The event carries a `conflict` block
  (`{base_digest, served_digest, upstream_digest}`) so the surface can explain *what*
  diverged (the base the overlay was built on moved) rather than just "your fix was
  removed."

### Adopting an upstream change over a live overlay

Re-importing an upstream spec over an API whose current revision is a live confirmed
overlay is doubly gated: the caller needs both `catalog:import` **and**
`overlays:confirm`. An authorized re-import:

1. archives the overlay's materialized revision and adopts the fresh upstream as the
   served spec, then
2. **auto-deprecates** the superseded overlay and emits an attributed
   `overlay.deprecated` event (recording the authorizing operator and the overlay's
   author) so the change is visible where humans work, not just in the audit log.

An unauthorized caller is **refused** (HTTP 403 `overlay_supersede_forbidden`) and a
fresh `catalog.update_conflicts_overlay` event is re-emitted for a privileged
operator — the fix is never silently discarded.

### Snoozing a known change

An operator who has seen an upstream change and chosen not to adopt it can **snooze**
the notification (`POST /catalog/{api_id}:snooze`, requires `events:write`). Snoozing
pins the accepted upstream digest; a *newer* upstream digest automatically re-lights
the badge, so a real new change is never hidden. `jentic catalog outdated
--include-snoozed` lists muted entries.

## Invariants (summary)

- The served spec always equals the API's current revision.
- A confirm never silently discards a prior overlay's effect — it materializes over
  it (cumulative).
- Adopting an upstream change over a live overlay is operator-gated and attributed;
  it never happens silently.
- Rollback restores exactly the revision an overlay superseded; it does not unwind a
  stack.
