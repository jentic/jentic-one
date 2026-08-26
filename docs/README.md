# Jentic One documentation

Jentic One is a self-hosted execution layer for AI agents: register the APIs
an agent may use, store each credential once, and the agent calls out through
a credential-injecting Broker — it never sees your keys. The
[repository README](../README.md) has the architecture and component overview;
this page maps the docs.

## Install it

Start at the [installation overview](installation/quickstart.md) — the
released artifacts, and a guide per deployment shape:
[Docker](installation/docker.md) · [systemd](installation/systemd.md) ·
[Helm](installation/helm.md) · [AWS](installation/cloud/aws.md). Building and
running from a source checkout is covered under
[development](development/local-setup.md) instead.

## Use it

| Guide | Covers |
| ----- | ------ |
| [First brokered call](guides/first-call.md) | From a running instance to a response from a real API, in six steps |
| [Credentials and toolkits](guides/credentials-and-toolkits.md) | How a stored credential relates to APIs and toolkits, and the one-active-credential invariant |
| [Overlays](guides/overlays.md) | Correcting an imported API description without editing the original |
| [Local coding agents](guides/local-agent.md) | Running Claude Code, Codex, Cursor, or Hermes isolated with `jentic run` |
| [Cloud vs self-hosted](guides/cloud-vs-self-hosted.md) | How Jentic One differs from the hosted Jentic platform, and running both without cross-talk |

The full `jenticctl`/`jentic` command surface is in the
[CLI README](../cli/README.md).

## Secure it

Read [Deploying Jentic One securely](security/hardening.md) before pointing an
instance at a real credential — the threat model and the deployment-posture
ladder. The design rationale behind the local-agent sandbox lives in
[security/local-agent/](security/local-agent/README.md).

## Develop and extend it

| Guide | Covers |
| ----- | ------ |
| [Local development setup](development/local-setup.md) | `make dev` — the one-command local bring-up from source |
| [Extending Jentic One](development/extending-jentic-one.md) | The backward-compatible seams for injecting implementations without editing core code |
| [Context and configuration](development/context-and-config.md) | The `jentic_one.shared` config and context system |
| [Releasing](development/releasing.md) | The maintainer runbook for cutting a release |
| [Product scope](development/product-scope.md) | The product-fit rubric used by issue intake |

Build and deployment tooling (images, charts, Terraform, observability) is
documented in [deploy/README.md](../deploy/README.md).

## Reference

[reference/](reference/README.md) holds **generated** material — the endpoint
and scope reference (`endpoints.md` / `endpoints.json`), regenerated with
`make endpoints` and drift-guarded in CI. Never hand-edit it. A running
deployment also serves interactive references at `/docs` and `/redoc`.

`assets/` holds the images used by these docs and the repository README.
