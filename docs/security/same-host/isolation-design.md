# How `jentic run` isolates a local agent

`jentic run` launches a CLI coding agent as a **dedicated, unprivileged Unix
user** (`<operator>-local-agent`), confined per session, so it cannot read the
operator's Jentic One keyset, credential DB, browser profile, SSH keys, or
Keychain. It reaches Jentic One the way any client does — over HTTP with its
own scoped token.

This page is the security design: the layers, what each one blocks, and what
the model deliberately does not protect. The problem it answers is in the
[threat model](threat-model.md); day-to-day usage (setup, flags, grants,
reset, troubleshooting) is in the
[local-agent guide](../../guides/local-agent.md). The implementation lives in
[`cli/internal/localagent`](../../../cli/internal/localagent/README.md).

## What it closes

Running as its own uid, the agent cannot read the operator's
`~/.config/jentic`, `~/.local/state/jentic`, the instance's config and
database, or the operator's browser profile. That closes **AP-1, AP-2 and
AP-3** on this machine. **AP-4** — the agent's own token or key reused
elsewhere — is outside what a uid boundary can close, because the agent has to
hold its own credentials; it is bounded by the token contract and the
instance's reachability (see [the threat model](threat-model.md#isolate-the-agent-not-just-the-instance)).

The isolation is **one-directional on purpose**: the operator is trusted and is
granted into the agent's home wholesale (to supervise its output); the agent is
never granted into the operator's home except one chosen directory at a time.

## The layers

Three layers stack. Each does one thing, and a session starts only when all
three hold.

| Layer | Mechanism | What it blocks |
| ----- | --------- | -------------- |
| **1. Agent account** (the floor) | A separate Unix user with its own home outside every human home | Everything the operator owns that is not world-readable: keys, tokens, DB, browser profile, Keychain. Rests on ownership, so it holds even if a sandbox profile is wrong |
| **2. Allow-only ACL grants** | Named-user ACLs: execute-only on ancestors, recursive read/write on the chosen directory | Nothing — they only *open* the one path the operator chose to the agent uid |
| **3. Per-session confinement** | `sandbox-exec` (macOS) / `bwrap` (Linux), regenerated at every launch | Any path under a human home that was not granted, even a world-readable one; writes to the binaries the agent is launched from |

### Layer 1 — the agent account

| | macOS | Linux |
| --- | --- | --- |
| Create | `sysadminctl -addUser` + `createhomedir -c -u` | `useradd -m -d <home> -s /bin/bash` |
| Default home | `/Users/Shared/<agent>` | `/opt/<agent>` |
| Delete (on reset) | `dscl . -delete /Users/<agent>` | `userdel` (without `-r`) |

The home lives under a shared parent, never under any human's home, so granting
the operator into it never widens a human home. There is one agent account per
operator, shared by every agent binary (`claude`, `codex`, `cursor`, `hermes`).

The agent has its own `$HOME`, so its own agent config, login Keychain, and
`jentic` identity. It authenticates its tools once, as itself, and cannot ride
the operator's sessions. At every launch `jentic run` exports the operator's
**active context** into the agent home's own XDG store
(`<agent home>/.config/jentic`, `<agent home>/.local/state/jentic`): one
environment, identity and context, with the mode forced to `agent`, plus the
env-scoped key and tokens (`0600`, chowned to the agent). The operator's store
stays the single source of truth; the export is overwritten on the next launch
and removed by `jentic reset`.

### Layer 2 — allow-only ACL grants

To let the agent work in `~/projects/api`, the grant lays down:

- **Traverse** — execute-only (search, not list, not read) on each ancestor the
  agent cannot already traverse, non-recursive. The agent can pass *through*
  `~/projects` to a path it knows but cannot list `~/projects`.

  ```bash
  sudo chmod +a "user:$AGENT allow execute" "$ANCESTOR"   # macOS
  sudo setfacl -m u:"$AGENT":--x "$ANCESTOR"              # Linux
  ```

- **Leaf** — recursive, inherited read/write/execute on the directory, its
  existing contents, and everything created there later. On macOS the full
  permission set is spelled out (the `write` shorthand on a directory omits
  `delete`, `delete_child` and `add_subdirectory`, which breaks
  write-then-rename). On Linux it is an access ACL plus a default ACL
  (`u:$AGENT:rwX`).

