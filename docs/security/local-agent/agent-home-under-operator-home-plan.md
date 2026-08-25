# Plan — default the agent home under the operator's home

> **Planning doc, not yet implemented — and no implementation is in flight.** This
> lays out *what would change* and *what must be true* to move the agent's home from
> the shared parent it uses today (`/Users/Shared/<agent>` on macOS, `/opt/<agent>`
> on Linux) to a discoverable location under the operator's own home
> (`~/jentic-agents/<agent>`). It builds on
> [`filesystem-access-model.md`](filesystem-access-model.md) and slots into the
> flows in [`local-agent-isolation.md`](local-agent-isolation.md). It supersedes the
> short "Future improvement" note in that doc.

## Why revisit this now

The relocation was deferred because the interaction with the isolation paradigm was
unproven: on stock macOS the operator's home is `0700`, so an agent running as its
own user **cannot traverse into `~` at all** without a grant, and opening a path into
`~` was exactly what the boundary existed to prevent.

Three things shipped since then make it viable:

- **The confinement profile denies all of `/Users` and `/home` wholesale** and
  re-opens only the agent's own home + granted dirs (plus ancestor metadata). A home
  under `~` is *already* handled by that generic re-allow path — the profile denies
  the operator's home and re-permits just the agent-home subpath, so the agent sees
  its home and nothing else in `~`, regardless of the home's location.
- **The operator gets a recursive, inherited ACL over the agent home** at account
  creation. That mechanism is location-independent — it already grants the operator
  full read/write into an agent-owned `0700` tree.
- **`jentic run` errors closed when confinement is unavailable.** There is no
  unconfined launch in which a traverse grant on `~` could be abused during a
  session.

So the sandbox already does the hard part. What remains is the **DAC traverse** into
`~` and the lifecycle bookkeeping for it.

## The one genuinely new thing — a persistent execute ACE on `~`

For the agent uid to *reach* `~/jentic-agents/<agent>` at the kernel level, it needs
**execute (search)** on each component: `~` and `~/jentic-agents`. This is the
existing [Layer-1 traverse-walk](filesystem-access-model.md#layer-1--traverse-walk-execute-only-per-ancestor)
mechanism, reused:

```bash
sudo chmod +a "user:$AGENT allow execute" "$HOME"                 # macOS
sudo chmod +a "user:$AGENT allow execute" "$HOME/jentic-agents"
sudo setfacl -m u:"$AGENT":--x "$HOME"                            # Linux
sudo setfacl -m u:"$AGENT":--x "$HOME/jentic-agents"
```

**This is the whole security delta, and it must be stated plainly.** Today the agent
home lives outside `~`, so we add **no** ACE to `~` at all. Relocating trades that
for a **persistent execute ACE on `~`** — one that lives on the operator's home
across every session, including when no `jentic run` is active.

What that execute bit does and does not permit:

- It is **search, not read/list** — the agent still cannot `ls ~` or enumerate names
  it doesn't already know.
- It **does** let the agent reach any sibling it can *name*, and whether it can then
  read that sibling falls to the sibling's own mode — the same
  [sibling-traversal residual](filesystem-access-model.md#the-sibling-traversal-residual-closed-by-confinement)
  documented for working-dir grants, except now **permanent** rather than scoped to a
  grant's lifetime. Real secrets at `0700` are unaffected; the exposure is bounded to
  *world-readable* siblings.
- **During a `jentic run` session it is fully masked** by the confinement profile,
  which denies all of `/Users` except the agent home. The residual only matters
  **outside** a confined session — i.e. if the agent user is driven by some other
  vector while a shell it controls has that persistent search bit.

The honest framing: this is a **real, if small, widening of the standing DAC posture
on `~`**, accepted in exchange for a more discoverable, operator-owned home. The
decision to make is whether that tradeoff is worth the UX gain — it is not free, and
the plan must not pretend otherwise.

## What changes in code

| # | Change | File(s) |
|---|--------|---------|
| 1 | **Default home path.** `DefaultHomeDir` returns `~/jentic-agents/<agent>` instead of `/Users/Shared/<agent>` / `/opt/<agent>`. Keep the shared-parent form reachable (a flag or config) for operators who prefer the current posture. | `cli/internal/localagent/localagent.go` |
| 2 | **Traverse-grant on `~` at account creation.** Add the execute-only ACEs on `~` and `~/jentic-agents` to `CreateAccountCmds`, reusing the Layer-1 traverse primitive. Record the ancestor chain (`~`, `~/jentic-agents`) so reset can drop it. | `cli/internal/localagent/localagent.go` |
| 3 | **Reset teardown.** Walk and drop the home-traverse ACEs on `~`/`~/jentic-agents` — distinct from working-dir traverse grants, and only when no *other* agent still homes under `~`. `~` must never be left with a dangling agent ACE. | `cli/internal/cmd/reset` path |
| 4 | **No sandbox-profile change expected.** The agent-home re-allow + ancestor metadata already covers a home under `/Users/<operator>/…`; confirm by generating a profile for such a home and checking the metadata literals include `~` and `~/jentic-agents`. | verify only |
| 5 | **Docs.** Fold this into the filesystem-access model (Direction 2) and update the isolation doc's Step-1 recipe. | docs |

## Open questions / risks to resolve before implementing

1. **`createhomedir` under `~`.** On macOS `sysadminctl -addUser -home
   ~/jentic-agents/<agent>` + `createhomedir` still materialises a full home template
   (`Library/…`), some of it SIP/TCC-protected. Confirm this works when the parent is
   another user's `0700` home, and that the existing best-effort `chown -Rf` reset
   path still applies. The `-keepHome`/`dscl` account-deletion nuance is unchanged.
2. **The persistent-`~`-ACE tradeoff (the central decision).** Is the permanent
   execute ACE on `~` acceptable, given the residual above? Options if not: (a) keep
   the shared-parent default and offer `~/jentic-agents` only opt-in; (b) neutralise
   world-readable siblings' bits at creation (rejected elsewhere — mutates operator
   files); (c) accept it, documented, because the confinement profile masks it during
   sessions and real secrets are `0700`.
3. **Multiple agents under one `~`.** The `~` traverse ACE must be reference-counted
   across agents: creating a second agent must not re-add a duplicate, and resetting
   one must not strip the ACE another still needs. `~/jentic-agents` itself is the
   natural shared parent to own that bookkeeping.
4. **Backups / cloud sync.** `~/jentic-agents` lands inside Time Machine / iCloud /
   Dropbox scopes that the shared-parent location escaped. An agent's working tree
   and seeded provider config would now be swept into the operator's backups. Flag
   this; possibly exclude the dir.
5. **Migration for existing agents.** Do we move already-created homes, or only
   default new ones there? Moving is destructive and re-owns; defaulting-new-only is
   safer. Recommend: change the default, leave existing homes where they are.

## Recommendation (provisional)

Change the **default for new agents** to `~/jentic-agents/<agent>`, gated on the
persistent-`~`-ACE tradeoff being explicitly accepted and documented, with the
shared-parent location retained as an opt-out for operators who don't want any ACE on
`~`. Do **not** migrate existing homes. Treat the sibling residual on `~` as a
documented, session-masked, world-readable-only exposure — the same class we already
accept for working-dir grants, made standing rather than per-grant.
