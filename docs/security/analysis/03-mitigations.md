# Mitigations

> Grounded in [`01`](01-threat-model.md), [`02`](02-browser-session-hijack.md),
> [`04`](04-current-state.md). The organising constraint is user framing point #4:
> **we don't own the agent client, so agent-side asks must be near-zero-effort.**

## The key distinction: isolate the agent, or isolate Jentic One?

There are two structurally different ways to restore the boundary, and they are
**not** equally powerful:

- **Isolate the *agent*** (run it in a container, or as its own OS user, so it
  cannot see the host filesystem, `~/.jentic`, the DB, *or* the operator's browser
  profile). Configured correctly this closes **every** gap — AP-1, AP-2, AP-4, and
  the browser gap AP-3 — because all four depend on the agent being able to reach
  something it now cannot. This is the real fix.
- **Isolate *Jentic One*** (run it under its own OS user, or on a separate
  host/VPC, so the agent's user can't read the key/DB). This closes AP-1 and AP-2
  (key/DB read, `jwt_secret` forge), but the operator's browser still runs as the
  human's user right next to the agent, so **the browser gap (AP-3) remains open.**

So: **agent isolation ⊃ Jentic-One isolation.** The classic advice ("put it on a
VPC") is the *weaker* of the two — necessary for teams/production, but on a single
developer machine it leaves the browser gap. The only reason to prefer Jentic-One
isolation is that it needs nothing of the agent client, whereas agent isolation
needs the user to run their agent our way (point #4).

Everything else in this document is **defense-in-depth**: real, worth doing, but
none of it *closes* a structural gap on its own. It shrinks blast radius for when
one of the structural fixes is absent or misconfigured.

The three axes from [`hardening.md`](../hardening.md), restated against this:

- **Axis A — isolate the key/DB from the agent** (Jentic-One isolation; AP-1/AP-2).
- **Axis B — where Jentic One runs / how clients reach it** (network hardening).
- **Axis C — operator-session integrity** (the *other* way to close AP-3 when the
  agent is not isolated — see WebAuthn step-up below).

---

## Structural fix 1 — Isolate the agent (closes everything)

The strongest posture, and the one point #4's own text calls out ("containerising
your agent, mounting only a workspace dir, installing only the `jentic` CLI is
also a secure pattern"). Two forms:

### 1a. Containerise the agent
Agent runs in a container / devcontainer with:
- **only the workspace directory mounted** — so `~/.jentic` (keyset, `jwt_secret`,
  agent keys) and the browser profile are *not visible*;
- **only the `jentic` CLI** installed and a scoped token;
- **default-deny egress**, allowlisting only Jentic One's address.

This closes AP-1/AP-2 (can't read key or DB), AP-4 (nothing to exfiltrate but a
short-lived token), and **AP-3 — the browser gap — because the container has no
access to the host browser profile or a drivable browser.** On macOS this is
especially clean: Docker Desktop runs the container inside a Linux VM, so the host
filesystem is invisible by default and only bind-mounts are shared.

We can't mandate it (we don't own the client), but we *can* ship it turnkey: a
reference devcontainer / compose snippet the user opts into. That satisfies point
#4 — the user's only step is "use our recipe."

### 1b. Run the agent as its own OS user (esp. macOS)
Where containerising the agent isn't practical, running the agent under a
**dedicated, unprivileged OS user** — distinct from the operator's login user —
gives most of the same guarantee via file ownership and per-user data stores:

- The agent user cannot read the operator's `~/.jentic/jentic-one.yaml` (keyset +
  `jwt_secret`) or the operator's DB files → **AP-1/AP-2 closed.**
