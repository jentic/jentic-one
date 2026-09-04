# Installation

Production installs run from released artifacts only: the published container image and the two Go CLI binaries. Nothing is built from source on the target hosts, and once the artifacts are inside your network nothing needs outbound internet access.

## The artifacts

| Artifact | Role | Distribution |
| -------- | ---- | ------------ |
| `ghcr.io/jentic/jentic-one-app` | The backend. One image runs both the **app** (control plane) and the **broker** (data plane) — the surface set is chosen at runtime via `JENTIC__APPS`. | GHCR. Cosign-signed with an SBOM attestation; pin by `@sha256:` digest. [Pull + verify](../../deploy/README.md#the-published-image). |
| `jenticctl` | Operator CLI, for the admin host. | GitHub Releases archive, checksummed and cosign-signed. [Download + verify](cli.md). |
| `jentic` | Agent CLI, for every host inside the network that calls the instance. | Same release archives, same verification — see [Installing the CLIs](cli.md). |

Verify signatures **before** the artifacts cross into a locked-down network —
the verify commands linked above need nothing but the downloaded files and
`cosign`.

## Pick a guide

| Guide | Use when |
| ----- | -------- |
| [Platform support](platform-support.md) | Checking what runs on Linux / macOS / Windows / WSL2 before you start. |
| [Windows](windows.md) | Windows host — WSL2 for the server, native `jentic.exe` for the agent side. |
| [Docker](docker.md) | A container host and an external Postgres. The baseline every other guide builds on. |
| [docker-compose](docker-compose.md) | The Docker deployment as one file — migrate, app, broker, health checks. |
| [systemd](systemd.md) | The same two containers, supervised by systemd on a Linux host. |
| [Helm](helm.md) | Kubernetes. |
| [AWS Marketplace](aws-marketplace.md) | Buying and running the listed product on EKS — prerequisites, zero-touch install, license-check behaviour. |

## Air-gapped transfer

- **Image:** on a connected machine, `docker pull` by digest, verify, then
  `docker save -o jentic-one-app.tar <image>`; transfer the tarball and
  `docker load -i` it inside the network (or push it to your internal
  registry).
- **Binaries:** transfer the release archives together with `checksums.txt`,
  `checksums.txt.sig`, and `checksums.txt.pem`, so the verification can be
  repeated inside the network.
- **Helm chart:** not published to a registry yet — vendor
  [`deploy/helm/jentic-one/`](../../deploy/helm/README.md) from a checkout of this
  repository at the release tag.

## After installing

Register each agent host against the instance (`jentic register --url … --broker-url …`,
see the [CLI README](../../cli/README.md#usage)), then make the
[first brokered call](../guides/first-call.md). Before pointing anything at a real
credential, read the [security hardening guide](../security/security.md). Day-2
(monitoring, upgrades, backups) lives in [operations](../operations/README.md).
