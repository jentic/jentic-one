# Design: run the agent as its own Unix user, wrapped by `jentic`

> Builds on the [threat model](threat-model.md). The structural fix is: run a CLI coding
> agent (Claude Code is the worked example) under a **dedicated, unprivileged OS
> user** distinct from the operator's login user, so it cannot read the
> operator's jentic keyset (`~/.config/jentic`, `~/.local/state/jentic`, legacy
> `~/.jentic`), DB, browser profile, or Keychain. It reaches
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
# 1. One-time: onboard. Right after picking your operator, setup/wizard offers
#    to create the dedicated agent user + its home (jentic run then confines each
#    session so the agent sees only the directories you grant).
jentic setup                             # (or: jenticctl wizard — same flow)

# 2. Every session: launch the agent, isolated, in the directory you name.
jentic run claude ~/projects/my-app

# 3. When you're done with an agent entirely: tear down what was created locally.
#    Run it as yourself — reset prompts for your password when it hits the
#    privileged steps (requires sudo to complete). NOT `sudo jentic reset`.
jentic reset
```

The agent-user account is **created as part of onboarding** — there is no
separate `agent-user setup` command. After you choose an operator (Claude,
Codex, Cursor, or Hermes), `jentic setup` and `jenticctl wizard` both offer to
isolate it behind a dedicated Unix user; the account-creation step is shared
between the two the same way the skill step is (see below). Everything else —
provisioning the agent's binary, seeding config, granting a working directory,
launching in a fresh login shell — is handled inside `jentic run` as guided
prompts. Management
shortcuts:

```bash
jentic run claude --list-grants          # show every directory the agent can reach
jentic run claude --grant   ~/work/api   # grant a directory, then exit
jentic run claude --revoke  ~/work/api   # remove a grant, then exit
```

Any command that prints the directory tree ends with a one-line reminder of how
to revoke a grant. (The V1 `jentic profile view` access map was removed with the
`profile` surface in the V2 activation; `--list-grants` is how the agent's
filesystem reach is inspected, and `jentic access whoami` covers the API side of
"what can I do?".)

### Forwarding arguments to the agent

Arguments after a `--` separator are forwarded **verbatim** to the agent binary,
following the usual CLI convention (`cargo run --`, `kubectl exec --`). Two forms
are supported:

```bash
# Trailing `--`: jentic's own args (agent, optional path) come first.
jentic run claude -- --model opus -p "review this"   # runs: claude --model opus -p "review this"
jentic run claude ~/work/api -- --resume             # forwards --resume, working dir ~/work/api

