# AWS Marketplace mirror of the bundled in-cluster Postgres.
#
# The Marketplace disallows docker.io pulls at install time, so the image the
# first-party postgresql subchart runs (pinned in
# deploy/helm/jentic-one/values.yaml and deploy/helm/values/aws-marketplace.yaml)
# is mirrored into the listing's ECR by
# .github/workflows/marketplace-mirror.yml, which builds this Dockerfile.
#
# This lives in deploy/docker/ so Dependabot's docker ecosystem tracks the
# digest (same mechanism as the python-base ARG pins). Keep the tag in
# lockstep with the Helm values files.
#
# The build hardens the official image just enough to pass the Trivy publish
# gate (the workflow scans every mirror run before pushing):
#   - drop gosu: only used by the entrypoint to step down from root, and the
#     chart always starts the container as the postgres user (uid 999), so it
#     is dead code — and its Go-stdlib CVE findings are a permanent treadmill
#     (upstream rebuilds it rarely).
#   - drop the Debian snakeoil placeholder TLS key: unused (the chart does
#     not enable ssl), but Trivy's secret scanner flags any private key.
#   - apt-get upgrade: picks up Debian security fixes published since the
#     pinned digest's last upstream rebuild.
FROM postgres:17.11@sha256:0b657ff48d7f76a1e907f381b1693eb4f2bf54c1d2df4feb6743d7dc601768dd
RUN rm -f /usr/local/bin/gosu /etc/ssl/private/ssl-cert-snakeoil.key \
 && apt-get update \
 && apt-get upgrade -y \
 && rm -rf /var/lib/apt/lists/*
USER postgres
