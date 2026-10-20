# syntax=docker/dockerfile:1
#
# The egress proxy of `jentic run --container` (experimental). Not published;
# build it locally from the cli/ module:
#
#   docker build -f deploy/docker/run-proxy.Dockerfile -t jentic-run-proxy:local cli
#
# One static binary, run unprivileged with a read-only root:
#
#   serve       the CONNECT proxy on :3128; publishes the session CA's public
#               certificate, a bundle (system roots + CA) and a ready file to
#               /run/jentic-ca (the session's CA volume)
#   set-tokens  replaces the session's invocation tokens (stdin) on the
#               container's private tmpfs at /run/jentic-proxy
#   ready       exits 0 once the proxy listens and /run/jentic-ca holds this
#               session's CA (readiness nonce check)
#
# The CA's private key exists only in the serve process's memory.

ARG GO_VERSION=1.25

FROM golang:${GO_VERSION}-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/jentic-egress-proxy ./cmd/jentic-egress-proxy

FROM alpine:3.20
# /run/jentic-ca seeds the session's CA volume (owned by the proxy user,
# readable by the agent); /run/jentic-relay is where the relay's socket volume
# is mounted, as in the bridge image.
RUN apk add --no-cache ca-certificates \
 && mkdir -p /run/jentic-ca /run/jentic-proxy /run/jentic-relay \
 && chown 65534:65534 /run/jentic-ca /run/jentic-proxy /run/jentic-relay \
 && chmod 0755 /run/jentic-ca \
 && chmod 0700 /run/jentic-proxy /run/jentic-relay
COPY --from=build /out/jentic-egress-proxy /usr/local/bin/jentic-egress-proxy
USER 65534:65534
EXPOSE 3128
ENTRYPOINT ["/usr/local/bin/jentic-egress-proxy"]
CMD ["serve"]
