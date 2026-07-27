# Securing the credential boundary for locally-run agents

> **Status:** analysis / design exploration. Not yet a committed plan.
> **Scope:** the failure mode where a locally-installed Jentic One shares a
> trust boundary with a locally-run agent, collapsing the "agent never sees
> credentials" guarantee.

## The core problem in one sentence

Jentic One's central promise — *the agent never sees the credential* — is a
**network-path** guarantee, but most users run the agent and Jentic One as the
**same OS user on the same machine**, where that guarantee does not hold.

## Why this matters now

The go-to-market motion is a one-line install (`curl … | sh`) aimed at
developers running coding agents (Claude Code, Cursor, Codex) locally. That
audience, by default, lands in the **weakest** posture (Tier T0 in
[`hardening.md`](../hardening.md)): agent and broker share a user and a host.

In that posture the credential boundary is defeated not by breaking the Broker,
but by **going around it**:

1. **Direct DB / key read.** A same-user process can read the credential
   database and the encryption key off disk or out of memory. No API auth
   applies. (Axis A failure.)
2. **Local admin/UI takeover.** The agent can reach the local Admin API / UI on
   loopback and — if it can obtain or forge an operator session — grant itself
   access, mint tokens, or read exportable material. (Control-plane failure.)
3. **Browser-session hijack.** Even if the agent is blocked from the DB, an
   agent with browser control (headless Chrome, a drivable browser extension,
   or shared cookie jar) can drive the *human's* authenticated admin session and
   act as the operator. (Session-confused-deputy failure.)
4. **Token/credential-file exfiltration.** Agent-held bearer tokens or config
   files in the shared home dir can be read and reused off-host.

## The four things we already know (user framing)

| # | Statement | Where it lands in this analysis |
|---|-----------|--------------------------------|
| 1 | Strongest boundary = Jentic One on an external VPC the agent can't reach | Target end-state; [`03`](03-mitigations.md) "separation" track |
| 2 | The quick install yields an insecure same-user setup | The central problem; [`01`](01-threat-model.md), [`03`](03-mitigations.md) install track |
| 3 | Browser control with the user's sessions breaks the paradigm even with DB isolation | [`02`](02-browser-session-hijack.md) |
| 4 | Containerising the agent + mounting only a workspace + only the `jentic` CLI is secure — but we don't own the client, so agent-side setup must be minimal | Constraint that shapes every mitigation; [`03`](03-mitigations.md) |

## The hard constraint

**We do not own the agent client.** Any mitigation that requires heavy agent-side
setup will not be adopted and will cost users. So the design space splits into:

- **Server-side / install-side changes we fully control** (defaults, separate
  user, token model, UI-session hardening). High leverage.
- **Agent-side asks that must be near-zero-effort** (point the CLI at a base URL,
  hold a scoped token). Anything more is opt-in for security-conscious users.

## Documents in this analysis

- [`01-threat-model.md`](01-threat-model.md) — actors, assets, trust boundaries,
  and the concrete attack paths, grounded in the current code.
- [`02-browser-session-hijack.md`](02-browser-session-hijack.md) — the issue-#3
  class: an agent driving the operator's authenticated browser session.
- [`03-mitigations.md`](03-mitigations.md) — layered mitigations, ordered by
  leverage vs. adoption cost, mapped to what we control.
- [`04-current-state.md`](04-current-state.md) — factual map of the current
  mechanisms (auth, crypto, install defaults) with file citations, so the above
  is grounded and not speculative.
- [`05-agent-as-own-unix-user.md`](05-agent-as-own-unix-user.md) — deep-dive on the
  structural fix of running a CLI agent (Claude Code) as its own Unix user:
  working-directory behaviour, shared-home layout, a ~5-command setup recipe, and
  the real sharp edges.
- [`06-gui-ide-as-agent-user.md`](06-gui-ide-as-agent-user.md) — the GUI case: can
  Cursor / VS Code run as the agent user? (No, not the raw GUI — but yes via the
  client/server Remote-SSH split, or headless `cursor-agent`.)
