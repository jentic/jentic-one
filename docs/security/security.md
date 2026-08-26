# Deploying Jentic One securely

Jentic One handles third-party API credentials. This guide explains the threat
model and gives a tiered set of deployment postures — from "fine for trying it
out" to "handling real production credentials" — so you can pick the right level
of isolation for your use case.

> **TL;DR:** Jentic One keeps your credentials off the
> *network* path (the Broker injects them; the agent only ever sees responses).
> To also keep them off the *host* path, **don't run Jentic One as the same OS
> user as your agent** — sandbox the agent, or run Jentic One on a separate
> host/network. The agent and CLI reach Jentic One over its HTTP API, so a
> remote or private-network deployment is reached the same way you'd reach any
> private service (VPN / private DNS / authenticated proxy) — see below.
>
> The strongly recommended pattern is to run Jentic One on a separate
> host or network.

## Threat model

The Broker's core guarantee is that a secret is injected into the outbound
request inside the data plane and is **never returned to the caller and never
seen by the agent**. That guarantee holds on the network.

It does **not** hold on the host when the agent runs on the **same machine and
as the same OS user** as Jentic One — the default when you run a local install
next to a coding agent (e.g. Claude Code, Cursor). A same-user process can read
the credential database and the encryption key directly off disk or out of
memory, regardless of the API-level controls. This is the single most important
thing to understand before you point Jentic One at real credentials.

## Two independent decisions

Hardening Jentic One is really **two separate questions** — people often conflate
them, but they're independent and you tune each on its own:

- **Axis A — Isolate the agent from Jentic One's secrets.** Stop a process on the
  same machine from reading the key/DB. Levers: run Jentic One as a *different OS
  user* or on a *different host*; and/or *sandbox the agent*.
- **Axis B — Where Jentic One runs, and how clients reach it.** localhost →
  another host → a private network/VPC. This is ordinary network hardening (don't
  expose it to the internet; put a VPN or authenticated proxy in front).

The key thing that makes Axis B simple: **the agent and the CLI are just HTTP
clients.** They talk to Jentic One over its authenticated API at a base URL — so
moving Jentic One to another host or into a private network doesn't change *how*
they connect, only *what address* they point at and *how the network route is
secured*. (See "How the agent and CLI connect" below.)

## How the agent and CLI connect

Everything that uses Jentic One — a coding agent, a running service, and the
`jentic` CLI (`register`, `catalog`, `execute`) — reaches Jentic One over HTTPS
at a configurable base URL (e.g. the CLI's `--broker-scheme` / `--broker-host`,
or the equivalent config/profile) plus a scoped bearer token. There is **no
requirement that any of them run on the same machine as Jentic One.**

So when Jentic One is remote or inside a private network/VPC, clients reach it
the normal way you'd reach any private service:

- **Agent or service running inside the same network/VPC** — the common
  production shape: a provisioned agent/service sits in the same private network
  and calls Jentic One over private DNS. Nothing is exposed publicly.
- **Operator/CLI from a laptop** — connect over a **VPN** (Tailscale/WireGuard),
  an SSH tunnel/bastion, or an authenticated reverse proxy. The CLI just points
  at Jentic One's private address once you're on the network. You do **not** need
  to put Jentic One on the public internet to use the CLI.
- **A local coding agent (Claude Code/Cursor) against a remote Jentic One** — works
  the same way (VPN + a scoped token), but see the note on production below.

**On production + interactive coding agents:** production credentials are best
used by **provisioned agents/services that run near Jentic One** (same private
network), each with its own short-lived, least-privilege token. Pointing a
developer's local interactive coding agent at a *production* Jentic One is a
dev-workflow pattern, not a production one — if you do it, do it over a VPN with a
tightly-scoped, short-lived token, and prefer a non-production instance for
day-to-day agent development. This keeps the blast radius of a prompt-injected or
compromised local agent away from production secrets.

## Deployment tiers

Each tier combines a point on Axis A (agent isolation) and Axis B (Jentic One
location). Pick the lowest tier that matches the sensitivity of the credentials
you store.

