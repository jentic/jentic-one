# Product Scope

> **STATUS: DRAFT — needs product-owner review.**
> This document was drafted by inferring scope from `README.md`, `CLAUDE.md`, and
> the codebase. It exists to give the issue-intake harness (see
> [`.harness/ISSUE_INTAKE_STANDARDS.md`](../.harness/ISSUE_INTAKE_STANDARDS.md)) an
> authoritative rubric for scoring **product fit**. Every `TODO(product)` marks a
> spot where a human must confirm or correct the inferred intent. Until those are
> resolved, treat fit scores as advisory.

## One-line definition

Jentic One is a **self-hosted gateway for secure third-party API execution by AI
agents**: you register the APIs an agent may use, store the credentials once, and
the agent calls out through a credential-injecting Broker so **secrets never leave
your infrastructure and never reach the agent**.

## Who it's for

- Teams running AI agents that must call real third-party APIs (SaaS, internal
  services) without handing the agent raw API keys.
- Operators who want fine-grained, auditable control over which agent may call
  which API, with which credential, under which permissions.

TODO(product): confirm the primary persona(s) and whether there is a secondary
audience (e.g. platform teams embedding Jentic One vs. individual developers).

## What's in scope (the product is about this)

The four surfaces, from `README.md`:

- **Broker** — stateless credential-injecting HTTP proxy (the data plane).
- **Registry** — the catalogue of registered APIs (immutable revisions, operations,
  security schemes, servers).
- **Control** — credential storage (polymorphic: API keys, OAuth2 client
  credentials, bearer, basic auth).
- **Admin** — operator account, permissions / access grants, async jobs, append-only
  audit log, execution telemetry.

Plus the supporting surfaces that make the above usable:

- **CLI** — `jenticctl` (install / lifecycle) and `jentic` (agent identity + catalog
  + execute).
- **Install / onboarding** — the install wizard, `install.sh`, first-run flow.
- **Web UI** — operator-facing management of the above.
- **Deploy** — Docker, Helm, Terraform, k8s, versioning.
- **Docs** — guides for the above.

An issue is **in scope** when it improves the security, correctness, reliability,
usability, or operability of one of these surfaces for the audience above.

## Explicit non-goals (score fit LOW)

These are the load-bearing part of this doc — they let the harness confidently
score a request as a poor fit rather than guessing.

- **Not an agent framework / LLM orchestrator.** Jentic One executes API calls on
  behalf of agents; it does not build, host, prompt, or reason as the agent.
- **Not a general-purpose API gateway / reverse proxy** for non-agent traffic
  (Kong/NGINX territory). The Broker's job is credential injection for governed
  agent calls, not arbitrary traffic management.
- **Not an iPaaS / workflow-automation builder** (Zapier/n8n). It executes single
  governed calls; it does not own multi-step business-workflow orchestration.
- **Not a secrets manager** in the Vault sense. It stores the credentials it injects,
  but it is not a general org-wide secret store.
- **Not a hosted SaaS.** It is self-hosted and open source; "please host this for me"
  is a commercial-support conversation, not a product issue. (See `SUPPORT.md`.)
- **Not running in the same trust boundary as the agent** by design — requests to
  co-locate the agent and broker in one process contradict the security model.

TODO(product): confirm / extend this list — non-goals are the highest-value input
to fit scoring and the most likely to be wrong when inferred.

## Product principles (tie-breakers for fit & priority)

1. **Secrets never leak.** Anything that risks exposing a credential to the agent,
   the network, or logs is high priority regardless of size.
2. **Secure and auditable by default.** Default-deny permissions; append-only audit.
3. **Self-hostable and operable.** It must be installable and operable by a small
   team without Jentic's help.
4. **Public Beta honesty.** Breaking changes are acceptable pre-1.0; polish that
   assumes stability is lower priority than correctness and security.

TODO(product): confirm these principles and their ordering — the harness uses them
as tie-breakers when fit is borderline.

## How the harness should use this doc

- **fit:high** — squarely improves an in-scope surface for the audience, aligned
  with a principle.
- **fit:med** — plausibly in scope but adjacent, speculative, or needs product
  judgment (e.g. a new surface, a large new capability).
- **fit:low** — matches a non-goal, or is for an audience/use-case this product
  does not serve.

Fit is about *"should this exist in Jentic One?"* — it is **independent** of
`feasibility` (can we build it) and `severity` (how much it hurts today).
