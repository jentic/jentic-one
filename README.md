<p align="center">
  <img src="docs/assets/jentic-logo.svg" alt="Jentic One" width="420">
</p>

<p align="center">
  <strong>A self-hosted execution layer for AI agents.</strong><br>
  Connect an agent to any API you need, and enforce exactly what it is allowed to call.
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="docs/quickstart.md">First brokered call</a> ·
  <a href="https://github.com/jentic/jentic-public-apis">API Directory</a> ·
  <a href="docs/security/hardening.md">Security</a> ·
  <a href="https://github.com/jentic/jentic-one/discussions">Discussions</a>
</p>

<p align="center">
  <a href="https://github.com/jentic/jentic-one/actions/workflows/ci.yml"><img src="https://github.com/jentic/jentic-one/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-A3CACC.svg" alt="License: Apache 2.0"></a>
  <img src="https://img.shields.io/badge/Python-3.12-68BAEC.svg?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Go-1.26-5EDEB9.svg?logo=go&logoColor=white" alt="Go 1.26">
  <img src="https://img.shields.io/badge/PostgreSQL-16-A3CACC.svg?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/SQLite-3-A3CACC.svg?logo=sqlite&logoColor=white" alt="SQLite">
  <br>
  <img src="https://img.shields.io/badge/lint-ruff-FDBD79.svg?logo=ruff&logoColor=black" alt="Linted with Ruff">
  <img src="https://img.shields.io/badge/types-mypy_strict-F1E38B.svg" alt="mypy strict">
  <a href="https://www.conventionalcommits.org/"><img src="https://img.shields.io/badge/commits-conventional-EDADAF.svg?logo=conventionalcommits&logoColor=white" alt="Conventional Commits"></a>
 </p>

