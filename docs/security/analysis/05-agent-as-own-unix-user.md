# Deep-dive: running the agent as its own Unix user

> Expands structural fix **1b** in [`03-mitigations.md`](03-mitigations.md). Focus:
> a **CLI-initiated** coding agent (Claude Code as the worked example) run under a
> dedicated, unprivileged OS user distinct from the operator's login user, on
> macOS (with Linux notes). The goal is to know the real ergonomics and the real
> sharp edges before recommending it. Platform-specific details are as of 2026-07;
> exact permission/group defaults vary between stock macOS, MDM-managed devices,
> and Linux distros, so the recipe below deliberately does not depend on them.

## Why this posture works

If the agent runs as user `agent` and the operator is their normal login user, then
by Unix ownership + macOS TCC the `agent` user cannot read the operator's
`~/.jentic/jentic-one.yaml` (keyset + `jwt_secret`), the operator's DB files, the
operator's browser profile, or the operator's Keychain. That closes **AP-1, AP-2,
AP-3, AP-4** in one move (see [`01`](01-threat-model.md)) — the agent can only
reach Jentic One the way any client does: loopback HTTP + its own scoped token.

It is weaker than a container (**same kernel, same machine** — a
local-privilege-escalation bug crosses it), but it needs no container runtime and
leaves the connection model unchanged. It's the pragmatic middle rung.

## The central question: what happens when you `su` to the agent user mid-session?

Scenario: you're in Terminal as your normal login user, cwd `~/`, and you launch
the agent as the `agent` user. **Which directory does it open in, and can it even
read the one you were standing in?**

### Working directory
- `su agent` (no `-`) keeps most of your environment and **does not change the
  cwd** — the child shell inherits the current working directory. So it "opens in"
  wherever you were (e.g. the operator's home). But inheriting the *path* is not
  the same as being *allowed in* — see the next point.
- `su - agent` (login shell) **resets the environment and `cd`s to the agent
  user's home** (`~agent`). This is the clean choice: predictable cwd, fresh
  `HOME`/`PATH`, no leakage of the operator's env vars (which may include tokens).
- `sudo -u agent -i` behaves like `su -` (login, cd to `~agent`); `sudo -u agent
  <cmd>` runs with the operator's cwd unless you also set it.

**Recommendation: always use a login shell (`su - agent` / `sudo -u agent -i`) so
the agent starts in its *own* home, not the operator's.** Then `cd` explicitly to
the shared workspace.

### The sharp edge: cwd inheritance vs. home-directory permissions
A `su agent` (non-login) that keeps the operator's cwd will land the agent shell
in the operator's home. Whether it can *read* there depends on how the operator's
home is permissioned and on **what group(s) the agent account belongs to** — and
this varies between machines, so a freshly-created account may or may not share a
group with the operator and be able to read more of the operator's home than you'd
expect. **Don't rely on the defaults being safe.** The robust rule is independent of
any specific machine's group layout:

1. **Lock the operator's home to owner-only** (`chmod 700 ~`). This is the one
   machine-independent guarantee: with no group or world bits, *no* other account —
   whatever group it lands in — can traverse or read the operator's home. It needs
   no shared-group bookkeeping and it's the cheapest way to close the read path.
2. **Start the agent with a login shell in its own home** (`su - agent` /
   `sudo -u agent -i`) so its cwd is never the operator's home in the first place.
3. **Don't leave anything sensitive group/other-readable.** Note Jentic One's own
   default writes its config under `~/.jentic/` and the directory is world-listable
   even though the key *file* is `0600`; the clean answer is to not store the key in
   the operator's home at all (keychain / a different owning user — see
   [`03`](03-mitigations.md) fix 2a). Combined with (1), the agent has no path into
   the operator's home regardless of platform defaults.

With those three in place, the cwd question is a non-issue and the read-exposure
question is closed by construction rather than by luck.

## Can the agent user's home live in a shared location the operator can also reach?

Yes — and this is the key ergonomic lever, because the operator will want to read
the agent's outputs, logs, and working files.

- A user's home directory is **just a path in the account record** (`NFSHomeDirectory`
  on macOS; the 6th passwd field on Linux). It does **not** have to be under
  `/Users/<agent>` — you can point it at a neutral path. This doc uses
  `/Users/Shared/<agent-name>` on macOS and `/opt/<agent-name>` on Linux, one home
  per agent under an existing shared parent.
- `/Users/Shared` is the conventional macOS "both users can reach it" location. It
  is world-writable, so the agent's home is created owned by the agent and closed to
  others (the operator gets in by the named ACL below) — don't leave it wide open.

