# syntax=docker/dockerfile:1
#
# The agent image of `jentic run --container` (experimental): the jentic CLI,
# gh, git, Node.js and Claude Code's native Linux build, run as an
# unprivileged user. The session's egress points the TLS clients at its CA
# bundle through the container's environment (SSL_CERT_FILE and friends);
# nothing is added to the image's trust store. Not published; build it
# locally from the cli/ module:
#
#   docker build -f deploy/docker/run-agent.Dockerfile -t jentic-run-agent:local cli
#
# /home is left empty: each session mounts its own home volume there and
# seeds /home/agent itself.

ARG GO_VERSION=1.25
ARG NODE_VERSION=24

FROM golang:${GO_VERSION}-bookworm AS jentic
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/jentic ./cmd/jentic

FROM node:${NODE_VERSION}-bookworm-slim
ARG GH_VERSION=2.89.0
ARG CLAUDE_CODE_VERSION=stable

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git less openssh-client procps \
 && rm -rf /var/lib/apt/lists/*

RUN arch="$(dpkg --print-architecture)" \
 && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz" \
    | tar -xz -C /tmp \
 && mv "/tmp/gh_${GH_VERSION}_linux_${arch}/bin/gh" /usr/local/bin/gh \
 && rm -rf /tmp/gh_*

# Claude Code's native build, installed outside any home so every session's
# fresh home volume finds it on PATH.
RUN HOME=/tmp/claude-install bash -c 'curl -fsSL https://claude.ai/install.sh | bash -s "$0"' "${CLAUDE_CODE_VERSION}" \
 && cp -L /tmp/claude-install/.local/bin/claude /usr/local/bin/claude \
 && rm -rf /tmp/claude-install \
 && claude --version

COPY --from=jentic /out/jentic /usr/local/bin/jentic

# The node image's uid 1000 becomes the agent user; its home directory is not
# created in the image (the session's volume provides it).
RUN userdel --remove node \
 && groupadd --gid 1000 agent \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /home/agent --shell /bin/bash agent \
 && rm -rf /home/*

ENV HOME=/home/agent \
    DISABLE_AUTOUPDATER=1 \
    GH_NO_UPDATE_NOTIFIER=1 \
    GIT_TERMINAL_PROMPT=0

USER 1000:1000
WORKDIR /tmp
CMD ["bash"]