> [!NOTE]
> **Public Beta.** Schemas and CLI commands can change between 0.x releases. Pin a version if
> you need stability. Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and the
> [open issues](https://github.com/jentic/jentic-one/issues).

## What Jentic One is

<p align="center">
  <img src="docs/assets/how-it-works.gif" alt="Any agent calls Jentic One, which applies default-deny policies, injects credentials at execution time, and logs every call on your own instance, before reaching any public or private API. One call is allowed and returns 201; a second is denied by rule and never leaves the layer." width="100%">
</p>

Giving an agent API access normally means giving it an API key. Jentic One removes that step.
Register the APIs an agent may use, store the credentials once, and the agent makes its calls
through the Broker. The Broker checks the agent's permissions, attaches the credential at
execution time, and writes an audit record. Your agent never sees your keys.

Self-hosted and Apache-2.0. The open-source build is the real thing, not a trial.

**Who it's for**

- Developers running a coding agent locally — Claude Code, Codex, Cursor, OpenClaw, Cline, or
  one you built — that needs access to real APIs.
- Small teams running an agent in a private network or VPC, with the agent calling Jentic One
  over private DNS.
- Anyone who needs to pass a security review before an agent touches production credentials.

**What it is not**

| Not | Reason |
| --- | ------ |
| A workflow or orchestration engine | The Broker makes one governed upstream call per execution. An agent that needs multi-step orchestration composes calls itself. |
| A secrets manager | It stores and injects the credentials it brokers. It does not replace Vault for your wider infrastructure. |
| A hosted service | You run it. No tier exists in which Jentic holds your keys. |
| Safe to run as the same OS user as your agent, with real credentials | The network guarantee holds. The same-OS-user guarantee does not. See [Security](#security--telemetry). |

## Why self-hosted

Most tools in this category are hosted: the credentials live in the vendor's infrastructure.
Jentic One runs on infrastructure you control. Credentials are encrypted at rest in your own
database and are decrypted only inside the Broker, at execution time.

Jentic One exposes no MCP endpoint. An MCP server running beside an agent gives that agent a
credential-bearing surface to call, which is what the Broker exists to avoid. Agents integrate
through the `jentic` CLI, a generated skill, or plain HTTP.

## Quickstart

**Prerequisites:** `git`, [`uv`](https://docs.astral.sh/uv/), and Docker running. Node is
required only to build the UI from source.

### Option A — signed release binary

Download the binary for your platform from the
[latest release](https://github.com/jentic/jentic-one/releases/latest), verify it against
`checksums.txt` (signed: `checksums.txt.sig`, `checksums.txt.pem`), then run
`jenticctl install`. Each release also ships an SBOM. Use this path where the binary must be
verified before it runs.

### Option B — bootstrap script

```bash
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh
```

The script checks for a suitable Go toolchain and downloads Go 1.26.2 if it does not find one,
clones this repository, builds the two CLI binaries (`jenticctl` and `jentic`), then runs
`jenticctl install`, which configures and starts a local stack. Set `JENTIC_NO_INSTALL=1` to
stop after the binaries are installed.

### Option C — from source

```bash
make install   # install dependencies and git hooks
make dev       # idempotent local bring-up: fixtures + migrations + UI, then run the app
```

`make dev` is the one-command local flow and is safe to re-run, including after a reboot. See
[Local development setup](docs/development/local-setup.md).

## First brokered call

Six steps from a running instance to a response from a real API.

1. **Create your admin account** at `/setup` (the wizard opens this automatically, or run
   `jenticctl setup` to do it from the terminal).
2. **Import an API** from the [Jentic API Directory](https://github.com/jentic/jentic-public-apis)
   (e.g. `httpbin.org`, used in step 6), or upload your own specification.
3. **Store a credential** for that API. It is encrypted at rest and is never returned to a
   caller.
4. **Register the agent:** `jentic register` (add `--base-url <URL>` when the agent runs on a
   different machine). Registration waits for an operator to approve the
   agent. On a single-operator install, approve it in the UI and the command completes.
5. **Grant access** by binding the agent to a toolkit. A rule-less binding blocks everything;
   the default is deny.
6. **Make the call:** `jentic execute GET:https://httpbin.org/get --json` — the operation's
   full URL, as returned by `jentic search`/`jentic inspect`.

Full walkthrough: [docs/quickstart.md](docs/quickstart.md). To have a coding agent install and
register itself, see [AGENTS.md](AGENTS.md); [llms.txt](llms.txt) is the machine-readable index
of these docs for assistants evaluating the project.

## How it works

Jentic One handles secure third-party API execution for agents. It deploys as two peer units
above a shared database. **App** is the control plane and contains the Registry, Control and
Admin surfaces. **Broker** is the data plane. Configuration happens through App; the agent
talks only to the Broker.

<p align="center">
  <img src="docs/assets/architecture.png" alt="Two peer units above one database. App is the control plane, containing the Registry, Control and Admin surfaces, and is where the operator configures the instance. Broker is the data plane: a stateless credential-injecting HTTP proxy, and the only surface that touches a secret. Both sit above PostgreSQL or SQLite with registry, control and admin schemas." width="100%">
</p>

On each call the Broker checks the agent's permissions, attaches the stored credential,
forwards the request, and writes an audit record. The credential is added inside the Broker,
after the permission check, and is never returned to the caller.

## Components

| Component | Responsibility |
| --------- | -------------- |
| **Broker** | A stateless Broker: a credential-injecting HTTP proxy. Receives an HTTP request with the upstream URL as the path, injects the caller's stored credentials, forwards method/headers/body, and returns the upstream response. Secrets never leave the Broker. |
| **Registry** | API specification directory. Stores registered APIs with immutable revisions, operations, security schemes, and server definitions. Owns what APIs are available and at which version. |
| **Control** | Credential storage. Manages polymorphic API credentials (API key in header, query or cookie; basic; static bearer; session token; OAuth2 in client-credentials, authorization-code and implicit flows) used by the Broker at execution time. |
| **Admin** | Permissions, jobs, audit, and execution telemetry. Owns the operator account, role-based access grants, async job lifecycle, append-only audit log, and execution records. |
| **Shared** | Internal infrastructure layer: configuration loading, async database sessions, structured logging, metrics facade, and the multi-surface application factory. |
| **CLI** | Two Go binaries: `jenticctl` onboards and operates the platform (`jenticctl install`), and `jentic` registers agent identities (`jentic register`) and drives the catalog/broker (`jentic catalog`, `jentic execute`). See [`cli/`](cli/README.md). |

Agent identity is per-agent. Each agent registers with its own Ed25519 keypair through dynamic
client registration, and an operator approves it before it can call anything. Revoking one
agent does not affect the others.

Both PostgreSQL and SQLite are supported production backends.

## Does Jentic One replace my API gateway?

No. A gateway manages traffic: routing, rate limits, quotas, transformation. Jentic One governs
which agent may make a given call, which stored credential is attached to it, and what is
recorded afterwards. Both sit on the same request path and address different concerns, so
Jentic One runs alongside an existing gateway.

Jentic One works with any API you can reach, public or private, third-party or your own.
Registering internal services is a supported path: the same credential custody, per-agent
permissions and audit trail apply whether the upstream is Stripe or an API that exists only
inside your network. The public
[Jentic API Directory](https://github.com/jentic/jentic-public-apis) supplies specifications
for APIs that already have one.

## CLI reference

```bash
jenticctl install                                    # interactive wizard: config + install (local venv or Docker)
jentic register                                      # mint an agent identity, then wait for operator approval
jentic catalog search stripe                         # find an API in the directory
jentic execute GET:https://httpbin.org/get --json    # run a call through the Broker with the credential injected
```

Full reference: [`cli/README.md`](cli/README.md).

## Documentation

**Getting to a first call**

| Guide | Covers |
| ----- | ------ |
| [Local development setup](docs/development/local-setup.md) | Running a stack on your machine |
| [Credentials and toolkits](docs/credentials-and-toolkits.md) | Storing a credential and binding an agent to it |
| [Local coding agents](docs/local-agent.md) | Run Claude Code, Codex, Cursor, or Hermes as an isolated Unix user with `jentic run` — flow, examples, grants, and troubleshooting |
| [CLI reference](cli/README.md) | Every `jenticctl` and `jentic` command |

**Running it somewhere real**

| Guide | Covers |
| ----- | ------ |
| [Security hardening](docs/security/hardening.md) | Deployment-tier ladder and production checklist. Read before using real credentials. |
| [Build & deploy](deploy/README.md) | Docker, Helm, Terraform, versioning, kind, observability |
| [Self-hosted containers + external Postgres](deploy/README.md#self-hosted-containers--external-postgres) | Production-shaped deployment without Kubernetes |
| [Cloud vs self-hosted](docs/cloud-vs-self-hosted.md) | How Jentic One differs from the Jentic cloud platform, why there is no MCP endpoint, and how to run both (or migrate) without silent cross-talk |

**Reference**

| Guide | Covers |
| ----- | ------ |
| [Endpoint & scope reference](docs/reference/endpoints.md) | Every HTTP route, its scope, and who may call it |
| [API specs](openapi/) | OpenAPI specifications (broker, control) |
| In-app reference | On a running deployment: `/docs` for interactive Swagger, `/redoc` for the rendered reference. Both generated from code. |

## Security & telemetry

- **Credentials stay local.** Stored credentials are encrypted at rest and are only ever
  decrypted inside the Broker at execution time. They are never returned to callers, logged in
  cleartext, or exposed to the agent.
- **Run Jentic One separately from your agent.** The guarantee above holds on the network path,
  but a process running as the same OS user as Jentic One can read the key and credential
  database directly. For real credentials, do not run Jentic One in the same trust boundary as
  your agent: sandbox the agent, or run Jentic One on a separate host or network. The
  [security hardening guide](docs/security/hardening.md) contains the deployment-tier ladder
  and a production checklist.
- **Access is default-deny.** A rule-less binding blocks everything. Permissions are
  first-match.
- **Telemetry is opt-in and off by default.** Jentic One sends nothing unless anonymous product
  telemetry is explicitly enabled (`telemetry.enabled: true`); an instance whose config omits
  the telemetry block stays silent. When enabled, it sends a small, fixed set of anonymous
  events. Each event is a closed schema — `{id, version, event, actor_type?, tags?, ts}` —
  where `event` and `actor_type` are fixed enums and `tags` are fixed labels, never free text,
  so the payload has no room for credentials, request data, or PII. The OS family
  (`linux`/`darwin`/`windows`/`other`) is sent exactly once, as a tag on the one-time
  `instance_initialized` event. This is enforced in CI by
  [`tests/arch/test_telemetry_no_pii.py`](tests/arch/test_telemetry_no_pii.py).
- **Observability is self-hosted.** Metrics and tracing exporters emit to an
  OpenTelemetry/Prometheus endpoint you configure.
- **Reporting a vulnerability.** Do not open a public issue for security reports. See
  [SECURITY.md](SECURITY.md) for responsible-disclosure instructions.

## Development & testing

Common `make` targets (run `make help` for the full list):

| Target | Description |
| ------ | ----------- |
| `make install` | Full dev setup: sync deps + install git hooks |
| `make dev` | One-command local bring-up (idempotent): fixtures + migrations + UI, then start the app |
| `make check` | Lint, score, secrets audit, unit + arch tests |
| `make fix` | Auto-fix lint issues and reformat code |
| `make test` | Run unit tests |
| `make start-app` | Start the combined app (all surfaces) |

Tests are split into tiers:

- **Unit** — logic with no external services (`make test-unit`).
- **Integration** — database lifecycle against Docker fixtures (`make test-integration`).
- **Architecture** — enforcement of layering and conventions (`make test-arch`).
- **Smoke** — liveness against running services (`make test-smoke`).

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) with a mandatory
scope, enforced repo-wide by a `commit-msg` hook. The architecture tests run against a small
vendored subset of rule facts ([`tests/arch/vendored/`](tests/arch/vendored/)), so a plain clone
requires no additional setup. [CONTRIBUTING.md](CONTRIBUTING.md) covers pointing them at a
fuller set.

## Contributing

Commit messages follow Conventional Commits, and `make check` must pass before opening a pull
request. [CONTRIBUTING.md](CONTRIBUTING.md) has the full workflow, the
[open issues](https://github.com/jentic/jentic-one/issues) list where help is wanted, and the
Jentic [Code of Conduct](https://github.com/jentic/.github/blob/main/CODE_OF_CONDUCT.md)
applies.

Use [Discussions](https://github.com/jentic/jentic-one/discussions) for questions and proposals.

## Migrating from the hosted platform or Jentic Mini

Read [Cloud vs self-hosted](docs/cloud-vs-self-hosted.md) before running Jentic One alongside
the hosted Jentic platform or a self-hosted Jentic Mini instance: the failure mode is silent
wrong answers, not errors. New installations should start with Jentic One.

## Enterprise & commercial support

Jentic One is fully open source (Apache-2.0) and free to self-host. For help operating a
credential broker at scale — security hardening reviews, deployment architecture, SLAs, or a
managed option — contact [jentic.com/contact](https://jentic.com/contact).
[SUPPORT.md](SUPPORT.md) lists community and commercial support options.

## License

Jentic One is licensed under the [Apache 2.0](LICENSE) license, and ships with an explicit
[NOTICE](NOTICE) file containing additional legal notices.
