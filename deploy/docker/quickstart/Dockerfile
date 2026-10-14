# syntax=docker/dockerfile:1
# check=skip=SecretsUsedInArgOrEnv
#
# Jentic One quickstart — the README's Docker trial shape as ONE container.
#
# Wraps the published app image with the config/quickstart.env settings baked
# in (SQLite files on the /data volume, development-mode secrets, no TLS) and
# an entrypoint that runs migrations, then starts the broker (:8100) and the
# control plane (:8000) side by side. Build it straight from GitHub — no clone
# needed:
#
#   docker build -t jentic-one-quickstart \
#     https://github.com/jentic/jentic-one.git#main:deploy/docker/quickstart
#   docker run -d --name jentic -v jentic-data:/data \
#     -p 127.0.0.1:8000:8000 -p 127.0.0.1:8100:8100 jentic-one-quickstart
#   docker exec -it jentic python -m jentic_one create-admin --email you@example.com
#
# Trial use only: the broker and control plane share one container and the
# secrets are per-process dev values. The production shape (one role per
# container, external Postgres, real secrets, TLS) is docs/installation/docker.md.

ARG JENTIC_IMAGE=ghcr.io/jentic/jentic-one-app:latest
FROM ${JENTIC_IMAGE}

# Mirrors config/quickstart.env. Any config key can be overridden at run time
# with `-e JENTIC__SECTION__KEY=…` (see config/README.md). None of these are
# secrets — the skipped SecretsUsedInArgOrEnv check misreads the `AUTH` key.
ENV JENTIC__AUTH__CANONICAL_BASE_URL=http://127.0.0.1:8000 \
    JENTIC__DATABASES__REGISTRY__BACKEND=sqlite \
    JENTIC__DATABASES__REGISTRY__PATH=/data/registry.db \
    JENTIC__DATABASES__CONTROL__BACKEND=sqlite \
    JENTIC__DATABASES__CONTROL__PATH=/data/control.db \
    JENTIC__DATABASES__ADMIN__BACKEND=sqlite \
    JENTIC__DATABASES__ADMIN__PATH=/data/admin.db \
    JENTIC__TELEMETRY__ENABLED=true \
    JENTIC_QUICKSTART_BROKER_PORT=8100

COPY --chmod=0755 entrypoint.sh /usr/local/bin/jentic-quickstart

VOLUME ["/data"]
EXPOSE 8000 8100

# Both surfaces answer /health; the container is healthy only when both do.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request as u; [u.urlopen(f'http://127.0.0.1:{p}/health', timeout=3) for p in ('8000', os.environ['JENTIC_QUICKSTART_BROKER_PORT'])]"]

ENTRYPOINT ["/usr/local/bin/jentic-quickstart"]
