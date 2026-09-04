# Jentic One documentation

What Jentic One is and how it works: [repository README](../README.md).

## Where do I go?

- **Trying it out** → [README quickstart](../README.md#quickstart).
- **Installing and running it** (admin) → [Install](#install), then
  [Secure](#secure) for the security posture.
- **Keeping it healthy** (upgrades, backups, monitoring) → [Operate](#operate).
- **Using it** (connecting agents, brokering calls) → [Use](#use).
- **Understanding how it's built** (surfaces, processes, data model) →
  [Understand](#understand).
- **Developing on it** (spinning it up from source, extending the code) →
  [Develop](#develop).
- **You are an AI agent executing a task** → [agent runbooks](agent/README.md)
  and [`llms.txt`](../llms.txt).

## Install

- [Installation overview](installation/README.md) — artifacts, verification, air-gapped transfer
- [Platform support](installation/platform-support.md) — what runs on Linux / macOS / Windows / WSL2
- [Windows](installation/windows.md) — the WSL2 + native-CLI path, step by step
- [CLI binaries](installation/cli.md) — `jentic` / `jenticctl` download matrix and verification
- [Docker](installation/docker.md)
- [docker-compose](installation/docker-compose.md)
- [systemd](installation/systemd.md)
- [Helm](installation/helm.md)
- [AWS Marketplace](installation/aws-marketplace.md)
- Agent runbook: [install](agent/install.md) — the same install, written for an AI agent to execute

## Operate

- [Operating Jentic One](operations/README.md) — the day-2 map
- [Monitoring & logs](operations/monitoring.md) — health endpoints, log sinks, executions and the audit trail, metrics
- [Upgrades](operations/upgrades.md) — the version/migration contract, per-install pointers
- [Backup & restore](operations/backup-restore.md) — what a restorable backup must contain
- [Troubleshooting](operations/troubleshooting.md) — symptom index for humans, backed by the [agent failure catalogue](agent/troubleshoot.md)
- Agent runbook: [operate](agent/operate.md) — status/logs/upgrade/uninstall as executable steps

## Use

Get going:

- [First brokered call](guides/first-call.md) — the six-step route map

Connect agents:

- [Connect an agent](guides/connecting-agents.md) — the four integration paths
- [Run coding agents in isolation](guides/local-agent.md) — the `jentic run` sandbox
- [Serve MCP over HTTP](guides/mcp-http-endpoint.md) — the optional hosted `/mcp` endpoint

Operate the catalog:

- [Fix a spec with an overlay](guides/overlays.md) — patching imported API descriptions
- [How credential resolution works](guides/credentials-and-toolkits.md) — how secrets stay server-side, encrypted, and audited

Integrate apps:

- [Register an OAuth client](guides/oauth-clients.md) — third-party apps authenticating users
- [CLI README](../cli/README.md) — the full `jentic` / `jenticctl` command surface

Agent runbook: [use](agent/use.md) — discover → request access → execute, as executable steps.

## Secure

- [Deploying Jentic One securely](security/security.md) — threat model and the deployment-tier ladder
- [Same-host setups](security/same-host/README.md) — the threat model when an agent shares a machine with the instance, and the menu of isolation options (including `jentic run`)
- Agent runbook: [harden](agent/harden.md) — posture checks as executable steps

## Understand

- [Architecture overview](architecture/README.md) — the five surfaces, two processes, three databases, one diagram
- [Surfaces and layering](architecture/surfaces-and-layering.md) — how the code is organized and the import rules that keep it that way
- [Composition and processes](architecture/composition-and-processes.md) — boot sequence, `JENTIC__APPS`, deployment topologies, background work
- [Broker execution](architecture/broker-execution.md) — the life of a brokered call: pipeline, resilience, egress, credential injection
- [Data model](architecture/data-model.md) — three databases, no cross-database foreign keys, immutable revisions
- [Identity and authorization](architecture/identity-and-authorization.md) — actors, tokens, scopes, and the default-deny chain

## Develop

- [Local development setup](development/local-setup.md)
- [Extending Jentic One](development/extending-jentic-one.md)
- [Context and configuration](development/context-and-config.md)
- [Releasing](development/releasing.md)
- [Product scope](development/product-scope.md) — the product-fit rubric the issue-intake harness scores against
- [deploy/README.md](../deploy/README.md) — build architecture: images, charts, Terraform, multi-arch
- [Helm charts & local cluster](../deploy/helm/README.md) — chart docs, kind workflow, smoke tests, observability stack
- [AWS Marketplace publishing](development/marketplace-publishing.md) — the seller/maintainer side of the listing

## Reference (generated — never hand-edit)

- [Configuration](reference/config.md) — every config key, default, and env var (`make config-reference`)
- [Endpoints & scopes](reference/endpoints.md) — every HTTP route and its required scope (`make endpoints`)
- [reference/README.md](reference/README.md) — how the generated material works
