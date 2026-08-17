# syntax=docker/dockerfile:1
#
# Self-contained, MULTI-ARCH build of the app image for the release pipeline.
#
# Why a separate Dockerfile from deploy/docker/app.Dockerfile:
#   app.Dockerfile does `FROM python-base:runtime` and
#   `COPY --from=python-base:builder …` — bare LOCAL-DAEMON image tags that
#   `make build-base` populates first. That two-step local-tag dance works for
#   single-arch local builds (`make build-app`, the CLI's install BuildImages),
#   but `docker buildx build --platform linux/amd64,linux/arm64` cannot resolve
#   those local tags per-arch: buildx does not transparently alias a bare tag to
#   a build stage across a multi-platform build. So the release path uses THIS
#   file, which INLINES the python-base stages (ui-builder → builder → runtime)
#   into one self-contained multi-stage graph buildx can build for every arch in
#   a single invocation.
#
# Keep the stage bodies below IN LOCKSTEP with deploy/docker/python-base.Dockerfile
# and deploy/docker/app.Dockerfile — they are the single-arch local truth. (A
# drift here only affects the multi-arch release artifact; the local build and
# `make build-*` still use the two-file split.)

# Pinned to a specific digest for reproducible builds.
# To bump: `docker pull python:3.12-slim` / `node:22-slim` then update the
# digests below (`docker buildx imagetools inspect <image>` prints them).
ARG PYTHON_IMAGE=python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
ARG NODE_IMAGE=node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

# UI build stage — produces ui/dist, bundled into the wheel via the
# [tool.hatch.build.targets.wheel.force-include] "ui/dist" -> jentic_one/static.
# The UI bundle is arch-independent JS; build it on the native BUILDPLATFORM to
# avoid a slow emulated npm on the arm64 leg.
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS ui-builder
WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY ui/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
# `src/jentic_one/static/` (generated UI bundle) is excluded from the build
# context via `.dockerignore` — the wheel gets it solely from the force-include
# of `ui/dist` below, so a stale local copy can't collide with it (issue #654).
COPY src/ src/
COPY openapi/ openapi/
# Built SPA must be present before `uv build` so force-include packages it.
COPY --from=ui-builder /ui/dist ui/dist

RUN uv build --wheel --out-dir /build/dist

FROM ${PYTHON_IMAGE} AS runtime

RUN groupadd -r jentic && useradd --no-log-init -r -g jentic jentic

EXPOSE 8000

# --- app stage (mirrors deploy/docker/app.Dockerfile) ---------------------
FROM runtime AS app

COPY --from=builder /build/dist/*.whl /tmp/
RUN whl="$(ls /tmp/jentic_one-*.whl)" && \
    pip install --no-cache-dir "${whl}" && \
    rm /tmp/*.whl

# Writable data dir for the SQLite backend. Pre-creating it owned by the runtime
# user means a fresh Docker volume mounted here inherits jentic ownership, so the
# non-root process can create database files (a root-owned volume cannot).
RUN mkdir -p /data && chown jentic:jentic /data

USER jentic
ENV JENTIC__APPS=registry,admin,control,auth
CMD ["python", "-m", "jentic_one"]
