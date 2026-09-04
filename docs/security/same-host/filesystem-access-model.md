# The filesystem-access model

> How the agent's Unix account and the operator's account reach — and are kept
> out of — each other's files. This is the reference for the permission mechanics;
> [`local-agent-isolation.md`](local-agent-isolation.md) is where they sit in the
> `jentic run` / `setup` / `reset` flows.

The whole design rests on a dedicated, unprivileged Unix account for the agent,
distinct from the operator's login user. Two directories then matter, and the
boundary runs in **both** directions across them:

| Directory | Owner | Default posture | The other party gets in via |
| --------- | ----- | --------------- | --------------------------- |
| Operator home `~` (e.g. `/Users/alice`) | operator | per-session **process confinement** (sandbox-exec/bwrap) hides all but granted paths | **per-directory named-user ACLs**, granted on demand |
| Agent home (e.g. `/Users/Shared/alice-local-agent`) | agent | agent-owned, under a **shared** parent | an **inherited operator ACL**, laid down at creation |

> **Note.** In-home confidentiality against the agent is enforced per session by
> the [process-confinement layer](sandbox-confinement-design.md): `jentic run`
> launches under sandbox-exec/bwrap and **errors closed** if confinement is
> unavailable. The model never changes the operator home's own mode; real secrets
> keep their own `0700` modes regardless.

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

### Layer 0 — the confinement profile (targeted human-home deny)

The agent process is launched under a per-session confinement profile
(sandbox-exec on macOS, bwrap on Linux) that **denies every human-home root
(`/Users`, `/home`) except the paths this session legitimately needs** — see
[`sandbox-confinement-design.md`](sandbox-confinement-design.md). The re-allow list is the agent's
own home, the granted directories, and metadata-traversal on their ancestors;
everything else **under `/Users` and `/home`** is invisible to the agent regardless
of its mode. We add **no** ACL to `~` itself and never change its mode. Because
in-home confidentiality rests on the profile being applied, `jentic run`
**errors closed** when confinement is unavailable rather than launching an exposed
session. This is the layer that makes the per-entry non-negotiable (grant `~/a`,
hide `~/b`) hold — the ACLs below only ever *open* access; they never trim it.

> **This is a targeted home-deny, not a strict `(deny default)` jail.** The base
> profile stays permissive — `(allow default)` on macOS, a full `--dev-bind / /`
> on Linux — so the agent's own runtime dependencies (its binary, dylibs, `/tmp`,
> `/dev`, loopback socket, the shared toolchain) can't fail to start on an OS or
> agent update. Only the human-home roots are then carved out and selectively
> re-opened. Consequently paths **outside** `/Users` and `/home` — outbound
> network, process execution, `/tmp`, `/etc`, `/Library`, `/opt` — remain
> reachable; this layer is a **filesystem credential boundary around human homes**,
> not a network/exec jail. Confidentiality of anything a would-be exfiltrator could
> reach outside a human home (e.g. the network to send it over) is out of this
> layer's scope; front the provider with an LLM proxy to keep provider keys out of
> the agent account entirely.

