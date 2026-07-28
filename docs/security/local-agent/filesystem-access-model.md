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

### Open problem — the sibling-traversal residual

> **Provisional — under active investigation.** This section states the problem
> and the candidate fixes; the chosen direction is not yet settled and nothing
> here beyond the status quo is implemented.

**Symptom.** With `~` closed, the agent can touch nothing under it. Grant `~/a`
and the agent can now also reach `~/b` — a sibling it was never granted. Concretely
it can `ls ~/b` (and read its contents) when `~/b` happens to be world-readable.

**Root cause — directory execute is not per-entry.** To reach `~/a`, the kernel
checks *execute (search)* on every path component: `/` → `/Users` → `~` → `~/a`.
So the grant **must** add `user:agent allow execute` to `~` itself. But the search
bit on a directory is a single bit authorizing traversal to *any* child by name —
there is no Unix/POSIX-ACL primitive for "may traverse into `a` but not `b`". Once
the agent can search `~`:

- it still **cannot `ls ~`** — enumerating siblings needs *read* on `~`, which we
  never grant — so it can't discover names it doesn't already know; but
- it **can reach any sibling it can name**, and whether it can then read/list that
  sibling falls entirely to *that sibling's own mode*:
  - `~/b` at `0700`/`0750` → stays closed (the common case for real secrets). ✅
  - `~/b` at `0755` (world-readable — e.g. a checked-out repo, a `Public` dir) →
    the agent reads it as the "other" class. ❌ **This is the leak.**

**Blast radius.** Bounded to *world-readable* siblings — material any local user
could already read. Genuinely sensitive dirs (`~/.ssh`, `~/.aws`, `~/.jentic`, …)
are `0700`/`0750` and unaffected. The concern is precisely that the isolated agent
is meant to be *less* trusted than a normal local user, so "any local user could
read it" is not the bar we want.

**The hard constraint.** Any *in-place* access to `~/a` requires execute on `~`.
The only way to leave `~` untouched is to make the agent's path to the content
**not pass through `~`** at all.

