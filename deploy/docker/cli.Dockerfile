# syntax=docker/dockerfile:1
#
# Multi-arch image for the `jentic` agent CLI, built FROM SOURCE (not from
# GoReleaser artifacts) so the release pipeline can publish it BEFORE the
# binaries ship — the image gates the `release` job, preserving release.yml's
# "a release never half-ships" invariant with no circular dependency.
# Version-stamp parity with the binaries is enforced by the publish job's
# `jentic --version` == tag gate.
#
# The runtime stage implements the container-isolation contract stated in
# docs/security/mcp-same-host-hardening.md (Recipe 3):
#   - `jentic` on PATH,
#   - non-root uid 10001 (`jentic` user) with writable HOME=/home/jentic,
#   - $HOME/.config/jentic pre-created and OWNED by that user, so a named
#     volume mounted there seeds with the right ownership on first mount
#     (Docker copies the image's content at the mount path into an empty
#     named volume),
#   - ca-certificates for TLS to remote instances.
#
# No ENTRYPOINT: Recipe 3's entries (and its registration step) pass the
# binary name explicitly (`… <image> jentic mcp --context <name>` /
# `… <image> jentic register …`), so the argv must start the process
# directly. CMD gives a bare `docker run -i <image>` a useful default —
# the stdio MCP server — which is what official-MCP-registry clients spawn
# for an `oci` package (cli/server.json).
#
# The io.modelcontextprotocol.server.name label is the registry's OCI
# OWNERSHIP VERIFICATION: publishing to registry.modelcontextprotocol.io
# pulls the image and requires this label to equal server.json's `name`.
# Keep the two in lockstep (cli/server.json).

# Pinned to specific digests for reproducible builds (same policy as the
# sibling service Dockerfiles). To bump: `docker pull golang:1.25` /
# `ubuntu:24.04`, then update the digests (`docker buildx imagetools
# inspect <image>` prints them). The BUILDER never ships; the RUNTIME is
# Ubuntu, matching the app images' base (Ubuntu backports NVD-critical
# glibc fixes Debian stable marks won't-fix, keeping the Trivy gate green).
ARG GOLANG_IMAGE=golang:1.25@sha256:699337d620559a59b4a2bb298ad59611e535d2ee755a34cf2d2a98f37578dc80
ARG UBUNTU_IMAGE=ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

# Go cross-compiles natively, so build on the BUILDPLATFORM and target
# TARGETOS/TARGETARCH — no QEMU-emulated compile on the arm64 leg (same
# rationale as app.multiarch.Dockerfile's ui-builder stage).
FROM --platform=$BUILDPLATFORM ${GOLANG_IMAGE} AS builder
ARG TARGETOS
ARG TARGETARCH
# Version stamp, injected by the publish job (release.yml publish-cli-image)
# as the tag's X.Y.Z / short SHA / build date — the same
# -X …cmdcore.{version,commit,date} trio as cli/Makefile and
# cli/.goreleaser.yaml, so `jentic --version` inside the image equals the
# released binaries' output. `.git/` is dockerignored, so COMMIT cannot be
# derived in-build and must be passed in.
ARG VERSION=dev
ARG COMMIT=none
ARG DATE=unknown

WORKDIR /src
# Dependency layer first so source edits don't re-download modules.
COPY cli/go.mod cli/go.sum ./
RUN go mod download
COPY cli/ ./
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -trimpath \
      -ldflags "-s -w \
        -X github.com/jentic/jentic-one/cli/internal/cli/cmdcore.version=${VERSION} \
        -X github.com/jentic/jentic-one/cli/internal/cli/cmdcore.commit=${COMMIT} \
        -X github.com/jentic/jentic-one/cli/internal/cli/cmdcore.date=${DATE}" \
      -o /out/jentic ./cmd/jentic

FROM ${UBUNTU_IMAGE} AS runtime

# MUST match cli/server.json's `name` — see the ownership-verification note
# in the header. Standard OCI source label alongside it so GHCR links the
# package to this repo.
LABEL io.modelcontextprotocol.server.name="one.jentic/jentic" \
      org.opencontainers.image.source="https://github.com/jentic/jentic-one" \
      org.opencontainers.image.description="Jentic One agent CLI (jentic) — local MCP stdio server" \
      org.opencontainers.image.licenses="Apache-2.0"

# The upgrade picks up security fixes published since the pinned digest's
# last upstream rebuild (same posture as app.multiarch.Dockerfile).
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Recipe 3's uid/HOME/config-dir contract — see the header comment.
RUN useradd --uid 10001 --create-home jentic \
    && mkdir -p /home/jentic/.config/jentic \
    && chown -R jentic:jentic /home/jentic

COPY --from=builder /out/jentic /usr/local/bin/jentic

USER 10001:10001
ENV HOME=/home/jentic
CMD ["jentic", "mcp"]
