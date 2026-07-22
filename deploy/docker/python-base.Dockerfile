# syntax=docker/dockerfile:1
# Pinned to a specific digest for reproducible builds.
# To bump: `docker pull python:3.12-slim` then update the digest below.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203
ARG NODE_IMAGE=node:22-slim

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
