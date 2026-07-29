# Design: run the agent as its own Unix user, wrapped by `jentic`

> Builds on the [analysis](analysis.md). The structural fix is: run a CLI coding
> agent (Claude Code is the worked example) under a **dedicated, unprivileged OS
> user** distinct from the operator's login user, so it cannot read the
> operator's `~/.jentic` keyset, DB, browser profile, or Keychain. It reaches
> Jentic One the way any client does — loopback HTTP with its own scoped token.
>
> The raw mechanism is a handful of `sysadminctl`/`chmod`/`sudo -u` lines, but
> operators should never have to type them. **The `jentic` CLI wraps the whole
> lifecycle** — the shell snippets in this doc are what the commands run under
> the hood, shown so the behaviour is auditable, not so anyone runs them by hand.
> Platform details are as of 2026-07 and deliberately avoid depending on any
> machine's group defaults.

## The operator's whole workflow — three commands

```bash
# 1. One-time: onboard. Right after picking your operator, bootstrap/wizard offers
#    to create the dedicated agent user + its home (jentic run then confines each
#    session so the agent sees only the directories you grant).
jentic bootstrap                         # (or: jenticctl wizard — same flow)

# 2. Every session: launch the agent, isolated, in the directory you name.
jentic run claude ~/projects/my-app

# 3. When you're done with an agent entirely: tear down what was created locally.
#    Run it as yourself — reset prompts for your password when it hits the
#    privileged steps (requires sudo to complete). NOT `sudo jentic reset`.
jentic reset
```

The agent-user account is **created as part of onboarding** — there is no
separate `agent-user setup` command. After you choose an operator (Claude,
Cursor, …), `jentic bootstrap` and `jenticctl wizard` both offer to isolate it
behind a dedicated Unix user; the account-creation step is shared between the two
the same way the skill step is (see below). Everything else — provisioning the
agent's binary, seeding config, granting a working directory, launching in a
fresh login shell — is handled inside `jentic run` as guided prompts. Management
shortcuts:

```bash
jentic run claude --list-grants          # show every directory the agent can reach
jentic run claude --grant   ~/work/api   # grant a directory, then exit
jentic run claude --revoke  ~/work/api   # remove a grant, then exit
```

## Why this posture works

If the agent runs as `<operator>-local-agent` and the operator is their normal
login user, then by Unix ownership (plus macOS TCC for protected data) the agent
user cannot read the operator's `~/.jentic` keyset, DB files, browser profile, or
Keychain. That closes AP-1–AP-4 in one move — the agent can only reach Jentic One
over loopback with its own scoped token.

It's weaker than a container (same kernel, same machine — a
local-privilege-escalation bug crosses it) but needs no container runtime and
leaves the connection model unchanged. The pragmatic middle rung.

The isolation is **one-directional and that asymmetry is the whole point**:
sharing the *agent's* home outward to the operator is safe (the operator is
trusted and will want to read the agent's outputs); the thing we never do is the
reverse — expose the operator's home/keys/browser to the agent.

## Creating the account — folded into `bootstrap` / `wizard`

Account creation is not a standalone command. Right after the operator picks
their agent, `jentic bootstrap` and `jenticctl wizard` share one step (the same
way they share the skill step: the wizard delegates to bootstrap, so the step
lives in the bootstrap flow and both reach it) that offers to isolate the agent
behind a dedicated Unix user. The step is deliberately **sudo-last**: it asks
*"Create a dedicated user account for your local agent? (requires sudo)"* **before
any privileged command runs**, so an operator who declines never triggers a
password prompt and no half-provisioned state is left behind. The choice is
recorded either way (`local_agents.<agent>.account_created`), because "declined,
running same-user" shapes later behaviour.

