# Installation

Production installs run from released artifacts only: the published container image and the two Go CLI binaries. Nothing is built from source on the target hosts. Outbound internet access is needed to fetch and verify the artifacts; at runtime the defaults make two low-volume outbound calls, each with a disable knob (see the table below) — with those off, an install inside your network needs no outbound access.

> Evaluating first? The five-minute SQLite trial — no Postgres, four
> `docker run`s — is the [README quickstart](../../README.md#quickstart).
> This section is the production path.

## Outbound connections

| When | Destination | Purpose | Disable |
| ---- | ----------- | ------- | ------- |
| Install time | `ghcr.io` | Pull the container image | Air-gapped transfer (below) |
| Install time | `github.com` | Download CLI release archives | Air-gapped transfer (below) |
| Install time | Sigstore (Fulcio/Rekor) | `cosign` signature verification | Verify on a connected machine, transfer verified artifacts |
| Runtime (default **on**) | `api.github.com` | "Update available" release check | `release_check.enabled: false` |
| Runtime (default **on**) | `raw.githubusercontent.com` (`catalog.manifest_url`) | Public API catalog manifest refresh + update sweep | `catalog.manifest_max_age_seconds: 0` and `catalog.update_check_interval_seconds: 0` |
| Runtime (default **off**) | `api.jentic.com` | Anonymous product telemetry | Off unless `telemetry.enabled: true` |

(Defaults: [config reference](../reference/config.md).
Brokered API calls go wherever your imported APIs point — that egress is the
product.)

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
  `docker load -i` it inside the network. `docker load` restores the image
  under its ID only — it does **not** restore the `@sha256:` reference, so
  after loading, retag it (`docker tag <image-id> <internal-registry>/jentic-one-app:<version>`)
  and push it to your internal registry (or reference the local tag), and
  point the compose file / systemd `image.env` at that reference instead of
  the GHCR digest. The digest pin did its job on the connected side, where
  the signature was verified.
- **Binaries:** transfer the release archives together with `checksums.txt`,
  `checksums.txt.sig`, and `checksums.txt.pem`, so the verification can be
  repeated inside the network.
- **Helm chart:** not published to a registry yet — vendor
  [`deploy/helm/jentic-one/`](../../deploy/helm/README.md) from a checkout of this
  repository at the release tag. The documented bundled-DB install pulls
  more than the app image: `docker.io/postgres:17.x` (the bundled
  PostgreSQL), plus the OpenTelemetry Collector sidecar image if you enable
  `global.observability.otel` and the gateway image if you enable that
  subchart. Mirror each into your internal registry and override the
  corresponding `image.repository`, or an air-gapped install dies on
  `ImagePullBackOff` for an image the transfer never carried.

## After installing

Register each agent host against the instance (`jentic register --url … --broker-url …`,
see the [CLI README](../../cli/README.md#usage)), then make the
[first brokered call](../guides/first-call.md). Before pointing anything at a real
credential, read the [security hardening guide](../security/README.md). Day-2
(monitoring, upgrades, backups) lives in [operations](../operations/README.md).
