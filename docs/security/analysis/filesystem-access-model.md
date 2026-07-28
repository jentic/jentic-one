# The filesystem-access model

> How the agent's Unix account and the operator's account reach — and are kept
> out of — each other's files. This is the reference for the permission mechanics;
> [`local-agent-isolation.md`](local-agent-isolation.md) is where they sit in the
> `jentic run` / `bootstrap` / `reset` flows.

The whole design rests on a dedicated, unprivileged Unix account for the agent,
distinct from the operator's login user. Two directories then matter, and the
boundary runs in **both** directions across them:

| Directory | Owner | Default posture | The other party gets in via |
| --------- | ----- | --------------- | --------------------------- |
| Operator home `~` (e.g. `/Users/alice`) | operator | `chmod 700` — closed | **per-directory named-user ACLs**, granted on demand |
| Agent home (e.g. `/Users/Shared/alice-local-agent`) | agent | agent-owned, under a **shared** parent | an **inherited operator ACL**, laid down at creation |

The asymmetry is deliberate: the operator's home is the thing worth protecting,
so it is closed by default and opened one path at a time; the agent's home is
worth *supervising*, so the operator is granted in wholesale from the start.

---

## Direction 1 — the agent reaching into the operator's home

This is the guarded direction. The operator stands in some project dir (almost
always inside `~`) and wants the agent to work there. `jentic run` tests whether
the agent can already read/write/execute the target and, if not, offers
**Allow** / **Open in agent's home** (default) / **Cancel**. An **Allow** for a
path under `~` is built from three layers.

### Layer 0 — default-deny is `chmod 700 ~`

With `~` at `drwx------`, no other account (including the agent) can even
*traverse* the home, let alone read it. We add **no** ACL to `~` itself. This is
the machine-independent guarantee everything else builds on.

### Layer 1 — traverse-walk (execute-only, per ancestor)

For each directory on the path from `~` down to the leaf's parent that the agent
can't already traverse, grant **execute-only** — search, not list, not read:

```bash
sudo chmod +a "user:$AGENT allow execute" "$ANCESTOR"      # macOS
sudo setfacl -m u:"$AGENT":--x "$ANCESTOR"                 # Linux
```

The agent can pass *through* `~/projects` to reach `~/projects/api` but cannot
enumerate `~/projects` or read its other entries. These grants are
**non-recursive** — one ACE per named ancestor.

### Layer 2 — rwx-leaf (recursive, inherited)

Full read/write/execute on the chosen workspace **and everything already inside
it, plus everything created there later**:

```bash
# macOS — inserted at index 0 (first-match ordering), applied recursively
sudo chmod -R +a# 0 "user:$AGENT allow list,add_file,add_subdirectory,search,\
delete,delete_child,read,write,execute,append,readattr,writeattr,readextattr,\
writeextattr,readsecurity,file_inherit,directory_inherit" "$DIR"
# Linux — recursive access ACL + a default ACL for future children
sudo setfacl -R -m u:"$AGENT":rwX "$DIR"
sudo setfacl -R -d -m u:"$AGENT":rwX "$DIR"
```

Two consequences worth stating plainly:

- **The leaf grant is recursive over existing contents.** Granting `~/a/b` gives
  the agent read+write on `~/a/b/c/d.txt` and every other pre-existing file in the
  subtree — not just newly-created ones. If a subtree holds something the agent
  shouldn't touch, grant a narrower leaf.
- **On macOS the permission set must be spelled out in full.** `chmod +a
  "…allow write…"` on a *directory* silently expands `write` to
  `list,add_file,search` only — the agent could create but not delete or rename
  (breaking write-to-temp-then-rename, and leaving `test -w` false so `jentic run`
  re-prompts every launch). The explicit set carries the directory-mutation bits
  (`delete`, `delete_child`, `add_subdirectory`); grant and revoke share the
  identical string so macOS can drop the ACE by exact match.

### Danger classification — dangerous grants are never the default

Before offering **Allow**, `jentic run` classifies the target. The operator's
home **root**, its sensitive dotfile dirs (`~/.ssh`, `~/.jentic`, `~/.aws`,
`~/.config`, `~/.gnupg`, `~/.gcloud`, `~/.kube`, `~/.docker`, Keychain paths,
browser profiles), any other user's home, and system trees (`/etc`, `/usr`,
`/var`, `/System`, `/Library`, `/bin`, `/sbin`, `/`) all trip a warning: the safe
options are listed first, **Allow** requires a *typed* confirmation, and `--yes`
**declines** rather than grants. Granting those would re-open the very boundary
`chmod 700 ~` exists to close.

