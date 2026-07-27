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

### What it does, in order

1. **Resolve the agent user** (`<operator>-local-agent` by default) and verify it
   exists; if not, point at the [`05`](05-agent-as-own-unix-user.md) setup (or the
   proposed `jenticctl agent-user setup`).
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

<!-- Sections added incrementally: directory access, danger-flagging, and the
     wiring back into 05/jenticctl. -->