| Tier | Who it's for | Posture | Residual risk |
|---|---|---|---|
| **T0 — Local, same user** | Trying it out | Jentic One + agent run as your user on your laptop | A compromised or prompt-injected agent can read the key/DB. **Do not store real high-value credentials.** |
| **T1 — Sandbox the agent** | Local dev with some real creds | Jentic One still local, but the agent runs under its built-in OS sandbox with **network default-deny**, allowlisting only Jentic One's address, and unable to read Jentic One's files (provided they are not mounted in the agent's container) | Agent sandboxes filter by hostname without TLS inspection and have escape hatches → defense-in-depth, not a hard wall. Containerising agents is the best form of local sandboxing if available |
| **T2 — Separate users on one host** | Serious local use | Jentic One runs under a **dedicated non-root user** (or rootless container) so its key/DB aren't readable by the agent's user; the agent runs in a container with default-deny egress and reaches Jentic One over loopback/HTTP | Shared kernel |
| **T3 — Separate host / private network** *(recommended for real use)* | Teams / production | Jentic One + database on a **separate host/VM in a private network**, reachable only over that private network (VPN / private DNS / authenticated reverse proxy). Provisioned agents/services run inside the network; operators use the CLI over the VPN. The agent never shares a machine with the key store | Standard cloud hardening applies |
| **T4 — Strong isolation** | Untrusted agents / high-value creds | Agent in a **microVM** (e.g. Firecracker) or **gVisor** sandbox; Jentic One in a private subnet with restrictive security groups and default-deny egress; clients reach it only over private networking | Highest ops cost, smallest attack surface |

> **Note on "VPC":** a private subnet / VPC is just the cloud form of T3's private
> network — Jentic One isn't publicly reachable, and clients get in over a VPN,
> private DNS, or a PrivateLink-style endpoint. Jentic offers a managed **VPC
> edition** if you'd rather not operate this yourself (see the end of this guide).

## Concrete controls

These are the practices that comparable self-hosted secret-handling projects
(HashiCorp Vault, Infisical, LiteLLM) converge on. Adopt as many as your tier
warrants:

1. **Don't expose Jentic One to the public internet.** Bind to loopback or a
   private network; put an authenticated reverse proxy or VPN in front. Operators
   and the CLI connect over that private route — never a public port.
2. **Run as a dedicated, unprivileged (non-root) user.**
3. **Keep the encryption key out of files on disk** — inject it at runtime via
   an environment variable or secret manager, and store it outside the database.
   Never commit a real key.
4. **Never hand the agent a long-lived secret.** Jentic One is built for this:
   agents get short-lived, scoped tokens; the real credential stays in the
   Broker.
5. **Ship a hardened container** — non-root, read-only root filesystem, dropped
   Linux capabilities, `no-new-privileges`; prefer rootless Docker.
6. **TLS everywhere**, terminated at a reverse proxy.
7. **Network segmentation** between the agent and Jentic One/DB (host firewall,
   Kubernetes NetworkPolicy, or cloud security groups).
8. **Keep the audit log.** Every credential access and authorization decision is
   recorded — ship it somewhere durable.
9. **Rotate the encryption keyset** on a schedule (the keyset supports multiple
   versioned entries with an `active_id` for do-and-revoke rotation).

## Sandboxing the agent (Axis A)

If the agent must run on the same host as Jentic One (Tier 1), run it inside a
sandbox that denies network egress by default and only allows Jentic One's
address, and that cannot read Jentic One's key files. Options, lightest to
strongest:

- **Built-in agent sandboxes** — Claude Code
  ([sandboxing](https://code.claude.com/docs/en/sandboxing),
  [`sandbox-runtime`](https://github.com/anthropic-experimental/sandbox-runtime)),
  OpenAI Codex CLI
  ([sandboxing](https://developers.openai.com/codex/concepts/sandboxing)),
  Cursor ([agent sandbox](https://cursor.com/docs/reference/sandbox)). These use
  OS primitives (macOS Seatbelt; Linux bubblewrap/Landlock/seccomp) plus a
  network allowlist. Note they filter by hostname without TLS inspection and have
  escape hatches — treat as defense-in-depth.
- **OS primitives directly** — Linux
  [bubblewrap](https://github.com/containers/bubblewrap) (`--unshare-net` for a
  loopback-only network namespace),
  [Landlock](https://docs.kernel.org/userspace-api/landlock.html),
  [seccomp](https://docs.kernel.org/userspace-api/seccomp_filter.html); macOS
  [Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime).
- **Container / devcontainer** with a default-deny egress firewall, optionally
  under [gVisor](https://gvisor.dev/); in Kubernetes, a default-deny egress
  [NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/).
- **Separate VM / microVM** — [Firecracker](https://firecracker-microvm.github.io/)
  gives each workload its own guest kernel.

When Jentic One runs on a **separate host/network** (T3+), the agent can't touch
the key/DB by construction, so agent sandboxing becomes about limiting what a
compromised agent can *do* (egress control, least-privilege tokens) rather than
protecting the key at rest.

## Agent access credentials

How the agent authenticates to Jentic One depends on where Jentic One is
deployed:

- **Local deployments (same machine as the agent)** — the agent's virtual OAuth
  access credentials (scoped bearer tokens) can live directly in the agent's
  environment (e.g. env vars, a local config file). Because both the agent and
  Jentic One share the same trust boundary, there is no additional exposure from
  the agent holding its own token.

- **Publicly-accessible or remote deployments** — agent access credentials
  should **not** live inside the agent's environment. Instead, use a **sidecar**
  or external token broker that handles agent authentication on behalf of the
  agent process. This prevents a compromised or prompt-injected agent from
  exfiltrating long-lived credentials that could be used from outside the
  network.

> More detailed documentation on sidecar authentication patterns will be provided in future work.

## Production checklist

Before pointing Jentic One at production credentials:

- [ ] Jentic One + database run on a **separate host/network** from any agent (T3+).
- [ ] Clients reach Jentic One over a **private route** (VPN / private DNS /
      authenticated proxy) — provisioned agents/services run inside the network;
      operators use the CLI over the VPN. No public Jentic One port.
- [ ] Jentic One runs as a **dedicated non-root user** (or hardened/rootless container).
- [ ] The **encryption keyset is injected via env/secret manager**, not committed;
      generate a real 32-byte key:
      `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`.
- [ ] `admin.auth.jwt_secret` and other placeholder secrets are set to real values.
- [ ] Jentic One is **not exposed to the public internet** (private network / VPN /
      authenticated proxy).
- [ ] TLS is terminated in front of Jentic One.
- [ ] The audit log is shipped to durable storage.
- [ ] Telemetry is set as you intend (it is **off by default**; see the
      [README](../../README.md#security--telemetry)).

## Reporting a vulnerability

See [SECURITY.md](../../SECURITY.md).

## Need help hardening this for production?

This guide covers the self-serve path. If you'd like a security review of your
Jentic One deployment, or hands-on help running it safely at scale, reach out at
[jentic.com/contact](https://jentic.com/contact) — no obligation.

## Further reading

- HashiCorp Vault — [production hardening](https://developer.hashicorp.com/vault/docs/concepts/production-hardening)
- Infisical — [production hardening](https://infisical.com/docs/self-hosting/guides/production-hardening)
- OWASP — [Docker Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html)
- Docker — [rootless mode](https://docs.docker.com/engine/security/rootless/)