**Candidate recourse** (no free lunch — closing it in place means either mutating
the operator's own files or a non-`~` path):

| # | Approach | Closes leak? | Cost / caveat |
|---|----------|:---:|---|
| 1 | **Bind-mount outside `~`** — expose `~/a` at e.g. `/Users/Shared/agent-grants/<agent>/a`, grant there, add **no** ACL to `~` | fully | The clean fix. Linux `mount --bind`; **macOS has no native bind mount** → needs macFUSE/`bindfs` (kernel-ext dep). Stateful: persist + tear down on revoke/reset/reboot. Root. |
| 2 | **Neutralize the world bit** — `chmod o-rwx` the operator's world-readable dirs under `~` at grant time (record + restore on reset) | existing siblings | Mutates the operator's *own* files; may break their tools; TOCTOU — dirs created after the grant leak again. |
| 3 | **Explicit deny ACEs** — add `deny read` for the agent on each sibling | existing siblings | macOS-only (POSIX ACLs have no deny); same enumerate-at-grant / new-sibling-leaks churn as #2; N siblings = N ACEs. |
| 4 | **Keep workspaces out of `~`** — steer agent-shared projects to `/Users/Shared/…`; `~` is never traversed | fully (by avoidance) | Cheapest, no new machinery; requires the workflow change that agent-touched code doesn't live under `~`. |
| 5 | **Status quo** — accept the residual; rely on the danger-classifier + real secrets being `0700` | bounded | Already implemented + documented (here). |

Why none of #1–#3 is a clean answer to the non-negotiable ("grant `~/a` without
implicitly exposing `~/b`"):

- **The ACL/mode approaches (#2, #3) fight per-directory search semantics and are
  asymmetric across platforms.** The exposure is only ever *world-readable*
  siblings, so the set to neutralize is the top-level *children* of each traversed
  ancestor — dozens, not the tree — which makes them far lighter than a recursive
  inherited-deny. But **Linux POSIX ACLs have no `deny` primitive at all**, so #3
  is macOS-only; on Linux the sole lever is stripping the operator's own world bit
  (#2), which mutates their files and can break their tooling. Both are stateful
  (record + restore) and TOCTOU-prone (a sibling created after the grant leaks
  until re-scanned).
- **The clean structural fix (#1) is not portable.** A bind mount gives a
  genuinely non-`~` path, but macOS has no native bind mount — it needs a
  third-party kernel extension (macFUSE/`bindfs`), which we've ruled out.

This is why the honest conclusion is that **ACLing the shared tree cannot satisfy
the non-negotiable portably** — every in-place variant either can't express deny
on Linux or has to mutate the operator's own files. That pushes the search toward
a different axis entirely: instead of controlling *the tree*, control *the
process's view of the tree*.

### A different axis — confine the process, not the tree

The primitives above all answer "who may touch this inode?" The alternative is to
answer "what can *this process* see?" — launch the agent into a restricted view
where only the granted paths exist (or are reachable) and everything else in `~`
is simply absent or denied, regardless of its mode. This is per-*process*, not
per-*inode*, so it gives true per-entry control (grant `~/a`, `~/b` is invisible)
**without stamping or mutating anything on disk**, and it tears down when the
session process exits. There is no single cross-platform API, but each OS has a
native mechanism with the same shape:

| OS | Primitive | How it confines | Privilege | Teardown | Maturity / caveat |
|----|-----------|-----------------|-----------|----------|-------------------|
| **Linux** | **Mount namespace** (`unshare`/`clone` `CLONE_NEWNS`) + user namespace, or **`bwrap`** (bubblewrap) | New empty mount ns; `--ro-bind`/`--bind` only the granted dirs in — everything else on the host is *invisible*, `~` never appears | **Unprivileged** via user namespaces (no root/setuid) | **Automatic** — ns + its mounts are destroyed when the last member process exits | Mature (Flatpak uses bwrap). Needs `CONFIG_USER_NS` + unprivileged userns enabled (some distros gate it) |
| **Linux (alt)** | **Landlock LSM** | Process restricts *itself* to a ruleset of allowed path FDs; unlisted paths denied. Doesn't hide, but denies — `~/b` unreadable even if `0755` | **Unprivileged** (needs `no_new_privs`) | Automatic — dies with the process; cannot be relaxed, inherited by children | Linux **5.13+** only; ABI is versioned/best-effort. Complements, doesn't replace, the namespace |
| **macOS** | **Seatbelt** sandbox profile via **`sandbox-exec -f profile.sb`** (SBPL) | `(deny default)` then `(allow file-read* (subpath "~/a"))` — a default-deny profile that only permits the granted subpaths; `~/b` reads denied by the kernel | **Unprivileged** (wraps the launched command) | Automatic — policy is per-process, gone when it exits | ⚠ **`sandbox-exec`/SBPL is deprecated and undocumented by Apple** (still functional, and is how the App Sandbox works under the hood). Depending on an undocumented interface is the central risk |

**Why this is the promising axis.** It's the only family that (a) satisfies the
per-entry non-negotiable exactly, (b) needs no root, (c) writes nothing to disk
and self-cleans on exit, and (d) has a real native mechanism on *both* OSes — so
"works on Linux and macOS" is met by a thin per-OS launcher shim rather than one
portable call. It also composes with the existing model: the 700 home + ACL grants
stay as defense-in-depth, and confinement narrows what the agent process can
actually reach within them.

**Open risks to resolve before committing.**

- **macOS deprecation (open, but de-risked).** `sandbox-exec` is the load-bearing
  piece and Apple marks it deprecated. It is, however, **confirmed still functional
  on current macOS**, and it is the same Seatbelt machinery the App Sandbox itself
  runs on — so it is not going away quietly. The residual risk is a *future* macOS
  removing the CLI wrapper; there is no supported alternative for our case (the App
  Sandbox proper requires an entitled, signed app — not applicable to wrapping a
  third-party agent binary we don't control). Verdict: **sandbox-exec might be
  viable to build on**, with a fallback to the ACL model if a future OS drops it.
- **Linux userns availability.** Unprivileged user namespaces are disabled by
  default on some hardened distros; need a detection + fallback story (Landlock,
  or the ACL model, when unavailable).
- **SBPL profile authoring.** Getting a default-deny profile that allows *just*
  the granted subpaths while still letting the agent binary, its dylibs, its own
  home, and the loopback socket to Jentic One work is fiddly and version-sensitive.
- **Interaction with the CLI-tool PATH sharing and provider config** — those
  paths (`/opt/homebrew/bin`, the agent's own `~/.aws`) must be allowed in the
  profile/binds too.

**Current leaning** (provisional, to be revisited): **process confinement** is the
most promising route to the non-negotiable and the next thing to prototype —
`bwrap` on Linux (mature, unprivileged, self-cleaning) and a `sandbox-exec` SBPL
profile on macOS (confirmed still functional; deprecation is a manageable future
risk, not a blocker). Keep **#4** (workspaces outside `~`) as the recommended
zero-machinery posture regardless, and retain the current 700 + ACL grants as the
defense-in-depth layer underneath. Treat #2/#3 as non-defaults, and consider #1
dead for our purposes (no portable macOS bind mount).

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
