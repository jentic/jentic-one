"""Render-only assertions for the Helm chart (no cluster required).

These run inside the smoke matrix, but they only shell out to
``helm template`` (the chart has no remote dependencies — every subchart
lives in-tree) — they skip cleanly when helm is absent, so a bare local
``pytest tests/smoke`` stays green."""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "jentic-one"
VALUES_DIR = CHART_DIR.parent / "values"


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
    )
    assert result.returncode != 0
    assert "global.serviceAccount.name is required" in result.stderr


@pytest.mark.smoke
def test_render_marketplace_entitlement_env() -> None:
    """The enforcing env lands on BOTH pods with the live listing's IDs.

    Enforcement went live alongside the checkout-shape/check-in client fix —
    a chart carrying this env against an older image (no entitlement config;
    extra="forbid") crashes at boot, which is exactly why the env and the
    client fix ride the same release.
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.count("name: JENTIC__ENTITLEMENT__ENABLED") == 2  # app + broker
    assert out.count('value: "dwhxz1v53k58ew6pr7qzieumd"') == 2  # product code
    assert out.count('value: "prod-dd2p2s65dysv6"') == 2  # product ID (SKU)
    assert out.count('value: "users,executions"') == 2


@pytest.mark.smoke
def test_render_marketplace_app_secrets() -> None:
    """aws-marketplace.yaml auto-generates every secret — zero-touch install.

    The generated Secret carries the four scalar app secrets (encryption
    keyset, admin JWT secret, invite pepper, connect state secret — no safe
    defaults; JENTIC_ENV=production refuses the placeholders) plus the four
    bundled-DB passwords (pure pod-to-pod wiring on a ClusterIP service).
    Nothing here is buyer-supplied: this render passes NO passwords at all,
    and no REQUIRED-AT-INSTALL placeholder may survive. The Secret must be
    resource-policy keep — losing it orphans everything already encrypted
    and revokes live sessions.
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "name: jentic-app-secrets" in out
    assert '"helm.sh/resource-policy": keep' in out
    # Zero-touch: no password placeholder anywhere in the render.
    assert "REQUIRED-AT-INSTALL" not in out
    # App and broker both mount the config file, point the loader at it, and
    # run in production mode so the placeholder guards actually enforce.
    assert out.count("secretName: jentic-app-secrets") == 2
    assert out.count("value: /etc/jentic/app-secrets/config.yaml") == 2
    assert out.count("name: JENTIC_ENV") == 2
    # Service-pod DB passwords ride secretKeyRef (3 surfaces x app+broker),
    # never plain env values.
    for surface in ("registry", "control", "admin"):
        assert out.count(f"key: db-password-{surface}") >= 2
    # The Postgres server + init script draw from the same Secret.
    assert "key: db-password-postgres" in out
    for surface in ("REGISTRY", "CONTROL", "ADMIN"):
        assert f"name: PGINIT_PASSWORD_{surface}" in out
    # The init ConfigMap is a shell script that reads env — no inlined
    # passwords in a (non-secret) ConfigMap.
    assert "init-schemas.sh" in out
    assert "PASSWORD %L" in out  # psql format()-quoted, not Helm-interpolated
    # The generated config carries all four secrets, and the encryption
    # material decodes to exactly 32 bytes (AES-256).
    docs = out.split("---")
    secret_doc = next(d for d in docs if "name: jentic-app-secrets" in d)
    b64 = next(
        line.split(":", 1)[1].strip()
        for line in secret_doc.splitlines()
        if line.strip().startswith("config.yaml:")
    )
    config = base64.b64decode(b64).decode()
    for key in ("active_id: v1", "jwt_secret:", "pepper:", "state_secret:"):
        assert key in config, f"generated config.yaml missing {key}"
    material = next(
        line.split(":", 1)[1].strip()
        for line in config.splitlines()
        if line.strip().startswith("material:")
    )
    assert len(base64.b64decode(material)) == 32
    # All four DB password keys present in the Secret itself.
    for key in ("registry", "control", "admin", "postgres"):
        assert f"db-password-{key}:" in secret_doc


@pytest.mark.smoke
def test_render_explicit_passwords_beat_generated() -> None:
    """Explicit passwords always win over the generated Secret.

    This is the external-DB (RDS) escape hatch on the Marketplace chart —
    and the upgrade path for pre-zero-touch installs whose DB roles were
    created with buyer-chosen passwords.
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
        "--set",
        "global.databases.registry.password=explicit-pw",
    )
    assert result.returncode == 0, result.stderr
    assert 'value: "explicit-pw"' in result.stdout
    assert "key: db-password-registry" not in result.stdout
    # The other surfaces still resolve from the generated Secret.
    assert "key: db-password-control" in result.stdout


@pytest.mark.smoke
def test_render_app_secrets_existing_secret() -> None:
    """existingSecret mounts the buyer's Secret and renders none of ours."""
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
        "--set",
        "global.appSecrets.existingSecret=buyer-secrets",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("secretName: buyer-secrets") == 2
    # No chart-generated Secret rendered (the pod volume name still matches
    # "jentic-app-secrets", so key on the Secret's keep-annotation instead).
    assert '"helm.sh/resource-policy": keep' not in result.stdout


@pytest.mark.smoke
def test_render_app_secrets_off_by_default() -> None:
    """Bare renders carry no app-secrets Secret, mount, or JENTIC_CONFIG_FILE."""
    result = _helm_template()
    assert result.returncode == 0, result.stderr
    assert "jentic-app-secrets" not in result.stdout


@pytest.mark.smoke
def test_render_app_secrets_conflict_with_config_file() -> None:
    """generate=true + a dev configFile must fail loudly.

    Both claim JENTIC_CONFIG_FILE (the loader reads a single file); silently
    preferring one would ship secrets the operator didn't choose.
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "local-combined.yaml"),
        "--set",
        "global.appSecrets.generate=true",
    )
    assert result.returncode != 0
    assert "mutually exclusive" in result.stderr