### Accepted residual — world-readable dirs under `~`

Because Layer 1 grants *execute* on an ancestor, a directory inside `~` that is
itself **world-readable** (mode `o+r`, e.g. a `0755` project) becomes reachable
once the path to it is traversable, without an explicit leaf grant. Genuinely
sensitive material (`~/.ssh`, `~/.aws`, `~/.jentic`, …) is `0700`/`0750` and stays
closed, and the agent still cannot *list* `~` itself. We accept this rather than
re-introduce a home-wide sweep. It could be closed surgically ("narrow traverse":
grant execute on the exact ancestor chain and `chmod o-rwx` those ancestors) if
it ever matters — not currently implemented.

---

## Direction 2 — the operator reaching into the agent's home

The agent home lives under a **shared, world-traversable parent**
(`/Users/Shared/…` on macOS, `/opt/…` on Linux) — never under any human's home,
so granting the operator in never means widening a home. At account creation the
operator is granted a **recursive, inherited** ACL over the agent home:

```bash
# macOS — the same explicit permission set as the leaf grant, inherited
sudo chmod +a "user:$OPERATOR allow <full-leaf-ACE-set incl. inherit bits>" "$AGENT_HOME"
# Linux — recursive access ACL + default ACL
sudo setfacl -R -m u:"$OPERATOR":rwX "$AGENT_HOME"
sudo setfacl -R -d -m u:"$OPERATOR":rwX "$AGENT_HOME"
```

Why the operator needs it:

- **Supervision** — the operator can inspect and manage what the agent produces.
- **Bootstrap writes the agent's identity as the operator.** The platform
  identity is written by `mkdir <agent-home>/.jentic` **as the operator**, before
  ownership is handed to the agent — which is why the grant must carry
  `add_subdirectory` (the macOS `write` shorthand omits it) and the inherit bits
  (without `file_inherit`/`directory_inherit` the operator loses access to
  whatever the agent creates afterwards).

This grant is **additive and idempotent** — re-applying only ever widens — so it
is re-asserted on the account-reuse path where a prior `reset` may have left a
stale, too-narrow ACE. Ownership of the home tree itself is reclaimed to the agent
(`chown -Rf`) on every setup, best-effort (a macOS home carries SIP/TCC-protected
files nobody can chown).

---

## Lifecycle — grant, list, revoke, reset

Grants **persist across sessions** by design: a project the agent works on is not
a per-session decision. That makes lifecycle management explicit rather than
automatic.

| Action | Command | Effect on Direction-1 ACLs |
| ------ | ------- | -------------------------- |
| **Grant** | in-launch **Allow**, or `jentic run <agent> --grant <dir>` | lays down Layer-1 traverse on new ancestors + Layer-2 rwx-leaf on the target |
| **List** | `jentic run <agent> --list-grants` | none — shows recorded grants (danger-flagged) so access can't quietly sprawl |
| **Revoke** | `jentic run <agent> --revoke <dir>` | drops the **leaf** allow only; **ancestor traverse grants stay** (kept for the next grant). With the leaf gone the agent can't read/write the dir — and, unless world-readable, can't reach its contents |
| **Reset (named)** | `jentic reset <agent>` | full teardown of that agent: walks the ancestor chain and drops the traverse grants **too**, settles the home, removes the account + its config link |
| **Reset (full)** | `jentic reset` | every configured agent, then the operator's own `~/.jentic` |

The key revoke-vs-reset distinction: **`--revoke` intentionally leaves ancestor
traverse ACLs in place** (so re-granting a sibling later is cheap), whereas a
**full reset walks the ancestor chain and removes them**, returning the path to
its pristine 700-closed state.

Recorded grants live in the operator's `~/.jentic/config.yaml` under the agent's
entry; `reset`'s survey re-scans the on-disk ACLs live and flags any **drift**
between what's recorded and what's actually present.

---

## What this model does *not* do

- It never reverts `chmod 700 ~` — teardown removes agent ACLs but leaves the
  home locked.
- It never touches the operator's *own* access to their files — every ACL is a
  **named-user** entry scoped to the single agent account.
- It does not share anything under the operator's 700 home by symlink or PATH —
  those resolve with the agent's credentials and dangle at the home boundary (see
  the CLI-tool-sharing note in [`local-agent-isolation.md`](local-agent-isolation.md)).
