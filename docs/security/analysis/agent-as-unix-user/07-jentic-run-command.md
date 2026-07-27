# Proposal: `jentic run <agent>` — a launcher that makes the safe path the default

> Builds directly on [`05-agent-as-own-unix-user.md`](05-agent-as-own-unix-user.md).
> That doc establishes *why* a dedicated, unprivileged agent user closes the
> credential-boundary gaps, and gives the raw `sudo -u … -i` launch line. This doc
> proposes a CLI wrapper — `jentic run <agent>` — that hides the sharp edges of that
> launch (per-user toolchain, directory access, session start-dir) behind one
> command, so operators get the isolated posture *by default* instead of by
> discipline. This is the "make the safe path the default path" lever from
> [`03-mitigations.md`](../03-mitigations.md), realised as a concrete command.

## Why a wrapper

The manual launch from [`05`](05-agent-as-own-unix-user.md) works, but it leaks
several concerns onto the operator every time:

- **Which user?** They must remember the `<operator>-local-agent` naming and type
  `sudo -u … -i`.
- **Is the binary there?** The agent account has its own `$HOME` and its own
  toolchain; a coding-agent binary installed for the operator is *not* on the
  agent's `PATH` (real-issue #2 in [`05`](05-agent-as-own-unix-user.md)). Today this
  surfaces as a bare `claude: not found`.
- **Which directory?** A login shell starts in the agent's home; if the operator
  wanted the agent to work on the repo they were standing in, the agent may have no
  filesystem access to it — and granting that access is exactly where a careless
  operator can re-open the boundary they just closed (e.g. by exposing `~`).

`jentic run <agent>` collapses all three into one guided command.

## Command shape

```
jentic run <agent> [path] [flags]
```

- **`<agent>`** — a known coding-agent identifier: `claude`, `hermes`, `codex`,
  `cursor-agent`, … Each maps to a small descriptor: the binary name, how to detect
  it, and (optionally) how to install/copy it. Unknown identifiers error with the
  list of known agents.
- **`[path]`** — optional working directory for the session. Defaults to the
  current directory; the directory-access flow (below) decides whether the agent can
  actually be given it.
- **Flags** (all optional; the command is fully interactive without them, so it
  stays a copy-paste for newcomers and scriptable for power users):
  - `--home` — skip the current dir; open the session in the agent's own home.
  - `--allow-dir` / `--no-allow-dir` — pre-answer the directory-access prompt.
  - `--yes` — assume the safe default for every prompt (non-interactive); never
    picks a flagged-dangerous option (it declines instead).
  - `--agent-user <name>` — override the derived `<operator>-local-agent` user.

### State lives in the operator's `jentic` config

`jentic run` must not re-derive or re-prompt for the agent's identity every
invocation — all of it is **persisted in the operator's own `jentic` CLI config**
(the config that already lives in the operator's home and is read by every `jentic`
command). This is the operator-side record of "which local agent(s) exist and where
they live." Nothing secret goes here — it's paths and names, not keys — so it sits
safely in the operator's config file.

Proposed shape (one entry per configured local agent, keyed by the `<agent>`
identifier the operator types):

```yaml
local_agents:
  claude:
    user: alice-local-agent          # the OS account jentic run becomes
    home_dir: /Users/Shared/alice-local-agent
    binary: claude                    # what to exec / probe
    granted_dirs:                     # durable directory ACLs (see step 3)
      - /Users/Shared/alice-local-agent/work/api
      - /Users/alice/projects/scratch
    created_at: 2026-07-27T15:45:00Z
```

- **`user` / `home_dir`** are written by `jentic agent-user setup` when it creates
  the account, and read by every `jentic run` so the launcher never has to guess the
  name or home (it also lets `--agent-user` overrides persist).
- **`granted_dirs`** is the authoritative inventory backing `--list-grants`,
  `jentic revoke-dir`, and the `jentic doctor` sprawl check (step 3). The ACL on disk
  is the source of truth for *access*; this list is the record of *what jentic
  granted*, so doctor can reconcile the two and flag drift.
- Reading/writing this is a plain load-mutate-save of the existing operator config —
  no new store, no new file, no new permissions.

### What it does, in order

