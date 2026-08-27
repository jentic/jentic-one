"""Render-only assertions for the Helm chart (no cluster required).

These run inside the smoke matrix, but they only shell out to
``helm template`` (the chart has no remote dependencies — every subchart
lives in-tree) — they skip cleanly when helm is absent, so a bare local
``pytest tests/smoke`` stays green."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "jentic-one"
VALUES_DIR = CHART_DIR.parent / "values"

DEV_PASSWORD_SETS = [
    "--set",
    "global.databases.registry.password=x",
    "--set",
    "global.databases.control.password=x",
    "--set",
    "global.databases.admin.password=x",
    "--set",
    "postgresql.auth.password=x",
]


def _helm_template(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
    return subprocess.run(
        ["helm", "template", "jentic", str(CHART_DIR), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.smoke
def test_render_bare_passes_lint_with_placeholders() -> None:
    """Bare `helm lint` + `helm template` succeed — AWS Marketplace runs both.

    The chart ships no password defaults, but the guards are install-time
    only (common.require-install): offline renders emit an unmistakable
    REQUIRED-AT-INSTALL placeholder instead of failing, because AWS's chart
    validation rejects charts that fail bare lint/template
    (INVALID_HELM_LINT / INVALID_HELM_TEMPLATE). A real install against a
    live cluster still refuses to proceed without passwords.
    """
    lint = subprocess.run(
        ["helm", "lint", str(CHART_DIR)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr
    result = _helm_template()
    assert result.returncode == 0, result.stderr
    assert "REQUIRED-AT-INSTALL" in result.stdout
    # No real password defaults sneaked in anywhere.
    assert "postgres_pass" not in result.stdout


@pytest.mark.smoke
@pytest.mark.parametrize("values", ["local-combined", "local-parts", "local-broker"])
def test_render_dev_values(values: str) -> None:
    """The committed dev values files carry their own (dev-only) passwords."""
    result = _helm_template("-f", str(VALUES_DIR / f"{values}.yaml"))
    assert result.returncode == 0, result.stderr


@pytest.mark.smoke
def test_render_bundled_postgres() -> None:
    """The first-party postgresql subchart renders the pinned official image.

    Contract checks for what common.db-env and the init flow depend on: the
    service keeps the Bitnami-era name <release>-postgresql, the umbrella
    chart's <release>-pg-init ConfigMap is mounted for first-boot init, and
    the container runs as the image's postgres user (non-root — required by
    both the publish gate and the AWS Marketplace image scan).
    """
    result = _helm_template("-f", str(VALUES_DIR / "local-combined.yaml"))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "name: jentic-postgresql" in out
    assert 'image: "docker.io/postgres:' in out
    assert "mountPath: /docker-entrypoint-initdb.d" in out
    assert "name: jentic-pg-init" in out
    assert "runAsNonRoot: true" in out


@pytest.mark.smoke
def test_render_marketplace_images_all_ecr() -> None:
    """Every image rendered with aws-marketplace.yaml comes from the ECR registry.

    The Marketplace disallows docker.io/ghcr.io pulls at install time.
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
        *DEV_PASSWORD_SETS,
    )
    assert result.returncode == 0, result.stderr
    images = [
        line.split("image:", 1)[1].strip().strip('"')
        for line in result.stdout.splitlines()
        if line.lstrip().startswith("image:")
    ]
    assert images, "no image references rendered"
    for image in images:
        assert image.startswith("709825985650.dkr.ecr."), f"non-ECR image rendered: {image}"


@pytest.mark.smoke
def test_render_awsmp_launch_parameters() -> None:
    """The Marketplace launch substitutions render into the pod specs.

    The listing's delivery option passes the buyer's service account
    (${AWSMP_SERVICE_ACCOUNT}) into global.serviceAccount.name and the
    AWS-created license secret (${AWSMP_LICENSE_SECRET}) into
    global.awsmp.licenseSecret — both must land on the app AND broker pods.
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
        "--set",
        "global.serviceAccount.name=buyer-sa",
        "--set",
        "global.awsmp.licenseSecret=buyer-license",
        *DEV_PASSWORD_SETS,
    )
    assert result.returncode == 0, result.stderr
    # Both enabled deployments (app + broker) carry the account and mount.
    assert result.stdout.count("serviceAccountName: buyer-sa") == 2
    assert result.stdout.count("secretName: buyer-license") == 2
    assert result.stdout.count("mountPath: /var/run/secrets/aws-marketplace/license") == 2


@pytest.mark.smoke
def test_render_awsmp_defaults_are_inert() -> None:
    """Unset, the Marketplace launch values must render nothing at all."""
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
        *DEV_PASSWORD_SETS,
    )
    assert result.returncode == 0, result.stderr
    assert "serviceAccountName" not in result.stdout
    assert "awsmp-license" not in result.stdout
    assert "kind: ServiceAccount" not in result.stdout


@pytest.mark.smoke
def test_render_service_account_create_requires_name() -> None:
    """create=true without a name must fail loudly, not render a broken SA."""
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
        "--set",
        "global.serviceAccount.create=true",
        *DEV_PASSWORD_SETS,
    )
    assert result.returncode != 0
    assert "global.serviceAccount.name is required" in result.stderr