# Leading `--`: the whole agent command follows the separator.
jentic run -- claude --resumeSessionId=1234          # runs: claude --resumeSessionId=1234
```

In the **trailing** form, args before `--` are jentic's own (the agent id and an
optional working-directory path) and args after it are the agent's argv. In the
**leading** form, the first token after `--` is the agent id and everything else
is forwarded; there is no path argument (the working directory defaults to the
current one) — use the trailing form when you need to pass a path. The leading
form's advantage is that *nothing* after `--` is parsed by jentic's flag parser,
so an agent flag such as `--resumeSessionId` can never collide with a jentic flag
or need escaping.

The split is driven by cobra's `ArgsLenAtDash()` (the arg count before `--`: `> 0`
for the trailing form, `0` for the leading one), so a forwarded flag can never be
mistaken for a jentic flag or misread as the path positional. Each forwarded
argument is shell-quoted independently before it is embedded in the `sudo -u
<agent> … bash -lc` launch snippet, so spaces, globs, and quotes reach the agent
as single literal tokens; on Linux the confinement wrapper (`bwrap`) also ends its
own options with `--` before the binary so a forwarded `--flag` is passed through
rather than parsed by bwrap. The arguments run inside the same confined session as
an interactive launch — forwarding argv does not widen the sandbox.

## Why this posture works

If the agent runs as `<operator>-local-agent` and the operator is their normal
login user, then by Unix ownership (plus macOS TCC for protected data) the agent
user cannot read the operator's jentic keyset (`~/.config/jentic`,
`~/.local/state/jentic`, legacy `~/.jentic`), DB files, browser profile, or
Keychain. That closes AP-1–AP-4 in one move — the agent can only reach Jentic One
over loopback with its own scoped token.

It's weaker than a container (same kernel, same machine — a
local-privilege-escalation bug crosses it) but needs no container runtime and
leaves the connection model unchanged. The pragmatic middle rung.

The isolation is **one-directional and that asymmetry is the whole point**:
sharing the *agent's* home outward to the operator is safe (the operator is
trusted and will want to read the agent's outputs); the thing we never do is the
reverse — expose the operator's home/keys/browser to the agent.

### What this model does *not* protect — residual risks

The Unix-user boundary hardens the agent against the *operator's* environment. It
deliberately does **not** partition the agent account against itself. Two limits
are worth calling out plainly, because operators must not assume protections that
aren't there:

1. **No enforced boundary *between* agent identities.** There is one shared agent
   Unix user, and every identity exported to the account lives in that one
   account's home. Any agent launched through `jentic run` — under any context —
   runs as that same uid and can therefore read **every** identity in the shared
   home (all keys, tokens, and registrations), not just the one it was launched
   with. Contexts/identities are an *identity-selection* convenience, **not** a
   security compartment. A hard
   boundary between identities (e.g. a Unix user per identity, or per-identity
   confinement of the account's store) is a possible future addition; today it
   does not exist.

2. **Secrets in the agent's environment are not secured.** Anything in the agent
   session's environment or config — including LLM-provider API keys and any other
   credentials seeded into or exported within the agent home — is fully readable by
   the agent it is handed to, and by anything else running as the agent user. The
   Unix boundary keeps those secrets away from *other* login users' agents, but it
   does nothing to protect them *from the agent itself*. This is why we recommend
   keeping real credentials out of the agent environment and fronting the model
   with a **private LLM proxy and a virtual key**: the key the agent holds is then
   non-sensitive (revocable, scoped, worthless if exfiltrated), so a leaked agent
   environment costs you a rotation, not a real provider credential. Treat any
   secret that reaches the agent env as disclosed to the agent, and prefer virtual
   keys wherever a surface accepts them.

   The **jentic-one agent token is itself a virtual key** in this sense: for a
   local deployment it is minted against, and only meaningful to, your own
   control-plane on the local network — it authenticates the agent to *your*
   broker, carries no third-party provider credential, and is worthless off that
   network. So while it does sit in the agent home like any other credential, its
   presence there is not a provider-secret exposure; the guidance above is about
   the upstream provider keys you should keep *out* of the agent env by putting a
   virtual key in front of them.

3. **Network egress is not restricted.** This feature confines the agent's *view of
   the filesystem*; it does **not** firewall the agent's network access. An agent
   running under `jentic run` can make arbitrary outbound network connections, the
   same as any process running as that Unix user. Restricting egress was explicitly
   **out of scope** for this work — the confinement profile is a filesystem
   boundary, not an egress jail (see [`sandbox-confinement-design.md`](sandbox-confinement-design.md),
   "Why not `(deny default)`"). Operators who need to constrain where the agent can
   reach on the network must do so with a separate mechanism (host firewall, a
   proxy the agent is forced through, network namespaces/VPN egress rules); jentic
   does not install or manage one. This is a documented residual, not an oversight:
   combined with residual (2), assume an agent that acquires a secret can also send
   it somewhere — which is again why real provider credentials should never reach
   the agent env in the first place.

4. **The sandbox is not a full container.** The per-session confinement is scoped
   to the one thing it uniquely can do — per-entry access control *inside* the
   operator's home — and deliberately does not lock down process execution
   (`process-exec*`), IPC/`mach-lookup`, or the agent's ability to interact with its
   own process tree. The agent is **not** being fully containerised: the goal is to
   stop it acting *as* the human operator (reading their keys, browser session, the
   jentic-one credential store) and to close the sibling-traversal leak — not to
   sandbox it from itself or from the OS services any process of that uid may reach.
   Because the agent runs as its **own** unprivileged Unix user, it cannot signal or
   kill the operator's processes (different uid); what it *can* do within its own uid
   (spawn helpers, talk to system services) is intentionally left open so that
   Node-based agents like `claude` launch and run reliably. See
   [`sandbox-confinement-design.md`](sandbox-confinement-design.md) for why a stricter `(deny
   default)` profile was rejected as too brittle.

None of these undoes the core hardening: running the agent as a **separate Unix
user** still closes the operator-facing attack paths (AP-1–AP-4) above. These notes
scope what the boundary is *for* — protecting the operator and other users from the
agent — versus what it is *not*: an intra-agent, network-egress, or secret-hiding
sandbox.

## Creating the account — folded into `setup` / `wizard`

Account creation is not a standalone command. Right after the operator picks
their agent, `jentic setup` and `jenticctl wizard` share one step (the same
way they share the skill step: the wizard delegates to setup, so the step
lives in the setup flow and both reach it) that offers to isolate the agent
behind a dedicated Unix user. There is exactly **one** such account per operator,
shared across every agent binary and identity; the step is deliberately
**sudo-last**: it asks *"Create a dedicated user account for your local agent?
(requires sudo)"* **before any privileged command runs**, so an operator who
declines never triggers a password prompt and no half-provisioned state is left
behind. The choice is recorded either way (`agent_account.account_created`),
because "declined, running same-user" shapes later behaviour.

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
and the identity summary is shown, `setup` then **offers to start a session in
the agent's home right there** — displaying `cd <agent home>; jentic run <agent>`
and, on yes, launching it; declining leaves the command printed for later. For a
single-user machine that recipe is:

```bash
# macOS
AGENT="$(whoami)-local-agent"; AGENT_HOME_DIR="/Users/Shared/$AGENT"
# The full name embeds the account name: macOS refuses duplicate full names (and
# sysadminctl exits 0 on that refusal), so a per-operator constant would break
# every second agent account.
sudo sysadminctl -addUser "$AGENT" -fullName "$AGENT (jentic agent of $(whoami))" -home "$AGENT_HOME_DIR" -password -
sudo createhomedir -c -u "$AGENT"                                  # sysadminctl only *records* the home; this creates it
id -u "$AGENT" >/dev/null                                          # verify the add actually took — do not trust sysadminctl's exit code
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

