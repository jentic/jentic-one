# Product Scope

> **Purpose.** This document gives the issue-intake harness (see
> [`.harness/ISSUE_INTAKE_STANDARDS.md`](../.harness/ISSUE_INTAKE_STANDARDS.md)) a
> rubric for scoring an issue's **product fit** (`fit:high/med/low`). It is grounded
> only in **public** sources — `README.md`, `SECURITY.md`, `SUPPORT.md`,
> `docs/security/hardening.md`, and the code in this repo. It deliberately does **not**
> restate internal roadmap/prioritization detail; where a finer-grained or
> forward-looking judgment is needed, defer to the maintainers (label `needs-human`)
> rather than guessing.

## One-line definition

Jentic One is a **self-hosted gateway for secure third-party API execution by AI
agents**: you register the APIs an agent may use, store the credentials once, and
the agent calls out through a credential-injecting Broker so **secrets never leave
your infrastructure and never reach the agent** (`README.md`, `SECURITY.md`).

## Who it's for

**Primary — the self-hosting operator / small team** who installs, configures,
secures, and operates a Jentic One instance (`SECURITY.md` operator guidance;
`SUPPORT.md` "community-supported for self-hosted deployments"). It must be
installable and operable by a small team without Jentic's help.

Deployment personas the public docs call out (`docs/security/hardening.md`):

- **Local coding-agent developer** — running Jentic One next to a local coding agent
  (e.g. Claude Code, Cursor) for dev / trying it out.
- **Provisioned agent/service in a private network / VPC** — the production shape: an
  agent inside the same VPC calls Jentic One over private DNS ("recommended for real
  use").
- **Issue filer / contributor** — including AI agents filing issues (this harness's
  own audience; see `CONTRIBUTING.md`).

**Commercial-support boundary:** self-hosting is free and is the real product;
"host it for me / run it at scale / SLAs / managed edition" is a commercial
conversation, **not** a product issue (`README.md` Enterprise section, `SUPPORT.md`).

## What's in scope (the product is about this)

The runtime surfaces (from the public code / `README.md`):

- **Broker** — stateless credential-injecting HTTP proxy; the data plane. One
  upstream call per execution (single-call interceptor pipeline), run as its own
  service.
- **Registry** — catalogue of registered APIs (immutable revisions, operations,
  security schemes, servers); **APIs only**.
- **Control** — credential storage + toolkit/credential bindings + access-request
  lifecycle.
- **Admin** — operator accounts, role-based permissions/access grants, async jobs,
  append-only audit log, execution telemetry; serves the operator UI.
- **Auth** — agent self-registration, token minting, OAuth client, service accounts,
  and identity/`/me` discovery.

Plus the supporting surfaces that make the above usable: **shared** infra, the
**CLI** (`jenticctl` lifecycle + `jentic` agent/catalog/execute), **install /
onboarding**, the **Web UI**, **deploy** (Docker, Helm, Terraform, k8s), and
**docs**.

Supported specifics worth knowing (so the harness doesn't mis-score them as out of
scope):

- **Credential schemes:** API key (header/query/cookie), basic, static bearer,
  session token, and OAuth2 (client-credentials, authorization-code, implicit), plus
  no-auth (`shared/models/credentials.py`).
- **Database backends:** Postgres **and** SQLite — SQLite is a supported *production*
  target, not dev-only (`shared/db/backends/sqlite.py`).
- **ML/embeddings** exist but are **registry-search-only**; core surfaces don't use
  them (`tests/arch/test_no_ml_in_core_surfaces.py`).

An issue is **in scope** when it improves the security, correctness, reliability,
usability, or operability of one of these surfaces for the audience above.

## Explicit non-goals (score fit LOW)

- **Not a workflow / multi-step orchestration engine (iPaaS).** The Registry
  catalogues **individual APIs**, and the Broker does **single-call proxying** — one
  governed upstream call per execution, not branching multi-step workflows. An agent
  that needs multi-step orchestration composes broker calls itself.
- **Not co-located with the agent for _real / high-value credentials_.** *(Grounded —
  security model.)* The "credentials never leave the data plane" guarantee does not
  hold when the agent runs as the same OS user / same host as the broker
  (`SECURITY.md`, `docs/security/hardening.md`). **Nuance:** same-host use *is*
  supported for trying it out / non-real credentials — so a local/dev-mode request is
  **not** automatically out of scope; only "use real credentials in the agent's trust
  boundary" is.
- **Not a general-purpose API gateway for _non-agent_ traffic** (Kong/NGINX). The
  Broker governs credential-injecting **agent** calls, not arbitrary traffic
  management.
- **Not an agent framework / agent loop.** Jentic One executes and governs API calls
  on behalf of an agent harness; it does not build, host, prompt, or reason as the
  agent (no agent-reasoning path exists in the core surfaces).
- **Not a general secrets manager (Vault-sense).** It stores the credentials it
  injects; it is not an org-wide secret store.
- **Not a hosted SaaS.** Self-hosted and open source; "please host this for me" is
  commercial support (`README.md`, `SUPPORT.md`).

> Some new-capability requests (e.g. an LLM-API proxy, webhooks, additional protocol
> modules) are **forward-looking product calls**, not clear yes/no scope questions —
> the harness should not decide them from this doc. Score them `fit:med` and escalate
> `needs-human`.

## Product principles (tie-breakers for fit & priority)

Ordered by how strongly the public docs emphasize each.

1. **Secrets never leave / never reach the agent.** The most-repeated value across
   the tagline, `README.md`, `SECURITY.md`, and the hardening threat model, and
   enforced in code (credentials "never returned after create"; central redaction).
   Anything risking credential exposure is at least `severity:major` regardless of
   size.
2. **Secure & auditable by default.** Default-deny permissions (a rule-less binding
   blocks everything); append-only audit log; operator-supplied encryption keyset
   required (`SECURITY.md`, `control/web/schemas/toolkits.py`).
3. **Self-hostable & operable by a small team.** One-command install; tiered
   self-serve hardening path (`README.md`, `docs/security/hardening.md`).
4. **Telemetry opt-in / off by default / closed-schema; observability self-hosted.**
   No telemetry unless explicitly enabled; the event schema structurally can't carry
   PII (`SECURITY.md`, `tests/arch/test_telemetry_no_pii.py`).
5. **Public-Beta honesty.** Pre-1.0; breaking changes acceptable; not recommended for
   production yet — correctness/security outrank polish that assumes stability
   (`README.md`).

## How the harness should use this doc

- **fit:high** — improves the security, correctness, or operability of a core surface
  (broker / registry / control / admin / auth) or the CLI/UI/deploy that serve them,
  for the self-hosting operator. **Security and credential-safety issues are high fit
  regardless of size.**
- **fit:med** — plausibly in scope but adjacent, speculative, a large new capability,
  or a forward-looking product call this doc can't settle → pair with `needs-human`.
- **fit:low** — matches a non-goal above (workflow orchestration; real credentials
  inside the agent's trust boundary; non-agent gateway traffic; hosted-SaaS request).

Fit is about *"should this exist in Jentic One?"* — **independent** of `feasibility`
(can we build it) and `severity` (how much it hurts today).

> **Note.** This rubric is intentionally coarse and public-safe. Finer prioritization
> (release sequencing, internal roadmap ordering) is a maintainer decision and is not
> encoded here — when fit depends on it, escalate `needs-human`.