Opting in shows an editable, prefilled dialog — account name, home directory, and
two toggles for whether to copy the operator's agent config and LLM-provider
config into the agent's home (the same seeding, with the same warnings, described
under [Step 3](#step-3--config-seeding-opt-in-once-never-clobbers)). Because those
sources are already resolved from the operator's home before the dialog is shown,
each toggle **names the exact files it would copy** ("Will copy: ~/.claude,
~/.claude.json") and the provider toggle names the detected provider; a toggle
defaults to Yes only when there is actually something to copy (otherwise it offers
"No" with a *none found* note). On confirm it runs, idempotently and with platform
detection, what is otherwise a short manual recipe. After registration completes
and the profile summary is shown, `bootstrap` then **offers to start a session in
the agent's home right there** — displaying `cd <agent home>; jentic run <agent>`
and, on yes, launching it; declining leaves the command printed for later. For a
single-user machine that recipe is:

```bash
# macOS
AGENT="$(whoami)-local-agent"; AGENT_HOME_DIR="/Users/Shared/$AGENT"
sudo sysadminctl -addUser "$AGENT" -fullName "$(whoami) Local Agent" -home "$AGENT_HOME_DIR" -password -
sudo createhomedir -c -u "$AGENT"                                  # sysadminctl only *records* the home; this creates it
sudo chmod +a "user:$(whoami) allow read,write,execute,file_inherit,directory_inherit" "$AGENT_HOME_DIR"
# The operator's home is NOT force-locked to 700 — in-home confidentiality against
# the agent is enforced per session by the confinement profile (see below).
```

```bash
# Linux
AGENT="$(whoami)-local-agent"; AGENT_HOME_DIR="/opt/$AGENT"
sudo useradd -m -d "$AGENT_HOME_DIR" -s /bin/bash "$AGENT"
sudo setfacl -R -m u:"$USER":rwX "$AGENT_HOME_DIR" && sudo setfacl -R -d -m u:"$USER":rwX "$AGENT_HOME_DIR"
```

Three things worth calling out, because they're the load-bearing parts:

- **Per-session process confinement is the isolation guarantee.** `jentic run`
  launches the agent under a confinement profile (sandbox-exec on macOS, bwrap on
  Linux) that denies the operator's home except the granted subpaths, and **errors
  closed** if confinement is unavailable. This replaces the earlier blanket `chmod
  700 ~`: it closes the same read path *and* the sibling-traversal leak a whole-home
  700 could not, without changing the operator home's own permissions. Real secrets
  keep their own `0700` modes regardless. See
  [`sandbox-exec-plan.md`](sandbox-exec-plan.md) and the
  [filesystem-access model](filesystem-access-model.md).
- **The agent's home is the shared space.** It lives under an existing shared
  parent (`/Users/Shared/…`, `/opt/…`), owned by the agent, with an *inherited*
  ACL granting the single operator read/write. The `*_inherit` flags mean the
  grant applies to everything the agent creates later, so the operator keeps full
  access without re-running anything. No group, no world access.
- **Per-user auth is expected, not a bug.** The agent gets its own `$HOME`, so its
  own Claude config/history (`~/.claude`, `~/.claude.json`), its own login
  Keychain, and its own `jentic` identity (Ed25519 key under `~/.jentic`). The
  agent authenticates its own tools once, as itself — it can't ride the operator's
  sessions, which is the boundary working as designed.

### Where the platform identity is written — registration after the user decision

The agent-user question is asked **before** the platform registration (DCR) so that
by the time we mint and persist a jentic identity we already know *which config it
belongs in*. Registration is deliberately sequenced after the isolation decision,
not before it.

The consequence is a small but important targeting rule for **every** command that
writes an agent profile (bootstrap today; any create/update later):

- **Self-user agent (isolation accepted).** The agent's jentic identity — its
  Ed25519 key, agent ID, and tokens — is written **once, into the agent's own
  `~/.jentic`** (`<agent home>/.jentic`), which then belongs to the agent. That
  directory is the **single source of truth** for the agent's identity. The
  operator's config keeps only a **reference** to it: `local_agents.<agent>` records
  `config_dir` (a pointer to the agent's `~/.jentic`) and `home_dir` (the agent's
  home). There is **no dual-write** — the operator's config never holds a second
  copy of the identity, only the pointer and the home metadata needed to find and
  manage it.
- **Same-user agent (isolation declined).** Nothing changes: the identity lives in
  the operator's own `~/.jentic` as usual, and `config_dir` stays empty.

Mechanically, bootstrap targets the write by re-rooting the config paths at the
agent's `~/.jentic` for the identity + default-profile write, then `chown -R`s that
directory to the agent so it owns its own `0600` key and tokens (the operator
created the files, so they start operator-owned and must be handed over). This
"reference, not copy" model is what keeps the multi-config problem simple: one
writer, one source of truth, one pointer.

> **Future improvement — dual-write / propagation.** If a later need arises to keep
> a *projection* of agent state in the operator's config (e.g. so operator-side
> tooling can list agents without reading each agent's `~/.jentic`), that is a
> deliberate follow-up, not the current behaviour. For now, operator commands that
> touch a self-user agent write **only** to that agent's config and update the
> operator-side reference/metadata — never a second identity copy.

> **Future improvement — default agent home under the operator's home.** Today the
> agent home lives under a shared parent (`/Users/Shared/<agent>`, `/opt/<agent>`).
> A nicer default would be `~/jentic-agents/<agent-name>` — discoverable, clearly
> owned by the operator's account tree, and self-documenting. The open question is
> purely how it interacts with the isolation paradigm: on stock macOS the operator's
> home is `0700` by OS default, so an agent running as its own user **cannot traverse
> into `~` at all** without a grant. Putting the agent home under `~` would require
> granting the agent an **execute-only traverse** grant on
> the operator's home (the same Layer-1 mechanism [Step 4](#step-4--directory-access-traverse-walk--rwx-leaf--confinement)
> already uses for granted working directories) so it can reach `~/jentic-agents/<agent>`
> without being able to *read* anything else in `~`. That is believed to be a small
> change (reuse the existing traverse-grant path, point the default home there), but
> it is **documented here and deferred** — we'll follow up with the implementation
> once the traverse-into-`~` interaction is confirmed safe. Note it does **not**
> weaken the boundary: execute-without-read still blocks directory listing and file
> reads of `~`, and the confinement profile still denies everything but the granted
> path; it only opens the single named path.

### Optional: passwordless launch

Without this, each `jentic run` prompts for the operator's password (cached
per-terminal ~5 min). A scoped `sudoers` drop-in, installed through `visudo` so a
typo can never lock you out of sudo:

```bash
echo "$(whoami) ALL=($(whoami)-local-agent) NOPASSWD: /bin/bash" | sudo SUDO_EDITOR='tee -a' visudo -f /etc/sudoers.d/jentic-agent
```

The `(<operator>-local-agent)` target scopes this to *becoming the agent user* —
not a general root grant. On macOS you can instead enable Touch ID for sudo
(`auth sufficient pam_tid.so` in `/etc/pam.d/sudo_local`).

## `jentic run <agent>` — the daily driver

```
jentic run <agent> [path] [flags]
```

- **`<agent>`** — a known coding-agent identifier (`claude`, …), mapping to a
  small descriptor: binary name, how to detect it, how to install/copy it.
- **`[path]`** — working directory for the session; defaults to the current dir.
- **Flags** (all optional — the command is fully interactive without them):
  `--home`, `--allow-dir`/`--no-allow-dir`, `--seed-config`/`--no-seed-config`,
  `--yes` (assume the safe default; never picks a flagged-dangerous option),
  `--agent-user <name>`, and the `--list-grants`/`--grant`/`--revoke` shortcuts.

State — which local agents exist, their user/home, and their durable directory
grants — lives in the operator's own `jentic` config (`local_agents:` keyed by the
`<agent>` identifier). Nothing secret: paths and names, not keys. Reading/writing
it is a plain load-mutate-save of the existing operator config, so `jentic run`
never has to re-derive or re-prompt for the agent's identity.

What it does, in order:

1. **Resolve the agent user** from config (or the `<operator>-local-agent`
   default); if the account doesn't exist, offer to create it — the same account
   step `bootstrap`/`wizard` run, for operators who skipped it during onboarding.
2. **Ensure the binary is installed** for that user (below).
3. **Optionally seed config** — the agent's own settings, then the LLM-provider
   config (below).
4. **Resolve the working directory and its access** (below).
5. **Launch** as the agent user, in a login shell, in the resolved directory.

### Step 2 — binary provisioning (copy vs. install)

A binary the operator installed for themselves is not on the agent's `PATH` (and
shouldn't be — it may sit inside the operator's home, which the agent can't reach).
`jentic run` probes as the agent and, if the binary is genuinely absent, offers:

