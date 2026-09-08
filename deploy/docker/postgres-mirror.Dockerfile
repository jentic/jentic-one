# AWS Marketplace mirror of the bundled in-cluster Postgres.
#
# The Marketplace disallows docker.io pulls at install time, so the image the
# first-party postgresql subchart runs (pinned in
# deploy/helm/values/aws-marketplace.yaml) is mirrored into the listing's ECR
# by .github/workflows/marketplace-mirror.yml, which builds this Dockerfile.
#
# This lives in deploy/docker/ so Dependabot's docker ecosystem tracks the
# digest (same mechanism as the python-base ARG pins). Keep the tag in
# lockstep with the Marketplace values overlay.
#
# ALPINE, not the Debian default: AWS Marketplace's scanner (Inspector) rates
# CVEs by NVD severity, and Debian stable ships glibc CVEs it has marked
# no-dsa/won't-fix (e.g. CVE-2026-5450, NVD 9.8) — unfixable via apt, so any
# Debian-based image hard-fails listing validation indefinitely. Alpine/musl
# has no glibc at all. The umbrella chart's default for self-hosters remains
# the official Debian image.
#
# The build hardens the official image just enough to pass the publish gates:
#   - remap postgres uid/gid 70 -> 999: the postgresql subchart's StatefulSet
#     pins runAsUser/fsGroup 999 (the Debian image's postgres uid), and the
#     Marketplace overlay must keep working against the same chart contract.
#   - drop gosu: only used by the entrypoint to step down from root, and
#     the chart always starts the container as the postgres user, so it is
#     dead code — and its Go-stdlib CVE findings are a permanent treadmill
#     (upstream rebuilds it rarely).
#   - apk upgrade: picks up Alpine security fixes published since the pinned
#     digest's last upstream rebuild.
FROM postgres:17.11-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73
RUN apk upgrade --no-cache \
 && rm -f /usr/local/bin/gosu /sbin/su-exec \
 && apk add --no-cache shadow \
 # Alpine assigns gid 999 to `ping` (setgid busybox helper); move it out of
 # the way so postgres can take the gid the chart's securityContext pins.
 && ! getent group 998 \
 && groupmod -g 998 ping \
 && groupmod -g 999 postgres \
 && usermod -u 999 -g 999 postgres \
 && apk del shadow \
 && chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql
USER postgres
