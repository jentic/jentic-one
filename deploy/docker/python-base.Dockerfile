# syntax=docker/dockerfile:1
# Pinned to specific digests for reproducible builds.
# To bump: `docker pull python:3.12-slim` / `node:22-slim` / `ubuntu:24.04`
# then update the digests below (`docker buildx imagetools inspect <image>`
# prints them).
#
# The RUNTIME base is Ubuntu, not python:3.12-slim (Debian): AWS Marketplace's
# scanner (Inspector) rates CVEs by NVD severity, and Debian stable carries
# glibc CVEs it has marked no-dsa/won't-fix (e.g. CVE-2026-5450, NVD 9.8) —
# unfixable via apt, so any Debian-based image hard-fails listing validation
# indefinitely. Ubuntu backports those fixes to its stable glibc (noble ships
# python3.12 natively, so the runtime contract is unchanged). The BUILDER
# stages stay on the official python/node images — they never ship.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
ARG NODE_IMAGE=node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436
ARG UBUNTU_IMAGE=ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

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

FROM ${UBUNTU_IMAGE} AS runtime

# noble's python3.12 + venv (ensurepip) is everything the app stage needs to
# create /opt/venv and install the wheel into it. The upgrade picks up
# security fixes published since the pinned digest's last upstream rebuild
# (Ubuntu, unlike Debian stable, backports NVD-critical glibc fixes).
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r jentic && useradd --no-log-init -r -g jentic jentic

EXPOSE 8000
