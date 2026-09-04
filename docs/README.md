# Jentic One documentation

What Jentic One is and how it works: [repository README](../README.md).

## Where do I go?

- **Installing and running it** (admin) → [Install](#install), then
  [Secure](#secure) for the security posture.
- **Using it** (connecting agents, brokering calls) → [Use](#use).
- **Developing on it** (spinning it up from source, extending the code) →
  [Develop](#develop).
- **You are an AI agent executing a task** → [agent runbooks](agent/README.md)
  and [`llms.txt`](../llms.txt).

Each section gets more detailed the deeper you go: this index → a section
overview → one scoped guide → [generated reference](#reference-generated--never-hand-edit).

## Install

- [Installation overview](installation/quickstart.md) — artifacts, verification, air-gapped transfer
- [Platform support](installation/platform-support.md) — what runs on Linux / macOS / Windows / WSL2
- [Agent runbooks](agent/README.md) — install/operate/use/harden guides written for an AI agent to execute
- [CLI binaries](installation/cli.md) — `jentic` / `jenticctl` download matrix and verification
- [Docker](installation/docker.md)
- [systemd](installation/systemd.md)
- [Helm](installation/helm.md)
- [AWS Marketplace](installation/aws-marketplace.md)

## Use

- [First brokered call](guides/first-call.md)
- [Credentials and toolkits](guides/credentials-and-toolkits.md)
- [Overlays](guides/overlays.md)
- [Local coding agents](guides/local-agent.md)
- [Cloud vs self-hosted](guides/cloud-vs-self-hosted.md)
- [OAuth clients](guides/oauth-clients.md) — connecting OAuth-secured APIs
- [MCP over HTTP](guides/mcp-http-endpoint.md) — the optional hosted `/mcp` endpoint
- [CLI README](../cli/README.md) — the full `jentic` / `jenticctl` command surface

## Secure

- [Deploying Jentic One securely](security/security.md)
- [Local-agent sandbox design](security/local-agent/README.md)

## Develop

- [Local development setup](development/local-setup.md)
- [Extending Jentic One](development/extending-jentic-one.md)
- [Context and configuration](development/context-and-config.md)
- [Releasing](development/releasing.md)
- [Product scope](development/product-scope.md)
- [deploy/README.md](../deploy/README.md) — images, charts, Terraform, observability

## Reference (generated — never hand-edit)

- [Configuration](reference/config.md) — every config key, default, and env var (`make config-reference`)
- [Endpoints & scopes](reference/endpoints.md) — every HTTP route and its required scope (`make endpoints`)
- [reference/README.md](reference/README.md) — how the generated material works