The leaf grant is **recursive over existing contents**: granting `~/a/b` gives
read/write on every file already under it. Grant a narrower directory if a
subtree holds something the agent should not touch. The recursive walks skip
symlinks and multiply hard-linked files, so a link the agent plants in a
granted tree cannot extend a later re-grant to a file elsewhere.

The ACL layer never adds deny entries and never changes the mode of `~`. The
operator's home keeps its OS-default mode.

### Layer 3 — per-session confinement

The ACLs alone leave a gap. To reach `~/a` the agent needs *search* on `~`, and
directory search is not per-entry: an agent that can search `~` can open any
sibling it can name, and whether it can read `~/b` then depends only on `~/b`'s
own mode. A `0700` directory stays closed; a `0755` checkout is readable. No
portable ACL or mode arrangement fixes this (POSIX ACLs have no deny, and
stripping world bits would rewrite the operator's files). Confinement fixes it
on a different axis — the process's view of the tree:

- **macOS** — a Seatbelt profile built per launch:

  ```scheme
  (version 1)
  (allow default)
  (deny file* (subpath "/Users"))
  (deny file* (subpath "/home"))
  (allow file-read-metadata (literal "/Users") (literal "/Users/Shared"))
  (allow file* (subpath "/Users/Shared/alice-local-agent"))
  (allow file* (subpath "/Users/alice/projects/api"))
  (deny file-write* (subpath "/opt/homebrew/bin"))
  (deny file-write* (subpath "/usr/bin"))
  ```

  Ancestors of each re-opened path get `file-read-metadata` on the literal
  directory node only — traversal without exposing its other entries.

- **Linux** — `bwrap --die-with-parent --dev-bind / /`, then a `--tmpfs` over
  `/Users` and `/home`, a `--bind` for the agent home and each grant, and a
  `--ro-bind` for each executable route. bwrap grants no privilege; normal file
  permissions still apply inside the namespace.

Three properties hold by default and cannot be relaxed through the grant flow:

1. **All human homes are denied**, not just the operator's. A grant into one
   user's tree can never expose another's.
2. **The binaries the agent runs are read-only.** `/usr/bin`, `/bin`,
   `/usr/sbin`, `/sbin`, `/usr/local/bin`, the shared tool dirs that exist
   (`/opt/homebrew/bin`, plus `/opt/homebrew/sbin` on macOS or `/snap/bin` on
   Linux),
   and the agent's own `~/.local/bin` — which sits inside its writable home —
   are emitted last as write-denies (Seatbelt is last-match-wins; bwrap applies
   mounts in order). Without this an agent could overwrite the binary its next
   launch runs.
3. **It errors closed.** If `sandbox-exec` is missing, or `bwrap`, unprivileged
   user namespaces, or the `acl` tools are unavailable on Linux, `jentic run`
   refuses to launch (`CONFINEMENT_UNAVAILABLE`) rather than run unconfined.
   Setup checks the same prerequisites before creating the account, and
   `jenticctl doctor` reports them, from one shared probe.

The confinement wrapper is invoked by **absolute path**, resolved in the
operator's process, and the outer `sudo -u <agent> -H bash -c` shell is a
non-login shell — so no agent-owned rc file runs before the sandbox takes hold,
and the agent's PATH cannot shadow the wrapper. The login shell that sources
the agent's profile runs inside the sandbox.

**A targeted deny, not a jail.** The base profile is permissive
(`(allow default)` / a full `--dev-bind`), and only the human-home roots are
carved out. The agent's runtime dependencies outside human homes — its binary
and libraries, `/tmp`, `/dev`, the loopback socket, system tools — need no
enumeration, so an OS or agent update does not break the launch. The cost is
stated below under residual risks.

### What a grant may never cover

Before offering **Allow**, `jentic run` classifies the target (after resolving
symlinks; case-insensitively on macOS). A banned path gets no "grant anyway"
option, `--allow-dir` refuses it, and `--yes` takes the safe default.

| Class | Covers | Below it |
| ----- | ------ | -------- |
| **Soft ban** | Any human home root (`/Users/<name>`, `/home/<name>`, including the operator's own) and `~/.config` | A subdirectory is grantable (`~/projects/api`, `~/.config/nvim`) |
| **Hard ban** | `~/.ssh`, `~/.jentic`, `~/.aws`, `~/.gnupg`, `~/.gcloud`, `~/.kube`, `~/.docker`, `~/.mozilla`, `~/Library/Keychains`, Chrome's profile dir, and `~/.config/{gcloud,google-chrome,chromium,gh,jentic}`; system trees `/etc`, `/usr`, `/var`, `/System`, `/Library`, `/bin`, `/sbin`, `/` | Nothing — the path and every descendant are refused |

The same classifier filters the bulk "bring your workspaces over" offer during
setup, and every selection is re-classified at grant time. The grant inventory
lives in the operator's `~/.config/jentic/agent-account.yaml`, which is itself
hard-banned and outside the sandbox, so the agent cannot edit its own access.

## Other hardening on the launch path

- **No environment leak.** The launch passes only terminal and locale variables
  (`TERM`, `COLORTERM`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TERM_PROGRAM`) and
  unsets `SSH_AUTH_SOCK`, `SSH_AGENT_PID`, `SSH_CONNECTION`, `SSH_CLIENT`,
  `GPG_AGENT_INFO` and `GPG_TTY` inside the launch snippet, independent of the
  machine's sudoers `env_keep`.
- **Scoped passwordless launch.** The optional sudoers rule, written to
  `/etc/sudoers.d/jentic-agent` through a `visudo -cf`-validated temp file, is
  `<operator> ALL=(<agent>) NOPASSWD: /bin/bash` — it lets the operator become
  the agent user, not root.
- **Seeding never moves login tokens.** Seeded agent config has discrete
  credential files removed (`~/.codex/auth.json`, `~/.hermes/.env`); seeded
  provider config excludes cached logins (`~/.aws/sso/cache`, `~/.aws/cli/cache`;
  gcloud's `credentials.db`, `access_tokens.db`, `legacy_credentials`,
  `application_default_credentials.json`).
- **Validated inputs.** Account names, homes and grant paths are validated
  before they reach a shell, sudoers line, ACL entry or SBPL profile, and
  quoted again at each sink.
- **Reset removes credentials even from a kept home.** `jentic reset` drops the
  leaf and traverse ACLs first, then removes the agent's exported identity and
  the seeded agent/provider config dirs from its home before re-owning the home
  to the operator, then the sudoers line, then the account.

## Residual risks

The boundary protects the operator from the agent. It is not an intra-agent,
network-egress, or secret-hiding sandbox.

1. **No boundary between agent identities.** There is one agent account, and
   every identity exported into it is readable by every agent launched through
   `jentic run`. Contexts select an identity; they are not a security
   compartment.
2. **Secrets handed to the agent are disclosed to the agent.** Anything in the
   agent's environment or home — including seeded LLM-provider keys — is
   readable by the agent and by anything else running as its uid. Front the
   model with an LLM proxy (e.g. [LiteLLM](https://docs.litellm.ai/)) and give
   the agent a revocable virtual key, so a leak costs a rotation, not a real
   provider credential. The Jentic One agent token is already that kind of
   key: scoped, short-lived, carrying no third-party credential, and only
   redeemable where the instance is reachable.
3. **Network egress is not restricted.** Confinement controls the filesystem
   view; the agent can make arbitrary outbound connections. Combined with (2),
   assume an agent that obtains a secret can send it somewhere. Use a host
   firewall, a forced proxy, or network namespaces if you need egress control —
   `jentic run` does not install one.
4. **It is not a container.** Paths outside human homes (`/tmp`, `/etc`,
   `/opt`, `/Library`) stay governed by ordinary file permissions, and process
   execution, IPC and system services are not restricted. The agent cannot
   signal the operator's processes (different uid), but it shares the kernel: a
   local privilege-escalation bug crosses this boundary. A container or VM is
   the stronger option.
5. **`sandbox-exec` is deprecated on macOS.** It still works and underlies the
   App Sandbox. If a future macOS removes it, `jentic run` errors closed.
   Hardened Linux distributions that restrict unprivileged user namespaces
   (e.g. Ubuntu's AppArmor restriction) are detected and error closed the same
   way.

## GUI IDEs (Cursor / VS Code)

Don't launch an IDE GUI as a different macOS user inside the operator's login
session: WindowServer, the launchd Aqua domain, TCC and the Keychain are tied to
the console user, and the workarounds that make it render are the ones that
break the isolation. Use the IDE's client/server split instead: run the UI as
the operator and **Remote-SSH into `<agent>@localhost`**, which starts the
server, extension host and integrated terminal as the agent user. Headless
agents under `jentic run` (Claude Code, `cursor-agent`, …) remain the simpler
and stronger path; note that a Remote-SSH session is not wrapped in the
per-session confinement.