#### Prerequisites — checked before any privileged step

Because per-session confinement (below) is the actual isolation guarantee, an
account that can never be launched under confinement would be a dead end. So the
moment the operator opts into isolation — **before** the first `sudo` runs —
`setup`/`wizard` checks that this machine has what the model needs and,
crucially, treats a missing prerequisite as *non-blocking for everything else*:

| Platform | Prerequisites |
| --- | --- |
| macOS  | `sandbox-exec` (ships with macOS) |
| Linux  | `bubblewrap` (`bwrap`), unprivileged user namespaces, and the `acl` package (`setfacl`/`getfacl`, which back the directory grants) |

If something is missing, the account-creation path **stops there** and prints the
exact install command for the detected package manager — for example, on
Debian/Ubuntu:

```bash
sudo apt install bubblewrap acl
```

(`dnf`/`yum`/`pacman`/`zypper` are detected and named accordingly; unprivileged
user namespaces, if disabled, are re-enabled with
`sudo sysctl -w kernel.unprivileged_userns_clone=1`, persisted under
`/etc/sysctl.d/`). We **never run a package manager ourselves** — the command is
printed, not executed. The operator then has two clearly-offered choices:

1. **Continue same-user now** — setup proceeds without an isolated account
   (recorded as `account_created=false`, exactly like declining isolation), so a
   missing dependency never blocks the identity/skill provisioning they came for.
2. **Install the prerequisites and re-run** — the printed command, then
   `jentic setup` (or `jentic run <agent>`) again to isolate the agent.

The same check is the launch-time gate in `jentic run`: `launchAgent` refuses to
start an unconfined session and prints the same per-dependency breakdown, so the
setup-time and launch-time gates read from one source
(`localagent.AgentUserPrereqs`) and can never disagree about what this machine can
do.

Three things worth calling out, because they're the load-bearing parts:

- **Per-session process confinement is the isolation guarantee.** `jentic run`
  launches the agent under a confinement profile (sandbox-exec on macOS, bwrap on
  Linux) that denies the operator's home except the granted subpaths, and **errors
  closed** if confinement is unavailable. This replaces the earlier blanket `chmod
  700 ~`: it closes the same read path *and* the sibling-traversal leak a whole-home
  700 could not, without changing the operator home's own permissions. Real secrets
  keep their own `0700` modes regardless. See
  [`sandbox-confinement-design.md`](sandbox-confinement-design.md) and the
  [filesystem-access model](filesystem-access-model.md).
- **The agent's home is the shared space.** It lives under an existing shared
  parent (`/Users/Shared/…`, `/opt/…`), owned by the agent, with an *inherited*
  ACL granting the single operator read/write. The `*_inherit` flags mean the
  grant applies to everything the agent creates later, so the operator keeps full
  access without re-running anything. No group, no world access.
