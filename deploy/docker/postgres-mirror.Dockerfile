# AWS Marketplace mirror of the bundled in-cluster Postgres.
#
# The Marketplace disallows docker.io pulls at install time, so the image the
# postgresql subchart runs (pinned in deploy/helm/jentic-one/values.yaml and
# deploy/helm/values/aws-marketplace.yaml) is mirrored into the listing's ECR
# by .github/workflows/marketplace-mirror.yml, which builds this one-FROM
# Dockerfile (a retag that preserves layers byte-for-byte).
#
# This lives in deploy/docker/ so Dependabot's docker ecosystem tracks the
# digest (same mechanism as the python-base ARG pins). Keep the tag in
# lockstep with the Helm values files.
#
# KNOWN RISK: Bitnami moved free-tier images to `bitnamilegacy/` in Aug 2025
# and no longer patches them — this digest is frozen, so its CVE count only
# grows and AWS's continuous listing scan will eventually flag it. Before
# public listing, either move the chart to a maintained postgres image or
# make RDS the documented default for Marketplace deploys (the values file
# already carries the RDS variant).
FROM bitnamilegacy/postgresql:17.2.0-debian-12-r6@sha256:6c02546820ca8590cc9bd1109c73ee61e70c8d50122347178b47f951e38f096a