- **Copy** — for self-contained single-file binaries (e.g. Claude Code's
  `~/.local/bin/claude`). The binary carries no credentials, so copying it to the
  agent's `~/.local/bin` and `chown`-ing it is safe and instant. The default when
  a single-file binary is detected.
- **Install fresh** — run the agent's own installer *as the agent user* so the
  toolchain lands in the agent's `$HOME`. More maintainable (self-updates).
- **Skip** — print the manual commands and exit.

> **Copy carries the binary, never the credentials.** Auth lives in the agent
> user's own Keychain / `~/.claude.json`, which the copy doesn't touch — so a
> copied binary still triggers the agent's own first-run login as the agent user.

### Step 2a — sharing the operator's installed CLI tools

The agent binary is only one tool; a real session also wants `git`, `jq`, `rg`,
`node`, and whatever else the operator has installed. Rather than reinstall the
world in the agent account, `jentic run` puts the operator's **world-readable**
tool directories on the agent's `PATH`. It appends an idempotent, marker-guarded
`export PATH="$PATH:…"` line to the agent's login profiles — the same mechanism
that adds `~/.local/bin` — so agent sessions resolve those binaries directly. It
is appended *after* `$PATH`, so an agent-owned tool (in the prepended
`~/.local/bin`) always shadows the operator's copy.

**Only world-traversable dirs outside the operator's home qualify.** On macOS
that gap is Homebrew's `/opt/homebrew/bin` (+ `sbin`): world-readable, but not on
a fresh login shell's default `PATH`. `/usr/bin`, `/bin`, `/usr/local/bin`, etc.
are already on the default `PATH` (`/etc/paths`), so they need nothing. On Linux
the candidates are `/usr/local/bin`, `/opt/homebrew/bin`, `/snap/bin`.

