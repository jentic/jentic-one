<p align="center">
  <img src="docs/assets/jentic-logo.svg" alt="Jentic One" width="420">
</p>

<p align="center">
  <strong>A self-hosted execution layer for AI agents.</strong><br>
  Connect an agent to any API you need, and enforce exactly what it is allowed to call.
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="docs/README.md">Docs</a> ·
  <a href="docs/security/README.md">Security</a> ·
  <a href="https://github.com/jentic/jentic-public-apis">API Directory</a> ·
  <a href="https://github.com/jentic/jentic-one/discussions">Discussions</a>
</p>

<p align="center">
  <a href="https://github.com/jentic/jentic-one/actions/workflows/ci.yml"><img src="https://github.com/jentic/jentic-one/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-A3CACC.svg" alt="License: Apache 2.0"></a>
  <a href="https://www.opensourcealternatives.to"><img src="https://www.opensourcealternatives.to/badge-osa.svg" alt="Listed on Open Source Alternatives"></a>
</p>

> [!NOTE]
> **Public beta.** Schemas and CLI commands can change between 0.x releases — pin a version if
> you need stability.

# What it is

<p align="center">
  <img src="docs/assets/how-it-works.svg" alt="An agent sends POST /v1/invoices to Jentic One, which checks the permission, injects the stored credential and forwards the call to the Stripe API; the API returns 201 and the call is logged. The agent then sends POST /v1/refunds; the permission check denies it by rule, the request never leaves Jentic One, the agent gets a 403, and the denial is logged. The agent never sees the API key." width="100%">
</p>

Giving an agent API access normally means giving it an API key. Jentic One removes that step:
you store each credential once, on your own infrastructure, and the agent calls APIs through the
**Broker**. The Broker checks what the agent is allowed to do, attaches the credential, and records
the call. **The agent never sees your keys** — and you can cut off one agent without rotating anything.

Agents connect through the `jentic` CLI, a generated skill, an MCP server, or plain HTTP.

**Who it's for:** developers giving a coding agent (Claude Code, Codex, Cursor, Cline, …) real API
access; teams running agents inside a private network; anyone who has to pass a security review
before an agent touches production credentials.

# Quickstart

Sets up a self-hosted Jentic One instance on your machine with Docker. Run your agents in a separate environment from the
stack (a different machine, VM or user), and connect them over HTTP or MCP. An encryption key for
your credentials is written to `~/.jentic/enc-key` by default.

- For Windows, use the [Windows guide](docs/installation/windows.md). For other deployment methods, see
  [Alternative deployments](#alternative-deployments).
- By default, the quickstart enables anonymous telemetry. For more information and how to opt out,
  see [Telemetry](docs/reference/config.md#telemetry).
- Something failed? See [Troubleshooting](docs/operations/troubleshooting.md).

## Start the instance

```bash
# Download the quickstart compose file
curl -fsSLO https://raw.githubusercontent.com/jentic/jentic-one/main/deploy/docker/quickstart/docker-compose.yaml

# Start the stack. Open the webapp at http://127.0.0.1:8000 (or your configured url) once it returns
docker compose up -d --wait
```

## Connect your agent

Run these on the machine where your agent runs. For MCP setup, see [Connecting agents over MCP](docs/guides/mcp-http-endpoint.md).

### Install the CLIs

```bash
brew install --cask jentic/tap/jentic    # macOS / Linux
winget install Jentic.Jentic             # Windows

# Or install directly from GitHub
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/clis/install.sh | sh
```

### Register your agent

```bash
JENTIC_ONE_URL=http://127.0.0.1:8000   # where your Jentic One web app is running
jentic setup --url "$JENTIC_ONE_URL"   # registers the agent and installs its skill; approve it in the UI
```

Next: [make your first brokered call](docs/guides/first-call.md).

## Alternative deployments

| If you want… | Go to |
| --- | --- |
| Production on Docker (Postgres, pinned image, TLS) | [Docker](docs/installation/docker.md) · [docker-compose](docs/installation/docker-compose.md) · [systemd](docs/installation/systemd.md) |
| Kubernetes | [Helm](docs/installation/helm.md) |
| Buy it on AWS Marketplace (runs on your EKS) | [AWS Marketplace](docs/installation/aws-marketplace.md) |
| A trial without compose (plain `docker run`, SQLite) | [Trial setup](docs/installation/docker.md#trial-setup-on-sqlite) |
| From source, to develop on it | [Local setup](docs/development/local-setup.md) |
| CLI without a package manager | [Installing the CLIs](docs/installation/cli.md) |

# How it works

<p align="center">
  <img src="docs/assets/architecture.svg" alt="Two units over one database. The operator uses App, the control plane, with the Registry, Control, Admin and Auth surfaces: register APIs, store credentials, approve agents. The agent registers through App and calls the Broker, the data plane, which checks permissions, injects the credential and forwards the call to public or private APIs. App writes rules and credentials to PostgreSQL or SQLite (three databases: registry, control and admin); the Broker reads them and records every call." width="100%">
</p>

Jentic One runs as two units over one database. **App** (the control plane) is where you register
APIs, store credentials and approve agents. **Broker** (the data plane) is what agents call: it checks
permissions, injects the credential after the check, forwards the request and writes an execution
record. More in [Architecture](docs/architecture/README.md).

One boundary to know: the Broker protects credentials from agents running as *other* OS users or on
other machines. An agent running as the *same* OS user as its CLI state can read that state — see
[same-host setups](docs/security/same-host/README.md).

# Documentation

| I want to… | Start here |
| --- | --- |
| Install and run it | [Installation](docs/installation/README.md) |
| Make my first API call through it | [First brokered call](docs/guides/first-call.md) |
| Connect my agent | [Connecting agents](docs/guides/connecting-agents.md) |
| Deploy it securely | [Security](docs/security/README.md) |
| Keep it running (upgrades, backups, monitoring) | [Operations](docs/operations/README.md) |
| Understand how it's built | [Architecture](docs/architecture/README.md) |
| Look up a config key, endpoint or CLI flag | [Reference](docs/reference/README.md) |
| Contribute | [CONTRIBUTING.md](CONTRIBUTING.md) · [Local setup](docs/development/local-setup.md) |

Everything else is in the [documentation index](docs/README.md). AI agents can start from
[`llms.txt`](llms.txt).

# Community

Questions and ideas go to [Discussions](https://github.com/jentic/jentic-one/discussions);
bugs and contributions to the [issues](https://github.com/jentic/jentic-one/issues).
Security reports: [SECURITY.md](SECURITY.md). Support options: [SUPPORT.md](SUPPORT.md).
For commercial use: [jentic.com/contact](https://jentic.com/contact).

# License

[Apache 2.0](LICENSE); third-party attributions in [NOTICE](NOTICE).
