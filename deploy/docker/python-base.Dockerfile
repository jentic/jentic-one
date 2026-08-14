# syntax=docker/dockerfile:1
# Pinned to a specific digest for reproducible builds.
# To bump: `docker pull python:3.12-slim` / `node:22-slim` then update the
# digests below (`docker buildx imagetools inspect <image>` prints them).
ARG PYTHON_IMAGE=python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
ARG NODE_IMAGE=node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

# UI build stage — produces ui/dist, bundled into the wheel via the
# [tool.hatch.build.targets.wheel.force-include] "ui/dist" -> jentic_one/static.
FROM ${NODE_IMAGE} AS ui-builder
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