**Home-local tool dirs are deliberately NOT shared** — `~/.local/bin`,
`~/.cargo/bin`, `~/go/bin`, npm-global, pyenv/rbenv shims, and anything else under
the operator's home. The agent cannot reach into the operator's home (stock macOS
homes are `0700`, and the per-session confinement profile denies the home except
granted paths regardless), so a symlink or `PATH` entry pointing there resolves
with the *agent's* credentials and dangles with `EACCES` at the home boundary.
Sharing those would mean opening a path into `~` the confinement layer exists to
keep closed. If the agent needs such a tool, install it in
the agent account (`brew`, `pipx`, the tool's own installer run as the agent) or,
for a self-contained binary, use the Step 2 copy route. Sharing home-local tools
via a curated copy is a possible future addition, but is out of scope today.

> **This is convenience, not a boundary.** `PATH` sharing is best-effort: a
> failure warns and the launch continues. It exposes only already-world-readable
> executables — it grants no new filesystem reach, and the credential boundary is
> unchanged.

### Step 3 — config seeding (opt-in, once, never clobbers)

Provisioning gives a runnable tool but not the operator's *settings*. After the
binary step `jentic run` offers, once and opt-in, to copy the agent's config
(`~/.claude`, `~/.claude.json`) into the agent's home. Guards: it only runs when
the agent has no config of its own yet (a re-run never overwrites the agent's
evolved settings), it's off by default, and `--yes` declines.

**Provider-aware seeding.** Copying `~/.claude` alone isn't enough when Claude
Code is pointed at a cloud provider: that provider's credentials live elsewhere.
`jentic run` reads the `env` block of the operator's `~/.claude/settings.json` and
offers to seed *that provider's* config only:

| `settings.json` env | provider | config seeded |
| ------------------- | -------- | ------------- |
| `CLAUDE_CODE_USE_BEDROCK=1` | AWS Bedrock | `~/.aws` |
| `CLAUDE_CODE_USE_VERTEX=1` | Google Vertex | `~/.config/gcloud` (+ any `GOOGLE_APPLICATION_CREDENTIALS` file) |
| neither | Anthropic API | nothing extra — the key (if any) is already in the seeded agent config |

- **Config only — never the login token.** For SSO-backed providers we copy the
  configuration (profiles, SSO-session definitions) but deliberately exclude the
  cached SSO/session token. Claude Code performs the provider login
  programmatically on first launch, so the agent gets its own fresh session.

> **Warning shown at both seed prompts.** Until you front the provider with an LLM
> proxy (e.g. [LiteLLM](https://docs.litellm.ai/)), its credentials live in the
> agent's environment — for SSO the ability to initiate logins as the profile, for
> a static key the key itself. A proxy holds the credentials so nothing sensitive
> lands in the agent account at all. Seeding is a credential crossing the boundary,
> so it is always surfaced and confirmed, never silent.

### Step 4 — directory access: traverse-walk + rwx-leaf + confinement

By default `jentic run claude` works on the directory the operator is standing in
— almost always inside the operator's home, which the agent user can't read
without a grant. `jentic run` resolves this up front rather than letting it surface
as opaque permission errors. It tests whether the agent can read/write/execute the
target; if not, it offers **Allow** / **Open in agent's home** (default) / **Cancel**.

Granting a directory **inside the operator's home** — the important case, since
`~` stays closed to the agent by default — uses **named-user ACLs** (scoped to the
single agent user; the operator's own access is never touched) in three layers:

- **Layer 0** — the default-deny is the per-session confinement profile
  (sandbox-exec/bwrap), which denies the operator home except the granted subpaths;
  we add **no** ACL to `~` itself and no longer `chmod 700 ~`.
- **Layer 1 — traverse-walk** — execute-only (search, not list, not read) on each
  ancestor from `~` down to the leaf's parent, so the agent can pass *through* to a
  known path without enumerating it.
- **Layer 2 — rwx-leaf** — full, **recursive**, inherited read/write/execute on the
  chosen workspace and everything already in it or created later.

Before offering **Allow**, `jentic run` classifies the target: the operator's home
root, its sensitive dotfile dirs, other users' homes, and system trees all trip a
warning — **Allow** requires a *typed* confirmation and `--yes` declines. Revoke
drops the leaf allow (ancestor traverse stays); a full `jentic reset` walks the
ancestor chain and drops those too. Grants **persist across sessions** by design,
so `--list-grants` exists to keep access from quietly sprawling.

> The exact ACL commands, the macOS `write`-shorthand gotcha (why the permission
> set is spelled out in full), the recursive-over-existing-contents consequence,
> the world-readable-ancestor residual, and the full grant/revoke/reset lifecycle
> are documented in [**`filesystem-access-model.md`**](filesystem-access-model.md).

### Step 5 — launch

```bash
sudo -u "$AGENT" -H bash -lc 'cd "$DIR" && exec <binary>'
```

Two details from live testing, both baked into `jentic run`:

- **`-H bash -lc`, not `sudo -i`.** `sudo -i` re-serializes the command through the
  login shell and mangles multi-token snippets; plain `sudo -u … -H bash -lc`
  passes argv straight through. `-H` points `HOME` at the agent's home; `bash -l`
  still sources the agent's login profiles (so a `PATH` export there is honoured).
- **Pin the parent process's cwd to `/`.** The operator's shell cwd is typically
  inside the operator's home, which the agent can't read; if the `sudo` child inherits
  it, bash emits `getcwd: Permission denied` noise. `jentic run` sets the child's
  dir to `/` for every agent-user invocation.

## `jentic reset` — tear down an agent

Onboarding (`bootstrap`/`wizard`) and `jentic run` accumulate real system state on
the operator's machine: a Unix account and its home, a copied/installed agent binary,
seeded config and provider credentials, named-user ACLs stamped across the
operator's home (traverse grants + leaf grants), a `sudoers` drop-in, and the
`local_agents` entry in the operator's `jentic` config. `jentic reset` removes
that state — the inverse of setup — so an operator can cleanly decommission an
agent (or start over) without hand-reversing each `chmod`/`sysadminctl`.

### It requires sudo to complete — but you don't launch it with `sudo`

```bash
jentic reset [<agent>] [--delete-home] [--force]
```

Deleting a user account, removing another user's home, and stripping ACLs all
require root — but `jentic reset` is **run as the operator, not as
`sudo jentic reset`**. This mirrors the sudo-last posture of account *creation*:
the command reads the operator's own `jentic` config and does all its non-privileged
work (survey, confirmation) as the operator, and only the individual teardown
**steps** are privileged — each is a `sudo`-fronted command (`sudo chmod -a…`,
`sudo sysadminctl -deleteUser`, …). So reset **requires sudo to complete** and
you'll be prompted for your password when it reaches those steps, but it never runs
its config-reading or its prompts as root. Up front it prints a
*"Removing an agent's account and ACLs is privileged (requires sudo)"* notice — the
same `(requires sudo)` signal the account-creation gate shows — so the later
password prompt is never a surprise.

Running as the operator (rather than under `sudo`) also removes a whole class of
bugs: there is no `SUDO_USER` indirection to unwind and no risk of reading root's
`~/.jentic` instead of the operator's — the current user simply *is* the operator.

With no `<agent>` argument it targets every configured local agent; with one, just
that agent.

### It shows the full plan before touching anything

`reset` is destructive and largely irreversible (a deleted account doesn't come
back, and with `--delete-home` neither does the agent's work), so it runs in two
distinct phases: **survey, then confirm, then act**. Nothing is changed during the
survey. It prints exactly what it will do, resolved to concrete paths and the
specific ACL entries it will drop, and states plainly whether the home is being
preserved or deleted:

```
⚠  DANGER ZONE — jentic reset will PERMANENTLY remove the following for agent 'claude'
   (user alice-local-agent). This cannot be undone.

  Directory ACLs to remove (agent access granted by jentic run):
    - leaf grant   user:alice-local-agent  /Users/alice/projects/api
    - leaf grant   user:alice-local-agent  /Users/Shared/alice-local-agent/work
    - traverse     user:alice-local-agent  /Users/alice        (execute-only)
    - traverse     user:alice-local-agent  /Users/alice/projects

  Files & config to delete:
    - sudoers drop-in        /etc/sudoers.d/jentic-agent
    - local_agents['claude'] entry in ~/.jentic/config.yaml

  Account to delete:
    - Unix user  alice-local-agent  (uid 502)

  Agent home (asked about separately after you confirm below):
    - agent home directory   /Users/Shared/alice-local-agent
      Contains the agent's real work + seeded config (~/.aws, ~/.claude).
      Default: KEPT on disk and re-owned to you (alice). You'll be asked
      whether to delete it after confirming the rest.

  NOT touched:
    - your own home's permissions — reset only drops the agent's named-user ACLs
    - your own files, config, keys, and Jentic One itself

Type the agent name ('claude') to confirm, or anything else to abort: claude

  The agent's home /Users/Shared/alice-local-agent will be KEPT and re-owned to
  you. To PERMANENTLY DELETE it and everything in it instead, type 'delete home'
  (anything else keeps it):
```

Design requirements baked into that plan:

- **Everything is listed before the confirm**, resolved from two sources: the
  `local_agents` config entry (user, home, granted dirs) *and* a live re-scan of
  the on-disk ACLs, so grants that drifted from the config are still caught and
  shown. If the two disagree, `reset` shows both and flags the drift.
- **A "danger zone"-style banner** headlines the irreversible nature, and
  confirmation is a **typed agent name**, not a keypress — the same bar as a
  dangerous directory grant. `--yes` does **not** skip it (there is no safe default
  for destruction); a separate explicit `--force` is the only non-interactive
  escape hatch, intended for scripted teardown.
- **The agent home is preserved by default and needs a separate, explicit
  acceptance to delete.** The home holds the agent's real work (edited repos,
  history, seeded config), so removing it is a distinct destructive act from
  tearing down the *plumbing* (account, ACLs, sudoers, config entry). By default
  `reset` **keeps the home directory on disk and re-owns it to the operator**
  (`chown -R <operator>`) so nothing is lost and the operator can still read it
  after the agent account is gone. The gate for deleting it is a **second, separate
  confirmation asked during the run** — after the main plan is confirmed, `reset`
  prompts specifically about the home (naming its path and that it holds the agent's
  work) with **preserve as the default**; deleting requires a distinct typed
  acknowledgement there. It is **not** gated behind a `--delete-home` flag — asking
  the operator to re-run with a flag would be worse, since after a preserve run they
  may no longer know where the home lives. The flag exists **only** as the
  non-interactive escape hatch: `--delete-home` (paired with `--force`) is how a
  scripted teardown opts into deletion when there's no prompt to answer. Without
  that flag and without answering the home prompt affirmatively, the home is shown
  under a **PRESERVED** heading and left on disk.
- **The ancestor traverse ACLs are removed too**, not just the leaf grants — reset
  is a full teardown, so it walks the recorded ancestor chains and drops the
  execute-only entries it added. (Contrast `--revoke`, which intentionally leaves
  traverse grants in place for the next grant.)
- **It never changes the operator home's own permissions** — setup no longer locks
  it, and teardown only drops the agent's named-user ACLs — and never touches the
  operator's own files; the "NOT touched" block states this explicitly so the
  operator can see the blast radius stops at the agent.

### Order of operations

Remove access before removing the account, so a failure part-way never leaves a
live account with dangling grants: (1) drop leaf + traverse ACLs; (1c) remove the
agent's **own** jentic identity dir — its `~/.jentic` (`config_dir`), holding the
registration, cached tokens, and Ed25519 key. This is torn down **even when the
home is kept**, because that directory — not the operator's config — is the
reference-model home of the agent's platform identity; leaving it behind lets a
later `jentic bootstrap` that reuses the same home resurrect a torn-down
(now-archived) registration. (When the home is being deleted, step (2)'s recursive
`rm` already covers it, so this step is skipped.) (2) settle the
agent home — **re-own it to the operator** by default, or delete it *only* when the
separate home confirmation was answered affirmatively (or `--delete-home --force`
in non-interactive use) — this home confirmation is asked **per agent in both
flows**, the named `jentic reset <agent>` *and* the full clean slate, so a bare
`jentic reset` still preserves each home unless you explicitly opt into deleting it;
(3) remove the `sudoers` drop-in; (4) delete the Unix
account — on macOS by deleting the DirectoryService record with `dscl . -delete
/Users/<user>` (which has no filesystem side-effect), on Linux with `userdel`
**without** `-r` — both leave the home in place, so the account goes but the
already-settled home stays; (5) remove the `local_agents` entry from the operator's
config last, so a re-run after a mid-way failure still has the record of what to
finish cleaning. Each step reports success/failure; a failure stops the run with
what's already been done and what remains. Note the account-deletion step (4) must
not remove the home — the obvious macOS tool, `sysadminctl -deleteUser`, deletes it
unless passed `-keepHome`, but that flag is rejected at runtime on recent macOS, so
`dscl . -delete` is used instead; it removes only the account record and preserves
the already-settled home.

Step (2) is **best-effort**, unlike the others. A macOS home materialised by
`createhomedir` carries SIP/TCC-protected template entries (`Library/Mail`,
`Library/Containers`, `ContainerManager`, …) that *nobody* — not even root — can
`chown` or remove. The re-own runs `chown -Rf` (re-owns everything it can, quietly
skipping those) and the delete likewise can't remove them, so both legitimately
exit non-zero after handling the agent's actual work. reset reports that and
continues to the account deletion rather than aborting — otherwise those
unavoidable files would strand the teardown exactly the way the `-keepHome` bug
did.

### Scope follows the argument — named agent vs. full clean slate

Reset's blast radius is decided by whether an agent is named, not by a flag:

- **`jentic reset <agent>`** tears down just that agent and removes **only that
  agent's links** from the operator's config — the `local_agents.<agent>` entry
  (and, for a self-user agent, that entry's `config_dir`/`home_dir` reference). It
  never touches the operator's own identity or any other agent.
- **`jentic reset`** (no agent) is a full clean slate: it tears down **every**
  configured local agent and then also wipes the operator's **own** jentic CLI
  state, so "start over" genuinely returns the machine to zero. Every profile under
  `~/.jentic/profiles` (each profile's Ed25519 key, cached tokens, and registration
  metadata) is removed, and `default_profile` in `config.yaml` is cleared. The wipe
  is purely local — deleting the key and tokens already severs this machine's
  access, so `reset` does **not** attempt to revoke tokens server-side (the cached
  tokens are typically expired, so a revoke call would only add `http 401` noise).

This is intuitive — resetting one agent cleans up that agent; resetting everything
cleans up everything, including yourself — and needs no extra flag. Two properties
keep the config wipe safe:

- **Scoped to the invoking account.** Because `reset` runs *as the operator* (never
  `sudo jentic reset`), the config wipe can only touch the account's own `~/.jentic`
  — it can never reach across into another user's config. This is why the
  responsibility lives here at all: the command already runs as exactly the user
  whose config is being cleared.
- **One whole-slate confirmation.** A full reset previews **everything up front** —
  every agent's teardown plan *and* the operator's own config wipe (its danger-zone
  plan lists exactly which profiles will be deleted) — then takes a **single** typed
  **`reset`** acknowledgement to proceed. It deliberately does not ask the operator
  to type each agent's name in turn and then a separate config confirmation: having
  seen the complete blast radius once, one confirmation covers it. (The named
  `jentic reset <agent>` flow is unchanged — it still confirms with the typed agent
  name.) `--force` skips the prompt for scripted use; without a TTY and without
  `--force` it refuses. A bare `jentic reset` with nothing to remove — no agents and
  no config — is a friendly no-op.

The config wipe runs **last**, after every agent is torn down, so a failure
mid-agent never removes the config that records what still needs cleaning — and a
bare `jentic reset` with no configured agents is a valid config-only clean slate.

#### What a full reset deliberately leaves behind

"Clean slate" means agent + identity, not bare metal. Four things are left intact
by design:

- **Skills** — the generated skill files (see [below](#not-yet-implemented--skill-cleanup)).
- **The operator home's permissions** — setup never locks them and reset never
  changes them; teardown only drops the agent's named-user ACLs.
- **Agent homes** — preserved and re-owned to the operator by default (the agent's
  work survives); deleting a home is the separate, explicit per-agent confirmation,
  offered in the full clean slate too, not only the named flow. The agent's *identity*
  inside that home (`~/.jentic`) is **not** left behind, though — it is torn down
  regardless of the home disposition (see step (1c) above) so a re-bootstrap starts
  genuinely fresh.
- **The rest of `config.yaml`** — the wipe clears `default_profile` and empties
  `local_agents`, but leaves other settings (`base_url`, `broker`, telemetry
  consent) and the file itself in place. It resets your *identity and agents*, not
  every preference.

### Not yet implemented — skill cleanup

One further teardown responsibility belongs to `jentic reset` by design but is
**not implemented yet**: removing the generated skill files. `bootstrap`/`wizard`
write the Jentic CLI-usage skill into each operator's native layout; a full
decommission should delete the managed skill block/files it added for the agent, the
inverse of the skill step, just as reset already inverts the account/ACL/sudoers/
config steps. Until this lands, operators must remove skill files by hand (or re-run
`jentic skill` tooling). The **"NOT touched"** block above remains accurate on this
one point: reset does not currently remove skill files.

## GUI IDEs (Cursor / VS Code)

Don't launch the IDE *GUI* as a different macOS user in the operator's login
session — WindowServer, the launchd Aqua domain, and TCC/Keychain are all tied to
the console user, so it's unsupported and the tricks that make it "sort of" render
are the same ones that break the isolation.

The supported path uses the IDE's built-in **client/server split**: the agent's
file reads, terminal commands, and tool calls run in the extension host / server —
i.e. as the user the server runs as. So run the **UI as the operator** and
**Remote-SSH into `agent@localhost`**, which bootstraps the server + extension
host + integrated terminal as the agent user. Single window, side-by-side, with
the privilege boundary exactly where we want it. (On Linux the same split works;
X11 can also render `sudo -u agent … cursor` into the operator's desktop directly.)

But the simplest and strongest story is still **headless: no GUI at all** — run
the agent under the agent user via `jentic run` (Claude Code, `cursor-agent -p`,
…). The IDE-remote path is the accommodation for operators who insist on the GUI.

## Where this belongs — `jentic`, not `jenticctl`

Account creation, provisioning, grants, launch, and teardown are one
responsibility and live in **`jentic`** — the client-side package guaranteed to
run in the same environment as the agent (the operator's machine, as the
operator's user). `jenticctl` administers a Jentic One deployment and may run on a
different host; it must not own agent-user lifecycle. A `jentic doctor` check
should warn when the agent appears to run as the *same* uid as the operator (the
tripwire) and reconcile the on-disk ACLs against the recorded grants.
