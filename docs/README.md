# Jentic One documentation

Docs index. What Jentic One is and how it works: [repository README](../README.md).

## Install

- [Installation overview](installation/quickstart.md) — artifacts, verification, air-gapped transfer
- [Docker](installation/docker.md)
- [systemd](installation/systemd.md)
- [Helm](installation/helm.md)
- [AWS](installation/cloud/aws.md)

## Use

- [First brokered call](guides/first-call.md)
- [Credentials and toolkits](guides/credentials-and-toolkits.md)
- [Overlays](guides/overlays.md)
- [Local coding agents](guides/local-agent.md)
- [Cloud vs self-hosted](guides/cloud-vs-self-hosted.md)
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