The same profile also marks the **executable/CLI routes** on the agent's PATH
(`/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`, `/usr/local/bin`, and the sanctioned
tool dirs like `/opt/homebrew/bin`) **read-only** — see
[Non-negotiable boundaries](#non-negotiable-boundaries) below.

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

### Danger classification — two ban classes, and banned paths cannot be granted

Before offering **Allow**, `jentic run` classifies the target into one of three
outcomes (`localagent.Classify`). A banned path offers **no "grant
anyway" option at all** — the interactive prompt lists only *Open in the agent's
home* and *Cancel*, `--allow-dir` hard-refuses, and there is no typed-confirmation
escape hatch. The two ban classes differ only in *which* paths they cover:

| Class | Meaning | Examples | A **subdirectory** below it? |
| ----- | ------- | -------- | ---------------------------- |
| **Soft ban** | The directory holds secrets *directly*, so it can't be granted as-is — but a subdirectory beneath it is ordinary and grantable. | the operator's own home root; any other user's home (`/Users/<name>`, `/home/<name>`) | **grantable** |
| **Hard-subtree ban** | Nothing anywhere in the subtree may be granted — the path *and every descendant* is off-limits. | `~/.ssh`, `~/.jentic`, `~/.aws`, `~/.config`, `~/.gnupg`, `~/.gcloud`, `~/.kube`, `~/.docker`, Keychain paths, browser profiles; system trees (`/etc`, `/usr`, `/var`, `/System`, `/Library`, `/bin`, `/sbin`, `/`) | **also banned** |

So `~` itself is a *soft* ban (you can still grant `~/projects/api`), while
`~/.ssh` is a *hard* ban (neither `~/.ssh` nor `~/.ssh/keys/prod` can ever be
granted). Everything else is an ordinary path: `jentic run` offers **Allow** /
**Open in agent's home** (default) / **Cancel**, and `--yes` takes the safe
default (open in home) rather than granting.

**Bulk grants obey the same classification.** Account setup can offer to grant, in
one step, the workspaces the operator has already trusted in the agent's own config
(Claude Code's `~/.claude.json` `projects` entries with
`hasTrustDialogAccepted: true`) — see [local-agent-isolation.md](local-agent-isolation.md#optional-bringing-your-workspaces-over).
That offer uses `Classify` as its filter: any banned candidate (a hard-banned
subtree, or a soft-banned home root) is dropped before it is ever shown, and each
selection is re-classified at grant time as a belt-and-braces guard. The ban rules
cancel out anything the bulk offer would otherwise propose — the same precedence
the single-path grant flow enforces.

### Non-negotiable boundaries

Three boundaries are enforced by default and cannot be relaxed through the normal
grant flow:

1. **Human homes are denied wholesale.** The confinement profile denies *all* of
   `/Users` and `/home`, not just the operator's home, and re-opens only the
   agent's own home + granted paths. Granting the agent access to one user's
   directory can never expose another user's files.
2. **Hard-banned subtrees can never be granted.** The sensitive dotfile dirs and
   system trees above are refused at the prompt with no override — see the ban-class
   table.
3. **The binaries `jentic run` executes stay read-only.** The profile marks every
   executable route on the agent's PATH (`/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`,
   `/usr/local/bin`, and the sanctioned tool dirs such as `/opt/homebrew/bin`)
   **read-only** (SBPL `(deny file-write* (subpath …))` on macOS, `--ro-bind` on
   Linux). Read and execute are preserved; only writes are denied. This closes a
   **self-escape** path: without it, a compromised or prompt-injected agent could
   overwrite the very `jentic`/agent binary the operator runs, and thereby strip
   its own sandbox on the *next* `jentic run`. Because it protects the launcher
   itself, it is a default that the grant flow never opens.

### The sibling-traversal leak — why ACLs alone cannot close it

The confinement layer exists because the ACL layer has a structural gap.

**Symptom.** Grant `~/a` and the agent could, without confinement, also reach
`~/b`, a sibling it was never granted, whenever `~/b` happens to be
world-readable.

**Root cause — directory execute is not per-entry.** To reach `~/a`, the kernel
checks *execute (search)* on every path component: `/` → `/Users` → `~` → `~/a`.
So the grant **must** add `user:agent allow execute` to `~` itself. But the search
bit on a directory is a single bit authorizing traversal to *any* child by name —
there is no Unix/POSIX-ACL primitive for "may traverse into `a` but not `b`". Once
the agent can search `~`:

- it still **cannot `ls ~`** — enumerating siblings needs *read* on `~`, which we
  never grant — so it can't discover names it doesn't already know; but
- it **can reach any sibling it can name**, and whether it can then read that
  sibling falls entirely to *that sibling's own mode*: `0700`/`0750` stays closed
  (the common case for real secrets), while `0755` — a checked-out repo, a
  `Public` dir — is readable. That is the leak.

No in-place ACL/mode arrangement closes it portably: Linux POSIX ACLs have no
`deny` primitive, macOS has no native bind mount, and stripping the world bit
from the operator's own directories would mutate their files. The answer is on a
different axis: control the **process's view** of the tree, not the tree. The
per-session confinement profile denies the human-home roots and re-opens only the
granted paths, so `~/b` is invisible to the agent process regardless of its mode —
per-entry control with nothing stamped on disk, torn down when the session exits.
[`sandbox-confinement-design.md`](sandbox-confinement-design.md) is the design
reference for the profile.

### Division of labour — account + allow-ACL + sandbox

The three layers are orthogonal and each does the one thing it is good at:

- **The dedicated agent account is the floor.** The credential boundary
  (`~/.jentic`, the DB, Keychain, the operator's browser/SSH) rests on Unix
  *ownership* — unconditional, and independent of any sandbox profile being correct
  or `sandbox-exec` surviving a future macOS.
- **ACL grants are *allow-only* and coarse.** The traverse-walk +
  rwx-leaf grants open the one chosen path to the agent uid — that is all they do.
  The macOS sandbox is **intersection-only** (a process reaches a file only if
  *both* its uid's DAC check *and* the profile allow it; the sandbox can only ever
  *subtract*, never grant a uid access its ownership denies), so a DAC grant is
  still required to let the agent uid reach an operator-owned path under `~`, and
  ACLs are the least-invasive form of it. **We do not add default-deny,
  inherited-deny, or per-sibling deny ACEs** — the ACL layer stays exactly this
  simple.
- **The confinement profile closes the sibling leak on top.** Even though the
  coarse ACL grant plus home traversal *could* expose a world-readable sibling
  `~/b`, the profile denies the agent process any in-home path it wasn't granted.
  The residual stops being an ACL problem and becomes a profile the launcher
  writes per session — no tree mutation, self-cleaning on exit.

Net: **agent account (floor) + allow-only ACL grant (opens the path) +
`sandbox-exec`/`bwrap` profile (trims to just the grant).** The sibling
non-negotiable is met by the confinement layer, not by making the ACLs cleverer.

---

## Direction 2 — the operator reaching into the agent's home

The agent home lives under a **shared, world-traversable parent**
(`/Users/Shared/…` on macOS, `/opt/…` on Linux) — never under any human's home,
so granting the operator in never means widening a home. At account creation the
operator is granted a **recursive, inherited** ACL over the agent home:

```bash
# macOS — the same explicit permission set as the leaf grant (incl. inherit bits),
# stamped over every existing entry too. `find ! -type l` is used rather than
# `chmod -R` because macOS rejects `-R -h` together, and `chmod -R` follows
# symlinks — so we walk real entries and skip symlinks explicitly.
sudo find "$AGENT_HOME" ! -type l \
  -exec chmod +a# 0 "user:$OPERATOR allow <full-leaf-ACE-set incl. inherit bits>" {} +
# Linux — recursive access ACL + default ACL
sudo setfacl -R -m u:"$OPERATOR":rwX "$AGENT_HOME"
sudo setfacl -R -d -m u:"$OPERATOR":rwX "$AGENT_HOME"
```

Why the operator needs it:

- **Supervision** — the operator can inspect and manage what the agent produces.
- **Setup writes the agent's identity as the operator.** The platform
  identity is written by `mkdir <agent-home>/.jentic` **as the operator**, before
  ownership is handed to the agent — which is why the grant must carry
  `add_subdirectory` (the macOS `write` shorthand omits it) and the inherit bits
  (without `file_inherit`/`directory_inherit` the operator loses access to
  whatever the agent creates afterwards).
- **The per-launch identity export refreshes agent-owned files.** Every
  `jentic run` exports the operator's active context into the agent home's own
  XDG store (`<agent home>/.config/jentic`, `<agent home>/.local/state/jentic`)
  and chowns it to the agent. Because the grant is applied **recursively over
  existing entries** (not just inherited onto future ones), the operator can
  overwrite the previous launch's now-agent-owned files on the next export.

This grant is **additive and idempotent** (re-applying only ever widens), so it
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
| **List** | `jentic run <agent> --list-grants` | none — shows recorded grants (danger-flagged) so access can't quietly sprawl; closes with the revoke one-liner |
| **Revoke** | `jentic run <agent> --revoke <dir>` | drops the **leaf** allow only; **ancestor traverse grants stay** (kept for the next grant). With the leaf gone the agent can't read/write the dir — and, unless world-readable, can't reach its contents |
| **Reset** | `jentic reset` | full teardown of the shared account: walks the recorded ancestor chains and drops the traverse grants **too**, settles the home, removes the account and sudoers drop-in, then wipes the operator's own identity state |

The key revoke-vs-reset distinction: **`--revoke` intentionally leaves ancestor
traverse ACLs in place** (so re-granting a sibling later is cheap), whereas
**`jentic reset` walks the ancestor chain and removes them**, leaving no agent
ACE behind. To remove a single context or identity instead of everything, use
`jentic context delete` / `jentic identity delete` — finer-grained removal is
not reset's job.

Recorded grants live in the operator's agent state at
`~/.config/jentic/agent-account.yaml`, as the single `agent_account:` object's
`granted_dirs` list — one consolidated list, because the grants are one uid's
ACLs regardless of which agent binary made them (older releases kept the record
in `~/.jentic/config.yaml`; it is read as a fallback and adopted on the first
write). `reset`'s survey re-scans the on-disk ACLs live and flags any **drift**
between what's recorded and what's actually present.

---

## What this model does *not* do

- It never changes the operator home's own permissions — setup never changes its
  mode, and teardown only removes the agent's named-user ACLs.
- It never touches the operator's *own* access to their files — every ACL is a
  **named-user** entry scoped to the single agent account.
- It does not share anything under the operator's home by symlink or PATH —
  those resolve with the agent's credentials and dangle at the home boundary (see
  the CLI-tool-sharing note in [`local-agent-isolation.md`](local-agent-isolation.md)).
- It never lets the agent **write** the binaries it is launched with. Executable
  routes on its PATH are mounted read-only by the confinement profile (see
  [Non-negotiable boundaries](#non-negotiable-boundaries)); read + execute stay,
  writes are denied, so the agent cannot rewrite its launcher to escape the sandbox
  on a later run.
