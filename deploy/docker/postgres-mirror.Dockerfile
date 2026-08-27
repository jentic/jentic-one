# AWS Marketplace mirror of the bundled in-cluster Postgres.
#
# The Marketplace disallows docker.io pulls at install time, so the image the
# first-party postgresql subchart runs (pinned in
# deploy/helm/jentic-one/values.yaml and deploy/helm/values/aws-marketplace.yaml)
# is mirrored into the listing's ECR by
# .github/workflows/marketplace-mirror.yml, which builds this one-FROM
# Dockerfile (a retag that preserves layers byte-for-byte).
#
# This lives in deploy/docker/ so Dependabot's docker ecosystem tracks the
# digest (same mechanism as the python-base ARG pins). Keep the tag in
# lockstep with the Helm values files.
#
# Official library/postgres — actively maintained (replaced the frozen
# bitnamilegacy image, which failed the Trivy gate; 2026-08-26). The workflow
# still Trivy-scans every mirror run before pushing.
FROM postgres:17.11@sha256:0b657ff48d7f76a1e907f381b1693eb4f2bf54c1d2df4feb6743d7dc601768dd