- **Per-user auth is expected, not a bug.** The agent gets its own `$HOME`, so its
  own Claude config/history (`~/.claude`, `~/.claude.json`), its own login
  Keychain, and its own `jentic` identity (the per-launch context export under its
  own `~/.config/jentic` / `~/.local/state/jentic`). The
  agent authenticates its own tools once, as itself — it can't ride the operator's
  sessions, which is the boundary working as designed.

### Where the platform identity is written — registration after the user decision

The agent-user question is asked **before** the platform registration (DCR) so that
by the time we mint and persist a jentic identity we already know *which store it
belongs in*. Registration is deliberately sequenced after the isolation decision,
not before it.

The consequence is a small but important targeting rule for **every** command that
writes an agent identity (setup today; any create/update later):

- **Self-user agent (isolation accepted).** The identity minted by setup lives
  in the **operator's own XDG store** (`~/.config/jentic`, `~/.local/state/jentic`)
  like any other. The agent account receives identity material through exactly one
  channel: at every launch, `jentic run` exports the operator's *active context* —
  a minimal config, the env-scoped key, and tokens — into the **agent home's own
  XDG store** (`<agent home>/.config/jentic`, `<agent home>/.local/state/jentic`),
  owned by the agent (see "`jentic run`" below). That per-launch export is a
  refresh, not a second durable copy: the operator's store stays the single source
  of truth, and the agent-side copy is overwritten on the next launch and removed
  by `jentic reset`. The operator's side keeps only the management metadata: the
  single `agent_account:` object (account name, `home_dir`, grant inventory) in the
  operator's agent state at `~/.config/jentic/agent-account.yaml`.
- **Same-user agent (isolation declined).** Nothing changes: the identity lives in
  the operator's own store as usual, and there is nothing to export — the agent
  shares the operator's home and reads the same store.

This "export, not copy" model is what keeps the multi-config problem simple: one
writer, one source of truth, one refreshed projection.

> **Legacy note — `config_dir`.** Older releases wrote the agent's identity into a
> V1-style config root inside the agent's home (`<agent home>/.jentic`) and
> recorded a `config_dir` pointer to it in the operator's config. New accounts no
> longer record `config_dir`; the field is retained read-only so `jentic migrate`
> can rescue profiles from an old agent home and `jentic reset` keeps removing the
> legacy directory on teardown.

> **Future improvement — default agent home under the operator's home.** Today the
> agent home lives under a shared parent (`/Users/Shared/<agent>`, `/opt/<agent>`).
> A nicer default would be `~/jentic-agents/<agent-name>` — discoverable, clearly
> owned by the operator's account tree, and self-documenting. It would require
> granting the agent an **execute-only traverse** on the operator's home (the same
> Layer-1 mechanism [Step 4](#step-4--directory-access-traverse-walk--rwx-leaf--confinement)
> uses for working dirs) so it can reach `~/jentic-agents/<agent>` without *reading*
> anything else in `~`. The tradeoff is a **persistent** execute ACE on `~`, with
> open questions still to resolve. **Deferred; not implemented** — the full
> plan is kept in internal planning notes.

### Optional: passwordless launch

Without this, each `jentic run` prompts for the operator's password (cached
per-terminal ~5 min). `setup`/`wizard` offer this as a **consent gate during
account setup** (defaulting to yes): accept it and the CLI installs a scoped
`sudoers` drop-in; decline and you keep entering your login password on
each launch. The rule it writes is exactly:

```
<operator> ALL=(<operator>-local-agent) NOPASSWD: /bin/bash
```

The `(<operator>-local-agent)` runas target scopes this to *becoming the agent
user* — **not** a general root grant (the command is the login shell every agent
invocation already runs under, `sudo -u <agent> -H bash -lc …`). The drop-in is
written to `/etc/sudoers.d/jentic-agent` through a temp file **validated with
`visudo -cf` before install**, so a malformed edit can never lock you out of
sudo, and the exact line is appended only if absent (idempotent across re-runs
and account reuse). `jentic reset` removes the agent's line from the drop-in
(deleting the file when it becomes empty) as part of teardown. On macOS you can
instead enable Touch ID for sudo (`auth sufficient pam_tid.so` in
`/etc/pam.d/sudo_local`).

### Optional: bringing your workspaces over

