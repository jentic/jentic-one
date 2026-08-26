"""Render-only assertions for the Helm chart (no cluster required).

These run inside the smoke matrix (tools.deploy ci-smoke builds the chart
dependencies before invoking pytest), but they only shell out to
``helm template`` — they skip cleanly when helm or the built dependencies
are absent, so a bare local ``pytest tests/smoke`` stays green.
"""

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
    if not list((CHART_DIR / "charts").glob("postgresql-*.tgz")):
        pytest.skip("chart dependencies not built (run: helm dependency build)")
    return subprocess.run(
        ["helm", "template", "jentic", str(CHART_DIR), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.smoke
def test_render_requires_db_passwords() -> None:
    """The chart ships no password defaults; a bare render must fail loudly."""
    result = _helm_template()
    assert result.returncode != 0
    assert "password is required" in result.stderr


@pytest.mark.smoke
@pytest.mark.parametrize("values", ["local-combined", "local-parts", "local-broker"])
def test_render_dev_values(values: str) -> None:
    """The committed dev values files carry their own (dev-only) passwords."""
    result = _helm_template("-f", str(VALUES_DIR / f"{values}.yaml"))
    assert result.returncode == 0, result.stderr


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