1. **Resolve the agent user** from `local_agents[<agent>]` in the operator's config
   (or the `<operator>-local-agent` default), and verify the account exists; if not,
   offer to create it (the `jentic agent-user setup` flow — see
   [below](#agent-creation-belongs-in-jentic-not-jenticctl)), which runs the
   [`05`](05-agent-as-own-unix-user.md) recipe and records `user`/`home_dir` in the
   config.
2. **Ensure the agent's binary is installed** for that user — the provisioning flow
   (next section, added in a later commit).
3. **Resolve the working directory** and its access — the directory-access flow
   (added in a later commit), including danger-flagging.
4. **Launch** the agent as the agent user, in the resolved directory, via a login
   shell so no operator environment leaks.

Each of steps 2–4 is detailed in its own section below.

## Step 2 — ensure the agent's binary is installed

The agent account has its own `$HOME` and its own toolchain. A coding-agent binary
the operator installed for themselves is **not** on the agent's `PATH` — and
shouldn't be, since it may sit inside the now-`700`-locked operator home. So the
first thing `jentic run` does after resolving the user is check whether `<agent>` is
runnable *as the agent user*, and if not, offer to provision it.

### Detection

Probe as the agent, in a login shell, so the check sees exactly what the launch
will:

```bash
sudo -u "$AGENT" -i bash -lc 'command -v <binary>'
```

If that resolves, skip to step 3. If it doesn't, `jentic run` knows the binary is
missing (as opposed to a `PATH` problem — it distinguishes the two by also probing
the well-known install location for that agent, e.g. `~<agent>/.local/bin/<binary>`;
found-but-not-on-PATH prints the one-line `PATH` fix instead of re-installing).

### Provisioning — copy vs. fresh install

When the binary is genuinely absent, prompt the operator with two routes, defaulting
to the one that's correct for that agent:

```
Agent 'claude' is not installed for user alice-local-agent.
  [c] Copy the operator's binary  (/Users/alice/.local/bin/claude → the agent)   [default]
  [i] Install a fresh copy as the agent  (curl … | bash)
  [s] Skip — I'll set it up myself
Choice [c]:
```

- **Copy** — for **self-contained, single-binary** agents (e.g. Claude Code's native
  install at `~/.local/bin/claude`), the binary carries no credentials, so copying it
  to the agent's `~/.local/bin` and `chown`-ing it to the agent is safe and instant.
  This is the default when the operator already has a detectable single-file binary.
  `jentic run` does the `mkdir -p`, `cp`, and `chown` for the operator.
- **Install fresh** — for agents distributed via a package manager (npm global, etc.)
  or when no operator binary is found, run the agent's documented installer *as the
  agent user* so the toolchain lands in the agent's `$HOME`. This is the more
  maintainable route (it can self-update), at the cost of needing the agent's
  toolchain (node/npm, curl) reachable.
- **Skip** — print the manual copy/install commands and exit, for operators who want
  to do it by hand.

> **Copy carries the binary, never the credentials.** Auth for these agents lives in
> the agent user's own Keychain / `~/.claude.json`, which the copy does not touch —
> so a copied binary still triggers the agent's own first-run login as the agent
> user. That per-user auth is the boundary working as designed, not a bug to paper
> over. `jentic run` should say so in one line rather than silently copying config.

### The agent descriptor

Each known `<agent>` is one small record so adding an agent is data, not code:

| Field | Example (`claude`) | Purpose |
| ----- | ------------------ | ------- |
| `binary` | `claude` | what to exec and probe with `command -v` |
| `probe_paths` | `~/.local/bin/claude` | distinguish missing vs. not-on-PATH |
| `install` | `curl -fsSL https://claude.ai/install.sh \| bash` | the fresh-install command, run as the agent |
| `single_binary` | `true` | whether **copy** is offered as the default route |

`hermes`, `codex`, `cursor-agent`, … are additional rows. An unknown `<agent>`
errors with the list of known identifiers.

## Step 3 — resolve the working directory and its access

By default `jentic run claude` (no `[path]`) means "work on the directory I'm
standing in." But the operator's cwd is almost always inside the operator's own home
— which is now `700` and owned by the operator — so **the agent user has no
filesystem access to it.** The agent would start there and immediately fail to read
anything. `jentic run` resolves this up front rather than letting it surface as
opaque permission errors mid-session.

### The check

For the resolved target directory (the `[path]` arg, or cwd), test whether the agent
user can actually read and write it:

```bash
sudo -u "$AGENT" test -r "$DIR" -a -w "$DIR" -a -x "$DIR"
```

If the agent already has access (e.g. the directory is under the agent's own home,
or a shared-group workspace from [`05`](05-agent-as-own-unix-user.md)), `jentic run`
launches there with no prompt. If not, it offers a succinct choice:

```
Agent alice-local-agent has no access to /Users/alice/projects/api.
  [a] Allow the agent read/write to this directory   (adds an inherited ACL)
  [h] Open in the agent's home instead  (~alice-local-agent)             [default]
  [c] Cancel
Choice [h]:
```

- **Allow** — grant the agent access to *just this directory* via an inherited ACL,
  the same named-user mechanism [`05`](05-agent-as-own-unix-user.md) uses for the
  operator (reversed direction):

  ```bash
  # macOS
  sudo chmod +a "user:$AGENT allow read,write,execute,file_inherit,directory_inherit" "$DIR"
  # Linux
  sudo setfacl -R -m u:"$AGENT":rwX "$DIR" && sudo setfacl -R -d -m u:"$AGENT":rwX "$DIR"
  ```

  The grant is **scoped to that directory subtree only** — it does not open the
  operator's home generally, only the one path the operator chose. This is a
  system-level grant; **Claude Code (or whichever agent) still governs its own
  workspace-trust prompt** on top, so the operator confirms twice for two different
  things (OS access vs. the agent's own trust model). `jentic run` says which is
  which so the two prompts aren't confused.

- **Open in home** (default) — skip the grant entirely and launch the session in the
  agent's own home, the always-accessible shared space. The safe default: it never
  widens access, and the operator can copy files into the agent's home if they want
  the agent to see them.

- **Cancel** — do nothing.

`--allow-dir` / `--no-allow-dir` / `--home` pre-answer this prompt for scripted use.

### Persistence and revocation

An ACL granted this way **persists** after the session ends — the agent keeps access
to that directory until it's removed. **This is intentional:** the operator grants a
working directory once, and the agent can be re-run against it on subsequent days
without re-prompting; a project the agent works on is not a per-session decision.
Because the grant is durable, `jentic run` should:

- **Not silently accumulate grants.** On a repeat run against an already-granted
  directory it proceeds without re-prompting, but a `jentic run --list-grants` (or a
  line in `jentic doctor`) should surface every directory the agent has been given,
  so access doesn't quietly sprawl.
- Offer **`jentic revoke-dir <path>`** (or `--revoke` on the run command) that
  removes the ACL entry, so granting is reversible in one step rather than requiring
  the operator to remember `chmod -a` / `setfacl -x` syntax.

### Flagging directories that shouldn't be granted

Not every "Allow" is a good idea. Granting the agent access to the **operator's home
root** (or other sensitive trees) would hand back exactly the read path that
`chmod 700 ~` in [`05`](05-agent-as-own-unix-user.md) was there to close — defeating
the whole posture in one keystroke. So before offering **Allow**, `jentic run`
classifies the target directory and, when it's dangerous, makes **Allow** the
non-default, explicitly-confirmed option:

```
⚠  /Users/alice IS THE OPERATOR'S HOME. Granting the agent access here re-opens the
   credential boundary this setup exists to protect (keys, browser profile, SSH).
  [h] Open in the agent's home instead                                   [default]
  [a] I understand the risk — grant anyway  (type 'grant' to confirm)
  [c] Cancel
Choice [h]:
```

Directories that trip the warning (a denylist, matched against the resolved absolute
path):

- **the operator's home root** (`~operator`) and its direct dotfile dirs — `~/.ssh`,
  `~/.jentic`, `~/.aws`, `~/.config`, `~/.gnupg`, the browser profile dirs, Keychain
  paths;
- **any other user's home** (`/Users/*`, `/home/*`) that isn't the agent's own;
- **system trees** — `/etc`, `/usr`, `/var`, `/System`, `/Library`, `/`.

For a flagged path the safe options come first, **Allow** requires a typed
confirmation (not just a keypress), and `--yes` **declines** it rather than granting
(the non-interactive default must never silently punch the hole). A normal project
directory under a neutral location grants with the plain prompt from step 3.

> The point isn't to make granting impossible — an operator may have a legitimate
> reason to point the agent at an unusual path. It's to ensure the *dangerous* grants
> are never the path of least resistance, and never happen without the operator
> seeing why they're dangerous.

## Step 4 — launch

With user, binary, and directory resolved, launch as the agent user in a **login
shell** (fresh environment, so no operator tokens/vars leak) in the resolved
directory:

```bash
sudo -u "$AGENT" -i bash -lc 'cd "$DIR" && exec <binary>'
```

For the "open in home" path there's no `cd` — the login shell already starts in the
agent's home. This is exactly the launch line from [`05`](05-agent-as-own-unix-user.md),
now with the user/dir/binary filled in by the preceding steps instead of by the
operator's memory.

## Porting an existing workspace into the setup

A common ask: the operator already has a Claude workspace (a repo with `.claude/`,
history, MCP config, working files) and wants the new agent user to just *use it*,
rather than starting empty. The clean way to express that is **point the agent's
home directory at the existing workspace** — then all the agent's per-`$HOME` state
(`~/.claude`, `~/.claude.json`, the `jentic` identity) lands alongside the work
that's already there. `jentic agent-user setup --home <existing-dir>` (or a
`jentic agent-user adopt <dir>`) would set `home_dir` to that path instead of the
default.

The catch, and how to keep isolation:

- **If the workspace is on a neutral/shared path** (e.g. already under
  `/Users/Shared/...` or `/opt/...`): straightforward. `setup` sets the agent's home
  there, `chown`s it to the agent, adds the operator's inherited ACL, and it works
  like the default case. No isolation concern.

- **If the workspace is *inside the operator's home*** (e.g.
  `~/projects/my-app`) — the hard case, because `chmod 700 ~` (the isolation
  guarantee from [`05`](05-agent-as-own-unix-user.md)) means the agent can't even
  traverse `~` to reach it, and we must **not** loosen `~` to let it. Options, best
  first:

  1. **Move, don't share (recommended).** Relocate the workspace out of the
     operator's home to a neutral path and set the agent's home there:
     `mv ~/projects/my-app /Users/Shared/<agent>-home && ln -s` a convenience symlink
     back if the operator still wants it at the old path. The workspace leaves the
     protected tree entirely; nothing about `~` changes. `jentic agent-user adopt`
     can do the move + `chown` + operator-ACL in one step. Git history, `.claude/`,
     files — all preserved, just at a new path.
  2. **Copy in (when the original must stay put).** Copy the workspace contents into
     a fresh agent home on a neutral path (`cp -a`, then `chown` to the agent). The
     operator keeps their original inside `~`; the agent gets an independent copy.
     Downside: two diverging trees — fine for a one-off port, not for ongoing shared
     editing.
  3. **Traversal-only exposure (discouraged, must be explicit).** Technically you
     can grant the agent `--x` on `~` plus an ACL on just the workspace subdir, so
     the agent reaches `~/projects/my-app` without `~` being listable. But this
     re-opens a *path* into the operator's home and leans on every sibling dotfile
     being independently locked — exactly the fragility [`05`](05-agent-as-own-unix-user.md)
     rejects. `jentic` should treat this like a step-3 denylist hit: refuse it by
     default, and only allow it behind the typed-confirm danger prompt, never as the
     `adopt` default.

> **Rule of thumb the tool should enforce:** the agent's home must end up on a path
> that is *not* under any human's `700` home. `adopt` moves or copies to satisfy
> that; it never widens `~` to make an in-home workspace reachable. The port is a
> one-time relocation, after which the ported workspace *is* the agent's home and the
> operator reaches it via the inherited ACL like any other agent home.

## Agent creation belongs in `jentic`, not `jenticctl`

Account creation, binary provisioning, directory grants, and launch are all one
responsibility and must live in **`jentic`** — the client-side package that is
**guaranteed to run in the same environment as the agent** (the operator's machine,
as the operator's user). `jenticctl` administers a Jentic One deployment and may run
on a different host entirely; if it owned agent-user setup, that logic could land
somewhere with no relationship to where the agent actually runs. Keeping the whole
lifecycle in `jentic` means:

- `jentic agent-user setup` — one-time: create the `<operator>-local-agent` account
  and its home per the [`05`](05-agent-as-own-unix-user.md) recipe.
- `jentic run <agent>` — daily driver: the four steps above.
- `jentic revoke-dir` / `jentic run --list-grants` — manage the durable grants.
- `jentic doctor` — report every ACL grant made and warn on any pointing at
  sensitive trees, and flag when the agent appears to run as the *same* uid as the
  operator (the [`05`](05-agent-as-own-unix-user.md) tripwire).

`jenticctl` keeps its deployment-side role (installing/operating Jentic One itself);
it does **not** create or manage agent users.

## How this feeds [`05`](05-agent-as-own-unix-user.md)

- `jentic run` is the **daily-driver** counterpart to the one-time
  `jentic agent-user setup`: setup creates the account; `run` is what the operator
  types every time after.
- It turns the three real-issues from [`05`](05-agent-as-own-unix-user.md)'s honest
  assessment into guided prompts instead of tripwires: **per-user toolchain** (step
  2), **shared-workspace access** (step 3), and it keeps the **login-shell / own-home
  start** correct by construction (step 4).
- **Not owning the agent client still holds** (framing point #4): `jentic run` wraps
  *our* CLI and the OS, and merely execs the third-party agent binary. The operator's
  only new verb is `jentic run <agent>` instead of the raw `sudo -u … -i` line.