**Do not naively set the agent's home to a world-readable/writable dir** — that
recreates the exposure we're trying to remove (now the *operator's* processes, or
any user, could tamper with the agent's home). The right shape depends on how many
humans need access:

- **One human user (the common case):** don't bother with a group. Leave the agent's
  home owned by the agent, and grant the single operator access by **name** via an
  inherited ACL (`user:<operator> allow …`). No group to create or maintain, and
  still no world access.
- **Several humans:** create a **shared group** (e.g. `agents`) containing the
  operator(s) + the agent user, own the agent home `2750` and the shared workspace
  `2770` (setgid so new files inherit the group), and let group membership be the
  one place you add/remove people.

Either way the **shared workspace** (the repo the agent edits and the operator
reviews) is the dir both can read/write, with an inherited ACL so new files keep
the right perms. Concrete commands for both shapes are in the recipe below. This
gives the shared-workspace ergonomics without exposing either user's private home.

> **Direction of exposure matters.** Sharing the *agent's* home outward to the
> operator is safe (operator is trusted). The thing we must never do is the
> reverse — expose the *operator's* home/keys/browser to the agent. Keep the
> asymmetry.

## Concrete setup — how many commands does the operator actually run?

The point of this section is to answer "is this a weekend project or a
copy-paste?" **For a single-user machine it's four commands, and a one-line launch
each time.** All the account-creation commands need `sudo`; the daily launch does
not.

There are two shapes. **Most machines have exactly one human user**, and that case
is genuinely tiny: create the agent + its home, grant the operator read/write into
that home with a single ACL, then lock the operator's own home. The agent's home
*is* the shared space — the agent works there and the operator can read and edit all
of it. A group is only worth it when *several* humans need access. Pick your OS and run
the block that fits.

> Giving the operator write into the agent's home is deliberate — the operator is
> the trusted party, and this is the access we *want* them to have (review, edit,
> clean up the agent's files). The isolation that matters is one-directional: the
> **agent** still cannot read the operator's home, keys, or browser. That asymmetry
> is intact.

## macOS

### Single human user — the common case

```bash
AGENT="$(whoami)-local-agent"; AGENT_HOME_DIR="/Users/Shared/$AGENT"

# 1. Create a minimal-privilege (standard, non-admin) agent user. sysadminctl only
#    *records* the home path; -password - prompts you to set the account password.
sudo sysadminctl -addUser "$AGENT" -fullName "$(whoami) Local Agent" -home "$AGENT_HOME_DIR" -password -

# 2. Actually create the home directory from the account record.
sudo createhomedir -c -u "$AGENT"

# 3. Let the operator read + write everything in the agent's home, now and for new files.
sudo chmod +a "user:$(whoami) allow read,write,execute,file_inherit,directory_inherit" "$AGENT_HOME_DIR"

# 4. Lock the operator's own home to owner-only, so no other account can read it
#    regardless of what group the agent lands in. (Skip if it is already 700.)
chmod 700 ~
```

The agent is named `<operator>-local-agent` (e.g. `alice-local-agent`), so
the setup scales cleanly to several operators or a second agent on one machine. The
`*_inherit` flags make the operator's grant apply to everything the agent creates
later, so the operator keeps full read/write access without ever re-running
anything. No group, no setgid — step 4 is the whole isolation story and it doesn't
depend on any machine default.

### Optional: passwordless launch

Without this, `sudo -u agent -i …` prompts for the **operator's** password on each
new agent session (cached per-terminal for ~5 min). This rule lets the operator
become the agent without a password prompt. There's no native "add a sudo rule"
subcommand — `visudo` is editor-only by design — but you can point its editor at
`tee` so `visudo` does the validation *and* the atomic, correctly-permissioned
install in one line:

```bash
# $(whoami) is the operator (expanded before the pipe). visudo validates the
# result and only installs it (mode 0440) if it parses — so a typo can never
# lock you out of sudo.
echo "$(whoami) ALL=($(whoami)-local-agent) NOPASSWD: /bin/bash" | sudo SUDO_EDITOR='tee -a' visudo -f /etc/sudoers.d/jentic-agent
```

(If you ever *do* lock yourself out editing sudoers by hand: authenticate from
another admin account, or reboot into macOS Recovery and fix `/etc/sudoers.d/` —
the pattern above avoids that by never installing an invalid file.)

The `(<operator>-local-agent)` target scopes this to *becoming the agent user* — it
is **not** a general root grant. You can instead enable **Touch ID for sudo** (add
`auth sufficient pam_tid.so` to `/etc/pam.d/sudo_local`) so launches ask for a
fingerprint rather than a password.

### Multiple humans — add a shared group

When more than one person needs the agent's output, swap the named-user ACL for a
group so you add/remove people in one place.

```bash
# Shared agent, so it isn't tied to one operator — neutral name 'local-agent'.
sudo sysadminctl -addUser local-agent -fullName "Local Agent" \
  -home /Users/Shared/local-agent -shell /bin/zsh -password -
sudo dseditgroup -o create agents                                  # shared group...
sudo dseditgroup -o edit -a "$(whoami)" -t user agents; sudo dseditgroup -o edit -a local-agent -t user agents
sudo chown -R local-agent:agents /Users/Shared/local-agent && sudo chmod 2750 /Users/Shared/local-agent
sudo mkdir -p /Users/Shared/workspace && sudo chown local-agent:agents /Users/Shared/workspace && sudo chmod 2770 /Users/Shared/workspace
sudo chmod +a "group:agents allow read,write,delete,add_file,add_subdirectory,file_inherit,directory_inherit" /Users/Shared/workspace
```

### Launch (every time)

```bash
# Login shell => starts in the agent's own home, fresh env (no operator tokens leak).
sudo -u "$(whoami)-local-agent" -i bash -lc 'exec claude'
```

The login shell starts in the agent's home, which *is* the shared space in the
single-user setup, so there's nothing to `cd` into. (In the multi-user setup the
user is `local-agent`, and add `cd /Users/Shared/workspace &&`.)

