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

<!-- Sections added incrementally: binary provisioning, directory access,
     danger-flagging, and the wiring back into 05/jenticctl. -->
