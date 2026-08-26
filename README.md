<p align="center">
  <img src="docs/assets/jentic-logo.svg" alt="Jentic One" width="420">
</p>

<p align="center">
  <strong>A self-hosted execution layer for AI agents.</strong><br>
  Connect an agent to any API you need, and enforce exactly what it is allowed to call.
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="docs/guides/first-call.md">First brokered call</a> ·
  <a href="https://github.com/jentic/jentic-public-apis">API Directory</a> ·
  <a href="docs/security/security.md">Security</a> ·
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
Register the APIs an agent may use, store the credentials once, and the agent makes its calls through the Broker. The Broker checks the agent's permissions, attaches the credential at execution time, and writes an audit record. Your agent never sees your keys.

## Quickstart

### Self-hosted (Build local)

```bash
# Build and start service
git clone https://github.com/jentic/jentic-one.git && cd jentic-one
make install   # install dependencies and git hooks
make dev       # idempotent local bring-up: fixtures + migrations + UI, then run the app
open http://127.0.0.1:8000/app/setup

# Build CLI's
cd cli && make build
./jenticctl    # Jentic CTL (Control, Admin of the System)
./jentic       # Jentic CLI (search, inspect, execute - agent entrypoint)
```

### Self-hosted (Guided installation)

```bash
curl -fsSL https://raw.githubusercontent.com/jentic/jentic-one/main/tools/install.sh | sh
```

### Self-hosted(Docker)

```bash
# Download the Jentic One App and Broker
docker pull ghcr.io/jentic/jentic-one-app:latest

# Minimal config needed (all options: docs/reference/config.md)
cat > jentic-one.yaml <<EOF
databases:
  registry: {backend: sqlite, path: /data/registry.db, schema_name: registry}
  control:  {backend: sqlite, path: /data/control.db,  schema_name: control}
  admin:    {backend: sqlite, path: /data/admin.db,    schema_name: admin}
server: {host: 0.0.0.0, port: 8000}
credentials:
  encryption:
    active_id: v1
    entries:
      - id: v1
        material: "$(openssl rand -base64 32)"
EOF

# Initialise the databases (also the upgrade step — re-run it on a new image)
docker run --rm \
  -v jentic-data:/data \
  -v "$PWD/jentic-one.yaml":/etc/jentic/jentic-one.yaml:ro \
  -e JENTIC_CONFIG_FILE=/etc/jentic/jentic-one.yaml \
  ghcr.io/jentic/jentic-one-app:latest python -m jentic_one.migrations.run

# Start the Control Plane
docker run -d --name jentic-app \
  -v jentic-data:/data \
  -v "$PWD/jentic-one.yaml":/etc/jentic/jentic-one.yaml:ro \
  -e JENTIC_CONFIG_FILE=/etc/jentic/jentic-one.yaml \
  -p 127.0.0.1:8000:8000 ghcr.io/jentic/jentic-one-app:latest

# Start the Broker
docker run -d --name jentic-broker -v jentic-data:/data \
  -v "$PWD/jentic-one.yaml":/etc/jentic/jentic-one.yaml:ro \
  -e JENTIC_CONFIG_FILE=/etc/jentic/jentic-one.yaml -e JENTIC__APPS=broker \
  -p 127.0.0.1:8100:8000 ghcr.io/jentic/jentic-one-app:latest

# Open Web UI and create admin account
open http://127.0.0.1:8000/app/setup
```

[More information on Local Installations](docs/development/local-setup.md).

### Managed Install

AWS Marketplace (*coming soon*)
For Commercial use get in touch [jentic.com/contact](https://jentic.com/contact).

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

## Why we built Jentic One

Agents are getting API access the worst way we know how: keys pasted into env vars,
MCP configs, and dotfiles on every machine an agent runs on. Anything in the agent's
context — a prompt injection, a poisoned tool description, a plain bug — can read
those keys and exfiltrate them. And once an agent holds the key itself, there is no
scoping it to *this* endpoint, no record of what it called, and no way to revoke one
agent without rotating the key everywhere.

We think the fix is structural, not better prompting: the agent should never hold the
credential at all. In Jentic One the key is stored once, encrypted, on your
infrastructure; the agent gets an identity instead. Every call goes through the Broker,
which checks that agent's permissions, injects the credential after the check, and
writes an audit record. A compromised agent can only make the calls it was allowed
to make anyway — and you can see every one of them, and cut that one agent off
without touching the key.

## Documentation

The full index is at [docs/README.md](docs/README.md).

- [Installation](docs/installation/quickstart.md) — [Docker](docs/installation/docker.md), [systemd](docs/installation/systemd.md), [Helm](docs/installation/helm.md), [AWS](docs/installation/cloud/aws.md)
- [First brokered call](docs/guides/first-call.md) — from a running instance to a real API response
- [CLI](cli/README.md) — `jentic` (agent) and `jenticctl` (operator)
- [Deploying securely](docs/security/security.md) — read before pointing an instance at a real credential
- [Configuration reference](docs/reference/config.md) — every config key, default, and env var
- [Endpoint & scope reference](docs/reference/endpoints.md) — every HTTP route and who may call it
- [Local development](docs/development/local-setup.md) — running from a source checkout

Agents can read [llms.txt](llms.txt) for a machine-oriented map of the project.

## Contributing

Commit messages follow Conventional Commits, and `make check` must pass before opening a pull request. [CONTRIBUTING.md](CONTRIBUTING.md) has the full workflow, the
[open issues](https://github.com/jentic/jentic-one/issues) list where help is wanted, and the Jentic [Code of Conduct](https://github.com/jentic/.github/blob/main/CODE_OF_CONDUCT.md) applies.

Use [Discussions](https://github.com/jentic/jentic-one/discussions) for questions and proposals.

## Links

[Jentic One](https://jentic.com/jentic-one)
[API Directory](https://github.com/jentic/jentic-public-apis)

## License

Jentic One is licensed under the [Apache 2.0](LICENSE).