- On macOS, browser sessions live under the operator's `~/Library/Application
  Support/...` and are protected both by Unix ownership and by **TCC** (the agent
  user gets no access to another user's protected data), and the agent user has no
  standing session in the operator's browser → **AP-3 (V1/V2 profile-lift and
  drive-the-profile) closed.** The agent would have to log in as an admin itself,
  which the operator-session controls (Axis C) then govern.
- The agent still reaches Jentic One over loopback HTTP with its scoped token, so
  point #4 holds — the *connection* is unchanged.

**macOS specifics / friction to design around:**
- Create a separate standard (non-admin) account for the agent (`sysadminctl
  -addUser agentrunner` or System Settings → Users). Run the coding agent as that
  user — via `su - agentrunner` / `launchd` under that uid for headless agents, or
  fast-user-switching / a separate login for GUI-driven ones.
- **Shared workspace:** put the working tree in a location both users can reach
  (a shared group + group-readable dir, or macOS ACLs), *without* exposing the
  operator's home. This is the main ergonomic cost.
- **TCC prompts:** the agent user is a fresh security principal, so it will
  re-prompt for any protected resources it legitimately needs — a one-time cost.
- **Not as strong as 1a:** same kernel, same machine; a local-privilege-escalation
  bug crosses the boundary. It's the pragmatic middle when a container is too
  heavy but same-user (T0) is unacceptable.

> Both 1a and 1b are things the *user* does to their agent. Our job is to make them
> the documented, low-friction default path (a recipe + a `jenticctl` doctor check
> that warns "agent appears to run as the same user as Jentic One").

## Structural fix 2 — Isolate Jentic One (closes all but the browser gap)

When we can't touch the agent side at all, isolate Jentic One instead. Closes
AP-1/AP-2; **leaves AP-3 open** (hence pair it with Axis C below).

### 2a. Separate OS user / rootless container for Jentic One — as the install default
The single biggest thing **we fully control**. If `jenticctl install` provisions
Jentic One under a **dedicated non-root user** (or rootless container) by default,
the agent's user can no longer read the keyset, `jwt_secret`, or DB — the boundary
becomes an OS-user boundary instead of a file permission the owner defeats.
- Today everything is written as the invoking user (`cmd/install.go:393-419`),
  `0600` `~/.jentic/jentic-one.yaml` (`install/secrets.go:22-53`) — which the
  owning user reads freely.
- **Cost:** the wizard must create/manage a service user or rootless container and
  an elevation step. Recommend opt-out, not opt-in.

### 2b. Scripted remote deploy + sidecar token-broker
`jenticctl` command that stands Jentic One up on a **separate host/VPC** and points
the local CLI at it over a private route (the T3 target in `hardening.md`, but
scripted — agent-side change is just base-URL + token, point #4 satisfied). Pair
with the **sidecar** pattern already promised at `hardening.md:159-176`: the
sidecar holds the agent's long-lived credential and hands out only ephemeral,
host-bound tokens, so a compromised agent can't exfiltrate a reusable secret (AP-4).

## Closing the browser gap when the agent is *not* isolated — WebAuthn step-up

If neither structural fix isolates the agent from the browser (i.e. we only did
"structural fix 2"), AP-3 is still open and needs Axis C. The strongest single
lever: **require a WebAuthn / passkey presence gesture on dangerous mutations** —
minting agent keys, replacing scopes, approving access requests, creating
credentials, adding users. A driven/headless browser (AP-3 V2–V4) can't produce
the gesture, and a lifted `localStorage` token (V1) can't either. This is what lets
"Jentic One on a VPC" actually close the boundary on a dev machine, rather than
closing all-but-browser. (Full attack detail: [`02`](02-browser-session-hijack.md).)

---

## Defense-in-depth (do these, but they don't close a gap on their own)

Kept deliberately brief — each shrinks blast radius or improves posture awareness,
but none is a substitute for a structural fix above.

- **Encryption key out of the config file** (OS keychain / runtime-injected rather
  than `material:` in YAML, `install/secrets.go:22-53`). Raises the bar on AP-1;
  moot once the agent is isolated (fix 1) or the key is a different user's (fix 2a).
- **Subset-bound scope grants + a non-`org:admin` operator role.** Today
  `replace_scopes` has no subset check (`agent_service.py:374-405`); bounding it and
  not logging in as `org:admin` daily shrinks what a hijacked session/token grants.
- **Short-lived, least-privilege agent tokens by default** (scopes already
  live-resolve, `token_service.py:294-347`); narrow the default set
  (`shared/scopes.py:21-34`) and shorten TTLs to limit AP-4.
- **Sender-constrained tokens (DPoP / mTLS)** so a lifted token can't be replayed
  off-host (AP-4); CLI-internal, near-zero operator cost.
- **Posture banner / informed default** — installer + UI state the current tier
  ("T0: same-user, local — don't store production credentials") and link
  `hardening.md`. Cheap; converts a silent insecure default into an informed one.
- **Keep "reveal never" as an invariant** — no credential export endpoint exists
  today (`04`); lock it with an arch test so no convenience feature reopens AP-1.
- **Out-of-band alerts + reversibility** on sensitive mutations (new grant / new
  agent key), leaning on the append-only audit log (`orchestrator.py:94-105`).

---

## Priority recommendation

**Class 1 — structural boundary fixes** (each closes a real gap by construction):

| # | Change | Closes | Notes |
|---|--------|--------|-------|
| 1 | **Containerised-agent recipe** (workspace-only mount, `jentic` CLI only, deny-egress) | **AP-1, AP-2, AP-3, AP-4 — all** | Strongest. Opt-in (agent-side), so ship it turnkey |
| 2 | **Agent as its own OS user** (esp. macOS) | **AP-1, AP-2, AP-3, AP-4 — all** | Pragmatic middle; weaker than a container (same kernel) but no container needed |
| 3 | **Separate OS user / rootless container for Jentic One** — install default | AP-1, AP-2 (**every gap except browser**) | The best thing we fully control; needs nothing of the agent |
| 4 | **WebAuthn / passkey step-up on dangerous mutations** | AP-3 (the browser gap) | The complement to #3: #3 + #4 together ≈ #1/#2's coverage without isolating the agent |
| 5 | **Scripted remote deploy + sidecar** | AP-1, AP-2, AP-4 (browser still needs #4) | Isolate-Jentic-One family; the teams/production end-state |

**Class 2 — defense-in-depth** (worth doing; none closes a gap alone, so keep them
below the structural work): key-out-of-config, subset-bound scopes / operator role,
short least-privilege tokens, DPoP, posture banner, reveal-never arch test,
out-of-band alerts.

### The headline

- The **only complete fixes are #1 and #2 — isolating the agent.** Done right they
  close every gap, including the browser gap, and point #4 explicitly blesses them;
  our job is to make them turnkey.
- **#3 (isolating Jentic One) is the best default we can ship unilaterally**, and
  it closes everything *except* the browser gap — pair it with **#4 (WebAuthn
  step-up)** to get near-complete coverage without touching the agent client.
- Everything in Class 2 is real hygiene but should not be mistaken for progress on
  the structural boundary — it shrinks blast radius, it doesn't move the wall.