A fresh agent account starts with access to nothing but its own home, so the
operator would otherwise have to re-grant each project by hand on first
`jentic run`. To skip that, account setup **reads the workspaces the operator has
already trusted in the agent's own config and offers to grant them in one step**.
This is deliberately **operator-specific**: rather than scanning the home for
marker files (most `AGENTS.md`/`CLAUDE.md` dirs are dependencies, clones, or
templates the operator never actually opened with the agent), it reads the agent's
own record of where the operator genuinely worked. For Claude Code that is
`~/.claude.json`, whose top-level `projects` map is keyed by absolute directory
path; we take only the entries with `hasTrustDialogAccepted: true` — the
directories the operator explicitly answered "yes, I trust this folder" for.
Nested trusted workspaces are **collapsed to their top-level ancestor**: the leaf
grant is recursive + inherited, so granting a parent already covers every trusted
descendant — offering the child too would be a no-op that only clutters the picker.
The trusted set is shown as a **pre-selected multiselect** the operator can trim
before confirming. Each chosen workspace is granted through the same scoped-ACL
path (traverse-walk + rwx-leaf, see [Step 4](#step-4--directory-access-traverse-walk--rwx-leaf--confinement))
an in-launch grant uses, and is recorded so it persists across sessions.

**The strict permission model always takes precedence.** Every candidate is run
through `Classify` and any banned one is dropped — the two ban classes from the
[filesystem-access model](filesystem-access-model.md#non-negotiable-boundaries):
`HardBan` subtrees (sensitive dotfile dirs like `~/.ssh`, `~/.aws`, `~/.jentic`;
system trees) and any `SoftBan` root (a home root). Paths no longer on disk are
dropped too. A workspace that conflicts with the permission model is therefore
**never surfaced**, and as a belt-and-braces guard each selection is re-classified
at grant time and a banned one is skipped with a note — so a conflicting directory
can never be granted even if the trusted list and classification ever drifted. This
is the same precedence the whole grant flow follows: the boundaries cancel out
anything an offer would otherwise propose.

> **Applies to every selected agent binary (forward-looking).** Agent selection is
> **single-choice today**, so this runs for the one binary picked (e.g.
> `claude`), reading **its** trusted-projects list. Selection will become
> **multi-choice** — the operator will be able to pick several agent binaries
> (`claude`, `hermes`, …) in one setup run. When it does, this workspace offer must
> run for **each** selected binary. Unlike a home-wide marker scan, the
> trusted-projects source is **per-binary** (Claude Code's `~/.claude.json` here;
> another agent would have its own), so each binary reads **its own** trusted list
> — `TrustedWorkspaces` already dispatches on the agent descriptor. Wire the
> multi-binary loop so each selected binary's own trusted set is offered; because
> there is a single shared account, all the resulting grants land as one
> consolidated `granted_dirs` list on that one account (one uid, one ACL set).

## `jentic run <agent>` — the daily driver

```
jentic run <agent> [path] [flags] [-- <agent-args>...]
jentic run -- <agent> [agent-args...]
```

- **`<agent>`** — a known **runnable** coding-agent identifier (`claude`, `codex`,
  `cursor`, `hermes`), selecting only the **binary/descriptor** (binary name, how
  to detect it, how to install/copy it). It does **not** select an account or an
  identity: there is one shared agent account, and identity comes from the
  operator's active context (see below). `generic` is a **skill-only** operator — it
  names an `AGENTS.md` placement target for `jentic skill`, has no binary, and is
  rejected by `jentic run` with a message that says so.
- **`[path]`** — working directory for the session; defaults to the current dir.
- **Flags** (all optional — the command is fully interactive without them):
  `--home`, `--allow-dir`/`--no-allow-dir`, `--seed-config`/`--no-seed-config`,
  `--yes` (assume the safe default; never picks a flagged-dangerous option),
  `--agent-user <name>`, and the `--list-grants`/`--grant`/`--revoke` shortcuts.
- **`-- <agent-args>...`** — arguments forwarded verbatim to the agent binary. See
  [Forwarding arguments to the agent](#forwarding-arguments-to-the-agent) for the
  trailing (`jentic run claude -- …`) and leading (`jentic run -- claude …`) forms.

State — whether the shared agent account exists, its user/home, and its
consolidated directory grants — lives in the operator's own **agent state** at
`~/.config/jentic/agent-account.yaml`, under
the single `agent_account:` object. There is exactly one account per operator, so
this is one object, not a per-agent map; the grants are one `granted_dirs` list
because they are one uid's ACLs regardless of which binary made them. Nothing
secret: paths and names, not keys. Reading/writing it is a lock-guarded
load-mutate-save of that file (with a read-only fallback to a record left in the
legacy `~/.jentic/config.yaml` by older releases, adopted on the first write), so
`jentic run` never has to re-derive or re-prompt for the account's details.

**Identity comes from the operator's active context, not the `<agent>` argument.**
Before every launch, `jentic run` exports the active context into the agent
home's own XDG store (`<agent home>/.config/jentic` + `.local/state/jentic`): a
minimal config holding exactly one environment/identity/context — mode forced to
`agent` so command fencing holds inside the session — plus the env-scoped key
and token state (0600, fsynced), chowned to the agent. The `jentic` the agent
runs resolves that store from disk like any other user, so it acts as the
operator's current identity with no flags and no environment-variable injection;
switching contexts as the operator re-points the next launch automatically, with
no drifting second copy. (This replaces the V1 model of profile check-out plus a
`JENTIC_PROFILE` variable injected into the session.)

What it does, in order:

1. **Resolve the agent user** from config (or the `<operator>-local-agent`
   default); if the account doesn't exist, offer to create it — the same account
   step `setup`/`wizard` run, for operators who skipped it during onboarding.
2. **Ensure the binary is installed** for that user (below).
3. **Optionally seed config** — the agent's own settings, then the LLM-provider
   config (below).
4. **Resolve the working directory and its access** (below).
5. **Launch** as the agent user, in a login shell, in the resolved directory,
   forwarding any `--`-separated arguments verbatim to the agent binary (above).

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

> **The shared tool dirs are mounted read-only.** These executable routes
> (`/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, …) are marked read-only in
> the confinement profile (SBPL `(deny file-write* …)` / bwrap `--ro-bind`) as a
> **non-negotiable default** — read and execute stay, writes are denied. Without it
> a compromised agent could overwrite a binary the operator's next `jentic run`
> executes and strip its own sandbox. See
> [Non-negotiable boundaries](filesystem-access-model.md#non-negotiable-boundaries).

### Step 3 — config seeding (opt-in, once, never clobbers)

Provisioning gives a runnable tool but not the operator's *settings*. After the
binary step `jentic run` offers, once and opt-in, to copy the agent's config into
the agent's home — for Claude that is `~/.claude` + `~/.claude.json`, for Codex
`~/.codex`, for Cursor `~/.cursor`, for Hermes `~/.hermes` (each descriptor's
`ConfigPaths`). Guards: it only runs when the agent has no config of its own yet
(a re-run never overwrites the agent's evolved settings), it's off by default, and
`--yes` declines.

**Per-operator secret scrubbing.** Some operators keep their provider API key in a
*discrete* credential file inside that config tree — Codex's `~/.codex/auth.json`,
Hermes's `~/.hermes/.env`. Unlike Claude, where the key is embedded in the very
config the agent needs, these are separable, so after seeding `jentic run` deletes
them from the agent's home (each descriptor's `SecretConfigPaths`, scrubbed with a
non-recursive `rm -f` on the exact home-constrained path). The agent inherits the
operator's *settings* but authenticates as itself — the operator's raw key never
comes to rest in the agent account.

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

Before offering **Allow**, `jentic run` classifies the target into two ban
classes, and **a banned target gets no "grant anyway" option at all**: the prompt
offers only *Open in agent's home* / *Cancel*, `--allow-dir` hard-refuses, and
`--yes` takes the safe default. A **soft ban** (the operator's home root, another
user's home) blocks granting *that directory* but still lets you grant a
subdirectory beneath it; a **hard-subtree ban** (sensitive dotfile dirs — `~/.ssh`,
`~/.jentic`, `~/.aws`, …, Keychain, browser profiles — and system trees like
`/etc`, `/usr`, `/`) blocks the path *and every descendant*. Revoke drops the leaf
allow (ancestor traverse stays); a full `jentic reset` walks the ancestor chain and
drops those too. Grants **persist across sessions** by design, so `--list-grants`
exists to keep access from quietly sprawling.

> The exact ACL commands, the macOS `write`-shorthand gotcha (why the permission
> set is spelled out in full), the recursive-over-existing-contents consequence,
> the world-readable-ancestor residual, and the full grant/revoke/reset lifecycle
> are documented in [**`filesystem-access-model.md`**](filesystem-access-model.md).

### Step 5 — launch

```bash
sudo -u "$AGENT" -H bash -lc 'cd "$DIR" && exec <binary> <forwarded-args...>'
```

`<binary>` is wrapped in the platform confinement mechanism (`sandbox-exec` on
macOS, `bwrap` on Linux); any `--`-forwarded arguments are appended after it, each
shell-quoted independently so they survive the `bash -lc` snippet as single
tokens. Two further details from live testing, both baked into `jentic run`:

- **`-H bash -lc`, not `sudo -i`.** `sudo -i` re-serializes the command through the
  login shell and mangles multi-token snippets; plain `sudo -u … -H bash -lc`
  passes argv straight through. `-H` points `HOME` at the agent's home; `bash -l`
  still sources the agent's login profiles (so a `PATH` export there is honoured).
- **Pin the parent process's cwd to `/`.** The operator's shell cwd is typically
  inside the operator's home, which the agent can't read; if the `sudo` child inherits
  it, bash emits `getcwd: Permission denied` noise. `jentic run` sets the child's
  dir to `/` for every agent-user invocation.

## `jentic reset` — tear down the account and this machine's identity state

Onboarding (`setup`/`wizard`) and `jentic run` accumulate real system state on
the operator's machine: a Unix account and its home, a copied/installed agent binary,
seeded config and provider credentials, named-user ACLs stamped across the
operator's home (traverse grants + leaf grants), a `sudoers` drop-in, the agent's
exported identity in the account's home, and the single `agent_account:` object in
the operator's agent state. `jentic reset` removes that state — the inverse of
setup — so an operator can cleanly decommission the agent account (or start over)
without hand-reversing each `chmod`/`sysadminctl`. It takes **no argument**: a
`jentic reset` tears the whole account down and wipes this machine's jentic
identity state. To remove a single context or identity instead, use
`jentic context delete` / `jentic identity delete` — finer-grained removal is
not reset's job.

### It requires sudo to complete — but you don't launch it with `sudo`

```bash
jentic reset [--delete-home] [--force]
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
config trees instead of the operator's — the current user *is* the operator.

It tears down the whole shared agent account (and wipes the operator's own
identity state — the full clean slate).

### It shows the full plan before touching anything

`reset` is destructive and largely irreversible (a deleted account doesn't come
back, and with `--delete-home` neither does the agent's work), so it runs in two
distinct phases: **survey, then confirm, then act**. Nothing is changed during the
survey. It prints exactly what it will do, resolved to concrete paths and the
specific ACL entries it will drop, and states plainly whether the home is being
preserved or deleted:

```
⚠  DANGER ZONE — jentic reset will PERMANENTLY remove the shared agent account
   (user alice-local-agent) and your own jentic config. This cannot be undone.

  Directory ACLs to remove (agent access granted by jentic run):
    - leaf grant   user:alice-local-agent  /Users/alice/projects/api
    - leaf grant   user:alice-local-agent  /Users/Shared/alice-local-agent/work
    - traverse     user:alice-local-agent  /Users/alice        (execute-only)
    - traverse     user:alice-local-agent  /Users/alice/projects

  Files & config to delete:
    - sudoers drop-in        /etc/sudoers.d/jentic-agent
    - agent_account: object in ~/.config/jentic/agent-account.yaml
    - the agent's identity dirs  /Users/Shared/alice-local-agent/.config/jentic,
      /Users/Shared/alice-local-agent/.local/state/jentic (+ any legacy .jentic)
    - your own identity state under ~/.config/jentic + ~/.local/state/jentic
      (and any legacy V1 profile tree under ~/.jentic)

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

Type 'reset' to confirm, or anything else to abort: reset

  The agent's home /Users/Shared/alice-local-agent will be KEPT and re-owned to
  you. To PERMANENTLY DELETE it and everything in it instead, type 'delete home'
  (anything else keeps it):
```

Design requirements baked into that plan:

- **Everything is listed before the confirm**, resolved from two sources: the
  `agent_account:` state object (user, home, granted dirs) *and* a
  live re-scan of the on-disk ACLs, so grants that drifted from the record are
  still caught and shown. If the two disagree, `reset` shows both and flags the
  drift.
- **A "danger zone"-style banner** headlines the irreversible nature, and
  confirmation is a **typed token**, not a keypress — the same bar as a dangerous
  directory grant. The full clean slate is confirmed by typing **`reset`**.
  `--yes` does **not** skip it (there is no safe default for
  destruction); a separate explicit `--force` is the only non-interactive escape
  hatch, intended for scripted teardown.
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

Remove access before removing the account, so a failure
part-way never leaves a live account with dangling grants: (1) drop leaf +
traverse ACLs; (1c) remove the agent's **own** jentic identity dirs — the
exported XDG store (`<agent home>/.config/jentic` with the Ed25519 key,
`<agent home>/.local/state/jentic` with the cached tokens) plus any legacy
`<agent home>/.jentic`. These are torn down **even when the home is kept**: the
credential material handed to the agent must not survive the teardown in the
soon-to-be-operator-owned home, and leaving a legacy registration behind lets a
later `jentic setup` that reuses the same home resurrect a torn-down
(now-archived) registration. (When the
home is being deleted, step (2)'s recursive `rm` already covers it, so this step is
skipped.) (2) settle the agent home — **re-own it to the operator** by default, or
delete it *only* when the separate home confirmation was answered affirmatively (or
`--delete-home --force` in non-interactive use), so a bare `jentic reset` still
preserves the home unless you explicitly opt into deleting it; (3) remove the
`sudoers` drop-in; (4) delete the Unix account — on macOS by deleting the
DirectoryService record with `dscl . -delete /Users/<user>` (which has no
filesystem side-effect), on Linux with `userdel` **without** `-r` — both leave the
home in place, so the account goes but the already-settled home stays; (5) remove
the `agent_account:` object from the operator's agent state
(`~/.config/jentic/agent-account.yaml`, clearing any legacy copy in
`~/.jentic/config.yaml` too) last, so a re-run after a mid-way failure still has
the record of what to finish
cleaning. Each step reports success/failure; a failure stops the run with
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

### Scope — the full clean slate

`jentic reset` is the full clean slate: it tears down the shared agent account —
the Unix user, the named-user ACLs, the sudoers drop-in, and the agents'
identities in the account's home — and then also wipes the operator's **own**
jentic identity state, so "start over" genuinely returns the machine to zero.
That state is the XDG store (contexts, environments, identities, keys, and
tokens under `~/.config/jentic` and `~/.local/state/jentic`) plus any legacy V1
profile tree under `~/.jentic`. The wipe is purely local — deleting the key and
tokens already severs this machine's access, so `reset` does **not** attempt to
revoke tokens server-side (the cached tokens are typically expired, so a revoke
call would only add `http 401` noise).

Finer-grained removal is deliberately **not** reset's job: `jentic context
delete` / `jentic identity delete` remove a single context or identity and leave
the agent account, its grants, and everything else alone. (The V1
`jentic reset <profile>` single-profile arm was removed with the profile surface
in the V2 activation.) Two properties keep the identity wipe safe:

- **Scoped to the invoking account.** Because `reset` runs *as the operator* (never
  `sudo jentic reset`), the wipe can only touch the account's own store
  — it can never reach across into another user's config. This is why the
  responsibility lives here at all: the command already runs as exactly the user
  whose state is being cleared.
- **One whole-slate confirmation.** A full reset previews **everything up front** —
  the account teardown plan *and* the operator's own identity wipe (its danger-zone
  plan lists exactly what will be deleted) — then takes a **single** typed
  **`reset`** acknowledgement to proceed. Having seen the complete blast radius
  once, one confirmation covers the account and the identity wipe together.
  `--force` skips the prompt for scripted use; without a TTY and
  without `--force` it refuses. A `jentic reset` with nothing to remove — no
  account and no identity state — is a friendly no-op.

The identity wipe runs **last**, after the account is torn down, so a failure
mid-teardown never removes the record of what still needs cleaning — and
a `jentic reset` with no agent account is a valid identity-only clean slate.

#### What a full reset deliberately leaves behind

"Clean slate" means agent + identity, not bare metal. Four things are left intact
by design:

- **Skills** — the generated skill files (see [below](#not-yet-implemented--skill-cleanup)).
- **The operator home's permissions** — setup never locks them and reset never
  changes them; teardown only drops the agent's named-user ACLs.
- **The agent home** — preserved and re-owned to the operator by default (the
  agent's work survives); deleting it is the separate, explicit home confirmation.
  The agent's *identity* inside that home (the exported `~/.config/jentic` +
  `~/.local/state/jentic`, and any legacy `~/.jentic`) is **not** left behind,
  though — it is torn down regardless of the home disposition (see step (1c)
  above) so no credential material outlives the account and a fresh setup starts
  genuinely fresh.
- **The rest of the operator's state** — the wipe removes identity state (the XDG
  store and any legacy `~/.jentic/profiles`) and the `agent_account:` object, but
  leaves other settings (telemetry consent, theme) and the config files themselves
  in place. It resets your *identity and agent account*, not every preference.

### Not yet implemented — skill cleanup

One further teardown responsibility belongs to `jentic reset` by design but is
**not implemented yet**: removing the generated skill files. `setup`/`wizard`
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