## Linux

### Single human user — the common case

```bash
AGENT="$(whoami)-local-agent"; AGENT_HOME_DIR="/opt/$AGENT"
sudo useradd -m -d "$AGENT_HOME_DIR" -s /bin/bash "$AGENT"
sudo setfacl -R -m u:"$USER":rwX "$AGENT_HOME_DIR" && sudo setfacl -R -d -m u:"$USER":rwX "$AGENT_HOME_DIR"
chmod 700 ~   # lock the operator's own home so no other account can read it
```

The `-d` (default) ACL makes the operator's grant apply to everything the agent
creates later, so the operator keeps full read/write access without re-running
anything; the final `chmod 700 ~` is the machine-independent isolation guarantee.

### Optional: passwordless launch

Same as macOS — a scoped `sudoers` drop-in installed through `visudo`:

```bash
echo "$(whoami) ALL=($(whoami)-local-agent) NOPASSWD: /bin/bash" | sudo SUDO_EDITOR='tee -a' visudo -f /etc/sudoers.d/jentic-agent
```

The `(<operator>-local-agent)` target scopes this to *becoming the agent user*, not
a general root grant. (If you ever lock yourself out editing sudoers by hand, fix
`/etc/sudoers.d/` from a root shell — the validate-on-install pattern above prevents
it.)

### Multiple humans — add a shared group

```bash
# Shared agent, so it isn't tied to one operator — neutral name 'local-agent'.
sudo useradd -m -d /opt/local-agent -s /bin/bash local-agent
sudo groupadd agents; sudo usermod -aG agents "$USER"; sudo usermod -aG agents local-agent
sudo chown -R local-agent:agents /opt/local-agent && sudo chmod 2750 /opt/local-agent
sudo mkdir -p /srv/workspace && sudo chown local-agent:agents /srv/workspace && sudo chmod 2770 /srv/workspace
sudo setfacl -d -m g:agents:rwx /srv/workspace
```

### Launch (every time)

```bash
sudo -u "$(whoami)-local-agent" -i bash -lc 'exec claude'
```

Single-user starts in the agent's home directly; for the multi-user setup the user
is `local-agent`, and add `cd /srv/workspace &&`.

---

First run only, authenticate the agent's own tools as that user (its own Claude
Code login and its own `jentic register`) — see the next section.

> **This is exactly the kind of thing `jenticctl` should wrap** — a
> `jenticctl agent-user setup` that runs the above (idempotently, with the right
> platform detection) collapses even the two-command recipe into one, and a
> `jenticctl doctor` check can warn when the agent is running as the same uid as
> Jentic One. That is the "make the safe path the default path" lever from
> [`03`](03-mitigations.md), applied here.

## Interaction with Claude Code specifically

Claude Code is a good stress test because it keeps state in several places:

- **Config + project state:** `~/.claude/` and `~/.claude.json`. These are
  **per-`$HOME`**, so running as `agent` with `HOME=~agent` gives the agent its
  **own** Claude config, MCP servers, and history — cleanly separate from the
  operator's. Good: the agent can't read the operator's `.claude.json` (it's
  `0600`, and with the agent account in its own group per the setup above there's
  no group path to it either).
- **Auth/credentials:** on macOS, Claude Code stores its OAuth credentials in the
  **login Keychain** (item `Claude Code-credentials`) when available, falling back
  to `~/.claude.json`. Keychains are **per-user** — the `agent` user has its own
  login keychain, so **the agent must authenticate Claude Code itself** (its own
  login / API key), and it cannot read the operator's Claude auth. This is a
  one-time setup cost, not a blocker. (For a headless/`launchd` agent with no GUI
  login session, Keychain unlock is fiddly — an `ANTHROPIC_API_KEY` env var or a
  dedicated keychain unlocked at session start is the usual answer.)
- **Sandbox / OS-permission prompts:** Claude Code's own sandbox and macOS **TCC**
  operate per-user. The `agent` user is a fresh principal, so it will **re-prompt
  for TCC-protected resources** (Documents, Desktop, etc.) the first time — but a
  well-scoped agent should be pointed only at the shared workspace and shouldn't
  need them. Expect first-run prompts; don't grant blanket access.
- **The `jentic` CLI state:** `~/.jentic/<profile>` (Ed25519 key + tokens) now
  lives in the **agent's** home, `0600` as the agent user — which is exactly right:
  the agent owns its own identity, and the operator (who runs Jentic One as a
  *different* user, ideally) never had the agent's key anyway.

### GUI vs CLI agents
Our current focus is CLI-initiated agents, where `su - agent` / `sudo -u agent -i`
is the whole story. For completeness:
- A **GUI** coding agent (VS Code, Cursor.app, a desktop Claude) is harder: macOS
  ties GUI apps to the logged-in `Aqua` session. Running a second user's GUI app in
  the operator's session is awkward (fast-user-switching gives a separate session
  but you lose side-by-side; `launchctl asuser` + GUI is unsupported territory).
  **For GUI agents, containerising (fix 1a) or a separate host is the better
  path** — flag this rather than force the own-user model onto GUIs.
- The `jentic` CLI itself is unaffected either way — it's a pure HTTP client with a
  local key file, so it runs happily under the agent user in any of these models.

## Honest assessment

**Real issues (design around them):**
1. **Home-directory readability depends on the operator's home mode and on group
   membership, which varies by machine.** Depending on how accounts are provisioned,
   a freshly-created agent may share a group with the operator, and if the operator's
   home is group-readable it could read parts of it — including world/group-readable
   dirs like Jentic One's `~/.jentic` and Claude's `~/.claude` (the key *file* stays
   `0600`, but the directory is listable). The setup above closes this by
   construction with the machine-independent `chmod 700 ~` (owner-only home, no group
   path for anyone) plus a login shell, but it's the thing to get right — and it's
   why the key should not live in the operator's home in the first place.
2. Per-user auth means the agent must have its **own** Claude Code login / API key
   and its **own** `jentic` identity — one-time setup, and headless Keychain unlock
   needs thought.
3. Shared-workspace ergonomics need a deliberate ACL setup (a couple of `chmod
   +a`/`setfacl` lines on a single-user machine, or a group when several humans are
   involved); done naively you either can't collaborate or you over-share. (Hence
   the recipe.)

**Non-issues (don't overweight):**
- "Which directory does it open in" is a solved problem: use a login shell; the
  agent starts in its own home; `cd` to the shared workspace. Not a real obstacle.
- Separate Claude config/history under the agent's `$HOME` is a *feature*
  (clean separation), not a problem.
- TCC re-prompts are expected and correct — a fresh principal *should* re-consent.

**Where it's the wrong tool:** GUI agents (use a container or separate host), and
anything needing the operator's own sessions/credentials to be visible to the
agent (which is the whole thing we're preventing — if a workflow needs that, the
boundary can't hold regardless).

## Suggested `jenticctl` support (so we make it turnkey)

- A **doctor/preflight check**: detect when the agent appears to run as the *same*
  uid as Jentic One and warn (this is the T0 tripwire).
- A documented, copy-pasteable **setup recipe**: create `agent` user (with a
  distinct primary group), set its home to a neutral shared-group path, create the
  `agents` group + setgid workspace + inherited ACL, and the `su - agent` launch
  line. Ship it in `hardening.md` as the "local, no-container" hardening step
  between T0 and full containerisation.
