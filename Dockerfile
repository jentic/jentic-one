# Stage 1: Build UI
FROM node:24-slim AS ui-build
WORKDIR /build
COPY ui/ ./ui/
RUN mkdir -p static
RUN cd ui && npm ci --ignore-scripts && npm run build

# Stage 2: Install Python dependencies
FROM python:3.11-slim AS py-deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Stage 3: Runtime
FROM python:3.11-slim

# Upgrade the base image's system-wide pip / setuptools / wheel to clear
# transitive CVEs Trivy reports against /usr/local/lib/python3.11/site-packages
# (wheel CVE-2026-24049, setuptools-vendored jaraco.context CVE-2026-23949).
# These are bootstrap-only, not imported by the app; unpinned --upgrade is
# self-healing against future CVEs.
RUN python -m pip install --upgrade --no-cache-dir pip setuptools wheel

ARG APP_VERSION=0.13.1
ENV APP_VERSION=${APP_VERSION}

LABEL maintainer="vladimir@jentic.com" \
      org.opencontainers.image.authors="vladimir@jentic.com" \
      org.opencontainers.image.url="https://github.com/jentic/jentic-mini" \
      org.opencontainers.image.source="https://github.com/jentic/jentic-mini" \
      org.opencontainers.image.description="Jentic Mini Docker image" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${APP_VERSION}"

WORKDIR /app

COPY --from=py-deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /app/data /app/src

# Copy source into /app/src so that `from src.xxx` imports work from WORKDIR /app
COPY src/ /app/src/

# Copy Alembic migration config and scripts
COPY alembic.ini /app/alembic.ini
COPY alembic/ /app/alembic/

# Copy built UI assets from stage 1 — placed outside /app/src/ so the
# dev bind mount (./src:/app/src) doesn't hide them at runtime.
COPY --from=ui-build /build/static/ /app/static/

COPY --chmod=0644 LICENSE NOTICE llms.txt /app/
COPY --chmod=0755 docker-entrypoint.sh /app/docker-entrypoint.sh

# Run as non-root
RUN useradd -r -s /bin/false jentic \
    && chown -R jentic:jentic /app/data
USER jentic

EXPOSE 8900

# Entrypoint runs DB init + broker app seed before starting the server.
# Both steps are idempotent — safe on every container start.
CMD ["/app/docker-entrypoint.sh"]
