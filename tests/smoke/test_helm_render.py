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
from typing import Any

import pytest
import yaml

from jentic_one.shared.config import GUARDED_FIELD_SURFACES

CHART_DIR = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "jentic-one"
VALUES_DIR = CHART_DIR.parent / "values"

# Every workload the chart owns that runs first-party code, and so must carry the
# hardened pod + container securityContext. The bundled Postgres is excluded on
# purpose: it is a third-party image that writes outside its data volume, so it
# sets its own (non-root, no read-only root) context in its subchart.
HARDENED_KINDS = ("Deployment", "Job")


def _helm_template(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
    return subprocess.run(
        ["helm", "template", "jentic", str(CHART_DIR), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _manifests(*args: str) -> list[dict[str, Any]]:
    """Render and parse, failing the test on any render error."""
    result = _helm_template(*args)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _env(container: dict[str, Any]) -> list[dict[str, Any]]:
    return container.get("env") or []


def _env_names(container: dict[str, Any]) -> list[str]:
    return [entry["name"] for entry in _env(container)]


def _manifests_from(stdout: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(stdout) if doc]


def _app_secrets_data(docs: list[dict[str, Any]]) -> dict[str, str]:
    """Decoded data of the chart-generated app-secrets Secret."""
    secret = next(
        doc
        for doc in docs
        if doc.get("kind") == "Secret" and doc["metadata"]["name"].endswith("-app-secrets")
    )
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


def _deployments(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Deployments keyed by their app.kubernetes.io/name (the subchart name)."""
    return {
        doc["metadata"]["labels"]["app.kubernetes.io/name"]: doc
        for doc in docs
        if doc.get("kind") == "Deployment"
    }


def _app_secret_refs(deployment: dict[str, Any]) -> dict[str, str]:
    """What a Deployment takes from the app-secrets Secret: env name -> key.

    The mounted file shows up under its JENTIC_CONFIG_FILE path's key.
    """
    spec = deployment["spec"]["template"]["spec"]
    container = spec["containers"][0]
    refs: dict[str, str] = {}
    for entry in _env(container):
        ref = (entry.get("valueFrom") or {}).get("secretKeyRef")
        if ref and not ref["key"].startswith("db-password-"):
            refs[entry["name"]] = ref["key"]
    for volume in spec.get("volumes") or []:
        if volume["name"] == "jentic-app-secrets":
            for item in volume["secret"]["items"]:
                refs["JENTIC_CONFIG_FILE"] = item["key"]
    config_file = [e for e in _env(container) if e["name"] == "JENTIC_CONFIG_FILE"]
    if "JENTIC_CONFIG_FILE" in refs:
        assert config_file == [
            {
                "name": "JENTIC_CONFIG_FILE",
                "value": f"/etc/jentic/app-secrets/{refs['JENTIC_CONFIG_FILE']}",
            }
        ]
    return refs


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
def test_render_marketplace_broker_role() -> None:
    """The Marketplace broker pod must override the app image's baked role.

    Both Marketplace deployments run the jentic-one-app image, which bakes
    JENTIC__APPS=registry,admin,control,auth. The subcharts don't set the role
    themselves, so aws-marketplace.yaml must: without the override the
    "broker" pod boots a second app instance with no forward-proxy surface
    and every brokered execute 404s (0.37.3 buyer test, 2026-08-28).
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "aws-marketplace.yaml"),
        "--set",
        "global.image.tag=0.0.0-test",
    )
    assert result.returncode == 0, result.stderr
    docs = result.stdout.split("---")
    broker = next(d for d in docs if "kind: Deployment" in d and "name: jentic-broker" in d)
    app = next(d for d in docs if "kind: Deployment" in d and "name: jentic-app" in d)
    assert "name: JENTIC__APPS" in broker
    assert 'value: "broker"' in broker
    # The app pod keeps the image's baked default role set.
    assert "name: JENTIC__APPS" not in app


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
    assert out.count('value: "ed4bj3dbc8w2r80qtnbxbboub"') == 2  # product code
    assert out.count('value: "prod-cwonumew2jeyo"') == 2  # product ID (SKU)
    # Gate on `users` ONLY: the public card is buyer-configurable (constraints
    # locked loosen-only), so the price rides users with executions at $0 —
    # requiring both dimensions would reject every self-service public buyer,
    # and a $0 executions-only cart must NOT mint a valid license.
    assert out.count('value: "users"') == 2
    assert "users,executions" not in out


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
    # App and broker both mount the keyset file, point the loader at it, and
    # run in production mode so the placeholder guards actually enforce.
    assert out.count("secretName: jentic-app-secrets") == 2
    assert out.count("value: /etc/jentic/app-secrets/credentials-encryption.yaml") == 2
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
    # One key per concern, and the encryption material decodes to exactly 32
    # bytes (AES-256).
    data = _app_secrets_data(_manifests_from(out))
    keyset = yaml.safe_load(data["credentials-encryption.yaml"])
    assert set(keyset) == {"credentials"}
    assert set(keyset["credentials"]) == {"encryption"}
    encryption = keyset["credentials"]["encryption"]
    assert encryption["active_id"] == "v1"
    assert len(base64.b64decode(encryption["entries"][0]["material"])) == 32
    for key in ("admin-jwt-secret", "admin-invite-pepper", "connect-state-secret"):
        assert data[key].strip(), key
    # The legacy single-document key carries the SAME values (layout=single and
    # a rollback to an older chart read it), never a second set.
    legacy = yaml.safe_load(data["config.yaml"])
    assert legacy["credentials"]["encryption"] == encryption
    assert legacy["admin"]["auth"]["jwt_secret"] == data["admin-jwt-secret"]
    assert legacy["admin"]["invite"]["pepper"] == data["admin-invite-pepper"]
    assert legacy["credentials"]["connect"]["state_secret"] == data["connect-state-secret"]
    # All four DB password keys present in the Secret itself.
    for key in ("registry", "control", "admin", "postgres"):
        assert f"db-password-{key}" in data


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


_ALL_SURFACES_ON = (
    "--set",
    "admin.enabled=true",
    "--set",
    "registry.enabled=true",
    "--set",
    "control.enabled=true",
    "--set",
    "broker.enabled=true",
    "--set",
    "global.databases.registry.password=x",
    "--set",
    "global.databases.control.password=x",
    "--set",
    "global.databases.admin.password=x",
    "--set",
    "postgresql.auth.password=x",
)

# The app surfaces each subchart runs (deploy/docker/*.Dockerfile JENTIC__APPS).
_SUBCHART_APPS = {
    "app": {"registry", "admin", "control", "auth"},
    "admin": {"admin", "auth"},
    "control": {"control"},
    "registry": {"registry"},
    "broker": {"broker"},
}

_SCALAR_KEYS = {
    "admin.auth.jwt_secret": ("JENTIC__ADMIN__AUTH__JWT_SECRET", "admin-jwt-secret"),
    "admin.invite.pepper": ("JENTIC__ADMIN__INVITE__PEPPER", "admin-invite-pepper"),
    "credentials.connect.state_secret": (
        "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET",
        "connect-state-secret",
    ),
}

# Which subcharts read the credential-encryption keyset (not a production-guarded
# scalar, so it has no GUARDED_FIELD_SURFACES entry): control (credential writes),
# broker (injection + token refresh), admin (provider-config client secrets),
# and app, which bundles all three.
_KEYSET_SUBCHARTS = {"app", "admin", "control", "broker"}


def _expected_split_refs(subchart: str) -> dict[str, str]:
    apps = _SUBCHART_APPS[subchart]
    expected = {
        env: key
        for field, (env, key) in _SCALAR_KEYS.items()
        if apps & GUARDED_FIELD_SURFACES[field]
    }
    if subchart in _KEYSET_SUBCHARTS:
        expected["JENTIC_CONFIG_FILE"] = "credentials-encryption.yaml"
    return expected


@pytest.mark.smoke
def test_render_app_secrets_split_mounts_only_what_each_surface_reads() -> None:
    """generate=true hands each Python surface exactly the secrets its code reads.

    The expectation is derived from shared/config.py GUARDED_FIELD_SURFACES (the same
    table that relaxes the production guard for a standalone surface), so the
    chart and the loader cannot drift apart: a surface the chart starves of a
    secret it reads would fail its production boot; a surface handed one it
    never reads is what this layout exists to stop. Covers the parts shape
    (admin/control/registry/broker) and the combined app.
    """
    for app_enabled in ("true", "false"):
        docs = _manifests(
            *_ALL_SURFACES_ON,
            "--set",
            f"app.enabled={app_enabled}",
            "--set",
            "global.appSecrets.generate=true",
        )
        deployments = _deployments(docs)
        for subchart in _SUBCHART_APPS:
            if subchart == "app" and app_enabled == "false":
                assert subchart not in deployments
                continue
            assert _app_secret_refs(deployments[subchart]) == _expected_split_refs(subchart), (
                subchart
            )


@pytest.mark.smoke
def test_render_app_secrets_split_expected_matrix() -> None:
    """Pin the per-surface matrix itself, independent of GUARDED_FIELD_SURFACES."""
    assert {name: set(_expected_split_refs(name).values()) for name in _SUBCHART_APPS} == {
        "app": {
            "credentials-encryption.yaml",
            "admin-jwt-secret",
            "admin-invite-pepper",
            "connect-state-secret",
        },
        "admin": {"credentials-encryption.yaml", "admin-jwt-secret", "admin-invite-pepper"},
        "control": {"credentials-encryption.yaml", "admin-jwt-secret", "connect-state-secret"},
        "registry": {"admin-jwt-secret"},
        "broker": {"credentials-encryption.yaml"},
    }


@pytest.mark.smoke
def test_render_app_secrets_existing_secret_defaults_to_single_layout() -> None:
    """An existingSecret keeps the legacy single config.yaml on every surface.

    Existing Secrets written for older chart versions hold only config.yaml;
    switching them to per-concern keys must be an explicit opt-in.
    """
    docs = _manifests(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.existingSecret=buyer-secrets",
    )
    for name, deployment in _deployments(docs).items():
        assert _app_secret_refs(deployment) == {"JENTIC_CONFIG_FILE": "config.yaml"}, name
        volume = next(
            v
            for v in deployment["spec"]["template"]["spec"]["volumes"]
            if v["name"] == "jentic-app-secrets"
        )
        assert volume["secret"]["secretName"] == "buyer-secrets"
    assert not [d for d in docs if d.get("kind") == "Secret"]


@pytest.mark.smoke
def test_render_app_secrets_existing_secret_split_layout() -> None:
    """existingSecret + layout=split references the per-concern keys by name."""
    docs = _manifests(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.existingSecret=buyer-secrets",
        "--set",
        "global.appSecrets.layout=split",
    )
    for name, deployment in _deployments(docs).items():
        assert _app_secret_refs(deployment) == _expected_split_refs(name), name
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        for entry in _env(container):
            ref = (entry.get("valueFrom") or {}).get("secretKeyRef")
            if ref:
                assert ref["name"] == "buyer-secrets"


@pytest.mark.smoke
def test_render_app_secrets_generate_single_layout() -> None:
    """generate + layout=single restores the old everything-everywhere mount."""
    docs = _manifests(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.generate=true",
        "--set",
        "global.appSecrets.layout=single",
    )
    for name, deployment in _deployments(docs).items():
        assert _app_secret_refs(deployment) == {"JENTIC_CONFIG_FILE": "config.yaml"}, name


@pytest.mark.smoke
def test_render_app_secrets_rejects_unknown_layout() -> None:
    result = _helm_template(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.generate=true",
        "--set",
        "global.appSecrets.layout=per-pod",
    )
    assert result.returncode != 0
    assert "global.appSecrets.layout" in result.stderr


@pytest.mark.smoke
def test_render_app_secrets_split_extra_env_wins() -> None:
    """An operator-set secret env var replaces the chart's secretKeyRef entry."""
    docs = _manifests(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.generate=true",
        "--set",
        "registry.extraEnv.JENTIC__ADMIN__AUTH__JWT_SECRET=from-operator",
    )
    container = _deployments(docs)["registry"]["spec"]["template"]["spec"]["containers"][0]
    entries = [e for e in _env(container) if e["name"] == "JENTIC__ADMIN__AUTH__JWT_SECRET"]
    assert entries == [{"name": "JENTIC__ADMIN__AUTH__JWT_SECRET", "value": "from-operator"}]


@pytest.mark.smoke
def test_render_app_secrets_split_registry_may_use_config_file() -> None:
    """In split mode the registry mounts no app-secrets file, so configFile is free.

    Surfaces that do mount the keyset file still refuse the combination.
    """
    ok = _helm_template(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.generate=true",
        "--set",
        "registry.configFile.contents.runtime.log_level=DEBUG",
    )
    assert ok.returncode == 0, ok.stderr
    clash = _helm_template(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.generate=true",
        "--set",
        "broker.configFile.contents.runtime.log_level=DEBUG",
    )
    assert clash.returncode != 0
    assert "mutually exclusive" in clash.stderr


@pytest.mark.smoke
@pytest.mark.parametrize(
    ("subchart", "apps", "expected"),
    [
        # The AWS Marketplace shape: the combined app image re-roled as broker.
        ("app", "broker", {"JENTIC_CONFIG_FILE": "credentials-encryption.yaml"}),
        # A widened standalone surface gets the union of what its apps read.
        (
            "registry",
            "registry, control",
            {
                "JENTIC_CONFIG_FILE": "credentials-encryption.yaml",
                "JENTIC__ADMIN__AUTH__JWT_SECRET": "admin-jwt-secret",
                "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET": "connect-state-secret",
            },
        ),
        # A surface the chart has no entry for gets everything (fail-safe).
        (
            "broker",
            "broker,enterprise-thing",
            {
                "JENTIC_CONFIG_FILE": "credentials-encryption.yaml",
                "JENTIC__ADMIN__AUTH__JWT_SECRET": "admin-jwt-secret",
                "JENTIC__ADMIN__INVITE__PEPPER": "admin-invite-pepper",
                "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET": "connect-state-secret",
            },
        ),
    ],
)
def test_render_app_secrets_split_follows_extra_env_apps(
    subchart: str, apps: str, expected: dict[str, str]
) -> None:
    """An extraEnv JENTIC__APPS override decides what the pod is handed.

    The loader's production guard keys off the apps the process runs, so the
    chart must too: keying off the subchart name alone would starve a re-roled
    pod of a secret it reads (or hand it ones it never reads).
    """
    docs = _manifests(
        *_ALL_SURFACES_ON,
        "--set",
        "global.appSecrets.generate=true",
        "--set",
        f"{subchart}.extraEnv.JENTIC__APPS={apps.replace(',', chr(92) + ',')}",
    )
    assert _app_secret_refs(_deployments(docs)[subchart]) == expected


# What chart versions before the per-concern split wrote: one config.yaml.
_OLD_CHART_CONFIG = (
    "credentials:\n  encryption:\n    active_id: v1\n    entries:\n"
    "      - id: v1\n        material: b2xkLWtleS1tYXRlcmlhbC0zMi1ieXRlcy1sb25nISE=\n"
    "  connect:\n    state_secret: old-state\n"
    "admin:\n  auth:\n    jwt_secret: old-jwt\n  invite:\n    pepper: old-pepper\n"
)


def _render_over_existing_secret(
    tmp_path: Path, existing: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    """Render generate mode as an upgrade over an existing app-secrets Secret.

    ``helm template`` has no cluster, so ``lookup`` returns nothing. A throwaway
    copy of the chart swaps that one call for a values-supplied object, so the
    reuse/derive logic runs exactly as it would against a live Secret.
    """
    if shutil.which("helm") is None:
        pytest.skip("helm not installed")
    chart = tmp_path / "chart"
    shutil.copytree(CHART_DIR, chart)
    template = chart / "templates" / "app-secrets.yaml"
    source = template.read_text()
    needle = 'lookup "v1" "Secret" .Release.Namespace $name'
    assert source.count(needle) == 1
    template.write_text(source.replace(needle, "(.Values.testExistingAppSecret | default dict)"))
    values = tmp_path / "existing.yaml"
    encoded = {k: base64.b64encode(v.encode()).decode() for k, v in existing.items()}
    values.write_text(yaml.safe_dump({"testExistingAppSecret": {"data": encoded}}))
    return subprocess.run(
        [
            "helm",
            "template",
            "jentic",
            str(chart),
            "-f",
            str(values),
            "--set",
            "global.appSecrets.generate=true",
            "--show-only",
            "templates/app-secrets.yaml",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _secret_data_over(tmp_path: Path, existing: dict[str, str], *args: str) -> dict[str, str]:
    result = _render_over_existing_secret(tmp_path, existing, *args)
    assert result.returncode == 0, result.stderr
    return _app_secrets_data(_manifests_from(result.stdout))


def _assert_legacy_matches_keys(data: dict[str, str]) -> None:
    legacy = yaml.safe_load(data["config.yaml"])
    keyset = yaml.safe_load(data["credentials-encryption.yaml"])
    assert legacy["credentials"]["encryption"] == keyset["credentials"]["encryption"]
    assert legacy["admin"]["auth"]["jwt_secret"] == data["admin-jwt-secret"]
    assert legacy["admin"]["invite"]["pepper"] == data["admin-invite-pepper"]
    assert legacy["credentials"]["connect"]["state_secret"] == data["connect-state-secret"]


@pytest.mark.smoke
@pytest.mark.parametrize("layout", ["split", "single"])
def test_render_app_secrets_upgrade_from_old_chart_keeps_values(
    tmp_path: Path, layout: str
) -> None:
    """Upgrading a pre-split release derives every key from its config.yaml.

    Nothing rotates, the legacy key keeps the same values (so a rollback to
    the old chart reads what the pods were using), and a second upgrade over
    the result is a no-op.
    """
    old = {"config.yaml": _OLD_CHART_CONFIG, "db-password-admin": "old-db"}
    data = _secret_data_over(tmp_path / "1", old, "--set", f"global.appSecrets.layout={layout}")
    assert data["admin-jwt-secret"] == "old-jwt"
    assert data["admin-invite-pepper"] == "old-pepper"
    assert data["connect-state-secret"] == "old-state"
    old_encryption = yaml.safe_load(_OLD_CHART_CONFIG)["credentials"]["encryption"]
    assert yaml.safe_load(data["credentials-encryption.yaml"]) == {
        "credentials": {"encryption": old_encryption}
    }
    assert data["db-password-admin"] == "old-db"
    assert yaml.safe_load(data["config.yaml"]) == yaml.safe_load(_OLD_CHART_CONFIG)
    _assert_legacy_matches_keys(data)
    again = _secret_data_over(tmp_path / "2", data, "--set", f"global.appSecrets.layout={layout}")
    assert again == data


@pytest.mark.smoke
def test_render_app_secrets_split_rejects_hand_added_legacy_settings(tmp_path: Path) -> None:
    """A config.yaml carrying extra settings blocks split, even after a single run.

    layout=single writes the per-concern keys too, so the check cannot rely on
    their absence: dropping back to split (say, an upgrade without the
    layout value) would otherwise silently stop mounting those settings.
    """
    edited = _OLD_CHART_CONFIG + "runtime:\n  log_level: DEBUG\n"
    first = _render_over_existing_secret(tmp_path / "1", {"config.yaml": edited})
    assert first.returncode != 0
    assert "runtime" in first.stderr
    assert "global.appSecrets.layout=single" in first.stderr
    data = _secret_data_over(
        tmp_path / "2", {"config.yaml": edited}, "--set", "global.appSecrets.layout=single"
    )
    # single keeps the hand-edited document verbatim.
    assert data["config.yaml"] == edited
    back_to_split = _render_over_existing_secret(tmp_path / "3", data)
    assert back_to_split.returncode != 0
    assert "runtime" in back_to_split.stderr


@pytest.mark.smoke
def test_render_app_secrets_legacy_follows_the_layout_pods_read(tmp_path: Path) -> None:
    """Whichever side the pods read is authoritative; the other follows it.

    split: a keyset rotated in place in credentials-encryption.yaml (new
    entry, new active_id) is written back into config.yaml, so a rollback
    can still decrypt what was written under the new key. single: a value
    changed in config.yaml flows into the per-concern key, so a later switch
    to split carries it rather than a stale copy.
    """
    base = _secret_data_over(tmp_path / "0", {"config.yaml": _OLD_CHART_CONFIG})
    rotated_keyset = yaml.safe_load(base["credentials-encryption.yaml"])
    encryption = rotated_keyset["credentials"]["encryption"]
    encryption["entries"].append(
        {"id": "v2", "material": "bmV3LWtleS1tYXRlcmlhbC0zMi1ieXRlcy1sb25nISE="}
    )
    encryption["active_id"] = "v2"
    split = _secret_data_over(
        tmp_path / "1", base | {"credentials-encryption.yaml": yaml.safe_dump(rotated_keyset)}
    )
    assert yaml.safe_load(split["config.yaml"])["credentials"]["encryption"] == encryption
    _assert_legacy_matches_keys(split)

    legacy = yaml.safe_load(base["config.yaml"])
    legacy["admin"]["auth"]["jwt_secret"] = "rotated-in-config-yaml"
    edited = base | {"config.yaml": yaml.safe_dump(legacy)}
    single = _secret_data_over(tmp_path / "2", edited, "--set", "global.appSecrets.layout=single")
    assert single["admin-jwt-secret"] == "rotated-in-config-yaml"
    assert single["config.yaml"] == edited["config.yaml"]
    _assert_legacy_matches_keys(single)


@pytest.mark.smoke
def test_render_parts_overlay_shares_jwt_secret() -> None:
    """The parts smoke overlay pins one jwt_secret across issuer + verifiers.

    Dev overlays use inline env (not appSecrets — control's configFile keyset
    is mutually exclusive with it), so the shared value must be stated
    explicitly on every surface that mints or checks admin JWTs. Without it,
    each pod generates its own per-process secret and POST /apis 401s — the
    exact failure the v0.38.2 release smoke caught.
    """
    result = _helm_template("-f", str(VALUES_DIR / "local-parts.yaml"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("name: JENTIC__ADMIN__AUTH__JWT_SECRET") == 3


@pytest.mark.smoke
def test_render_rejects_unquoted_numeric_password(tmp_path: Path) -> None:
    """A password YAML parsed as a number must fail the render, not be coerced.

    An unquoted `0123456789` reaches Helm as a float and renders as
    "1.23456789e+08" — the DB role would be created with a value the operator
    never typed, and nothing would report it. The error must name the path and
    say how to fix it.

    A values FILE is the only way to reproduce this: `--set` runs Helm's own
    strvals parser, which keeps a leading-zero literal a string, so the hazard is
    specific to the YAML parser reading the file.
    """
    numeric = tmp_path / "numeric.yaml"
    numeric.write_text("global:\n  databases:\n    registry:\n      password: 0123456789\n")
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "local-combined.yaml"),
        "-f",
        str(numeric),
    )
    assert result.returncode != 0
    assert "global.databases.registry.password must be quoted" in result.stderr
    # Quoting it is accepted, so the guard rejects the type and not the value.
    quoted = tmp_path / "quoted.yaml"
    quoted.write_text('global:\n  databases:\n    registry:\n      password: "0123456789"\n')
    ok = _helm_template(
        "-f",
        str(VALUES_DIR / "local-combined.yaml"),
        "-f",
        str(quoted),
    )
    assert ok.returncode == 0, ok.stderr
    assert 'value: "0123456789"' in ok.stdout


@pytest.mark.smoke
@pytest.mark.parametrize("values", ["local-combined", "local-parts", "local-broker"])
def test_render_never_floats_to_latest(values: str) -> None:
    """No rendered image may carry `:latest`, at any tag-resolution step.

    Each subchart's own appVersion is the last resort (`.Chart.AppVersion`
    resolves against the subchart, not the umbrella), and `_image.tpl` fails
    rather than falling back to a floating tag — migrating and serving must
    agree on a revision.
    """
    result = _helm_template("-f", str(VALUES_DIR / f"{values}.yaml"))
    assert result.returncode == 0, result.stderr
    images = [
        line.split("image:", 1)[1].strip().strip('"')
        for line in result.stdout.splitlines()
        if line.lstrip().startswith("image:")
    ]
    assert images, "no image references rendered"
    for image in images:
        assert not image.endswith(":latest"), f"floating tag rendered: {image}"
        assert ":" in image.rsplit("/", 1)[-1], f"untagged image rendered: {image}"


@pytest.mark.smoke
def test_render_tagless_install_uses_subchart_app_version() -> None:
    """With no tag set anywhere, each surface resolves its chart's appVersion."""
    docs = _manifests("--set", "broker.enabled=true")
    chart_version = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())["version"]
    images = {
        doc["metadata"]["name"]: doc["spec"]["template"]["spec"]["containers"][0]["image"]
        for doc in docs
        if doc.get("kind") == "Deployment"
    }
    assert images
    for name, image in images.items():
        assert image.endswith(f":{chart_version}"), f"{name} resolved {image}"


@pytest.mark.smoke
def test_render_jentic_env_production_by_default() -> None:
    """Every service pod defaults to JENTIC_ENV=production.

    Production mode is what arms the placeholder-secret guards, so it has to be
    the default rather than something an operator remembers to pass.
    """
    docs = _manifests(
        "--set",
        "broker.enabled=true",
        "--set",
        "global.appSecrets.generate=true",
        "--set",
        "postgresql.auth.password=x",
    )
    deployments = [doc for doc in docs if doc.get("kind") == "Deployment"]
    assert deployments
    for doc in deployments:
        container = doc["spec"]["template"]["spec"]["containers"][0]
        entries = [e for e in _env(container) if e["name"] == "JENTIC_ENV"]
        assert entries == [{"name": "JENTIC_ENV", "value": "production"}], doc["metadata"]["name"]


@pytest.mark.smoke
def test_render_migrate_job_stays_out_of_production_mode() -> None:
    """The migration Job must NOT get JENTIC_ENV=production.

    Its runner validates the whole AppConfig through load_config() but is handed
    only the DB env — never the appSecrets config file the service pods mount. In
    production mode the placeholder guards would fire on secrets it cannot see
    and fail every install, for a job that touches no secrets.
    """
    docs = _manifests(
        "-f",
        str(VALUES_DIR / "local-combined.yaml"),
    )
    job = next(doc for doc in docs if doc.get("kind") == "Job")
    for container in job["spec"]["template"]["spec"]["containers"]:
        assert "JENTIC_ENV" not in _env_names(container)


@pytest.mark.smoke
def test_render_extra_env_wins_on_duplicate_keys() -> None:
    """extraEnv is emitted last, so it overrides any chart-set variable.

    Kubernetes takes the last entry when a name repeats, so ordering is the
    override mechanism — and JENTIC_ENV is additionally suppressed at source so
    the pod spec carries exactly one entry rather than a confusing pair.
    """
    docs = _manifests(
        "--set",
        "app.extraEnv.JENTIC_ENV=development",
        "--set",
        "app.extraEnv.JENTIC__OBSERVABILITY__METRICS__EXPORTER=none",
    )
    app = next(
        doc
        for doc in docs
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "jentic-app"
    )
    container = app["spec"]["template"]["spec"]["containers"][0]
    entries = _env(container)
    jentic_env = [e for e in entries if e["name"] == "JENTIC_ENV"]
    assert jentic_env == [{"name": "JENTIC_ENV", "value": "development"}]
    # The exporter IS set by the chart, so both entries render — the operator's
    # must come second.
    exporters = [
        i for i, e in enumerate(entries) if e["name"] == "JENTIC__OBSERVABILITY__METRICS__EXPORTER"
    ]
    assert len(exporters) == 2, entries
    assert entries[exporters[-1]]["value"] == "none"


@pytest.mark.smoke
def test_render_dead_logging_values_are_gone() -> None:
    """`global.observability.logging.format` no longer exists, and level is opt-in.

    Both used to render env vars the application never reads. `level` has a real
    counterpart (JENTIC__RUNTIME__LOG_LEVEL) so it is wired through, but only
    when set — an always-on env var would silently outrank a mounted config file.
    """
    default = _manifests()
    for doc in default:
        if doc.get("kind") != "Deployment":
            continue
        names = _env_names(doc["spec"]["template"]["spec"]["containers"][0])
        assert "JENTIC__OBSERVABILITY__LOGGING__FORMAT" not in names
        assert "JENTIC__RUNTIME__LOG_LEVEL" not in names
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
    assert "format" not in values["global"]["observability"]["logging"]
    # Setting level does reach the pod.
    docs = _manifests("--set", "global.observability.logging.level=debug")
    app = next(doc for doc in docs if doc.get("kind") == "Deployment")
    assert {"name": "JENTIC__RUNTIME__LOG_LEVEL", "value": "debug"} in _env(
        app["spec"]["template"]["spec"]["containers"][0]
    )


@pytest.mark.smoke
def test_render_migrate_job_extra_args() -> None:
    """migrate.extraArgs reaches the runner; by default the Job passes none.

    It is the Helm escape hatch for a post-migration upgrade step that blocks
    an upgrade (`--skip-upgrade-step NAME`) — the command itself is fixed.
    """
    default = _manifests("-f", str(VALUES_DIR / "local-combined.yaml"))
    job = next(doc for doc in default if doc.get("kind") == "Job")
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "jentic_one.migrations.run"]
    assert "args" not in container

    docs = _manifests(
        "-f",
        str(VALUES_DIR / "local-combined.yaml"),
        "--set-json",
        'migrate.extraArgs=["--skip-upgrade-step","example_step"]',
    )
    job = next(doc for doc in docs if doc.get("kind") == "Job")
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["args"] == ["--skip-upgrade-step", "example_step"]


@pytest.mark.smoke
def test_render_migrate_job_hook_ordering() -> None:
    """Install runs the migration after the DB exists; upgrade runs it before pods roll.

    post-install because a first install has no Postgres to connect to until the
    StatefulSet is created; pre-upgrade so new pods never serve against the old
    schema and a failed migration aborts the upgrade with the old pods intact.
    """
    docs = _manifests("-f", str(VALUES_DIR / "local-combined.yaml"))
    job = next(doc for doc in docs if doc.get("kind") == "Job")
    annotations = job["metadata"]["annotations"]
    assert annotations["helm.sh/hook"] == "post-install,pre-upgrade"
    assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation"


@pytest.mark.smoke
@pytest.mark.parametrize("values", ["local-combined", "local-parts", "local-broker"])
def test_render_security_context_on_every_workload(values: str) -> None:
    """Every chart-owned workload runs non-root with a read-only root filesystem.

    A pod-level runAsNonRoot is only verifiable when the uid is numeric, which is
    why the images pin `USER 10001`; a container-level context is what actually
    drops capabilities, since pod-level cannot express them.
    """
    docs = _manifests("-f", str(VALUES_DIR / f"{values}.yaml"))
    workloads = [doc for doc in docs if doc.get("kind") in HARDENED_KINDS]
    assert workloads
    for doc in workloads:
        where = f"{doc['kind']}/{doc['metadata']['name']}"
        spec = doc["spec"]["template"]["spec"]
        pod_ctx = spec.get("securityContext") or {}
        assert pod_ctx.get("runAsNonRoot") is True, where
        assert isinstance(pod_ctx.get("runAsUser"), int), where
        containers = (spec.get("initContainers") or []) + spec["containers"]
        for container in containers:
            ctx = container.get("securityContext") or {}
            label = f"{where}:{container['name']}"
            assert ctx.get("allowPrivilegeEscalation") is False, label
            assert ctx.get("readOnlyRootFilesystem") is True, label
            assert ctx.get("capabilities", {}).get("drop") == ["ALL"], label


@pytest.mark.smoke
@pytest.mark.parametrize("values", ["local-combined", "local-parts", "local-broker"])
def test_render_read_only_root_has_writable_tmp(values: str) -> None:
    """readOnlyRootFilesystem is paired with a writable /tmp on every container.

    Python's tempfile (and anything it underpins) needs one, so the flag without
    the mount turns into a runtime failure a `helm template` cannot catch.
    """
    docs = _manifests("-f", str(VALUES_DIR / f"{values}.yaml"))
    for doc in docs:
        if doc.get("kind") not in HARDENED_KINDS:
            continue
        spec = doc["spec"]["template"]["spec"]
        containers = (spec.get("initContainers") or []) + spec["containers"]
        for container in containers:
            if not (container.get("securityContext") or {}).get("readOnlyRootFilesystem"):
                continue
            paths = {m["mountPath"] for m in container.get("volumeMounts") or []}
            assert "/tmp" in paths, f"{doc['metadata']['name']}:{container['name']}"


@pytest.mark.smoke
def test_render_optional_cluster_resources_off_by_default() -> None:
    """Ingress, PDB and NetworkPolicy render nothing unless asked for.

    Each changes what an existing release owns, so a chart upgrade must not
    start creating them.
    """
    kinds = {doc.get("kind") for doc in _manifests("-f", str(VALUES_DIR / "local-parts.yaml"))}
    optional = {"Ingress", "PodDisruptionBudget", "NetworkPolicy", "HorizontalPodAutoscaler"}
    assert not kinds & optional


@pytest.mark.smoke
def test_render_ingress_requires_trusted_proxies() -> None:
    """An Ingress without trusted proxies must fail the install, not degrade quietly.

    The OAuth limiters key on the socket address, so behind an ingress controller
    every client shares one bucket and a single noisy caller locks the fleet out
    of /authorize. The chart cannot discover the controller's addresses.
    """
    result = _helm_template(
        "-f",
        str(VALUES_DIR / "local-parts.yaml"),
        "--set",
        "ingress.enabled=true",
        "--set",
        "ingress.hosts[0].host=jentic.example.com",
    )
    assert result.returncode != 0
    assert "JENTIC__AUTH__OAUTH_RATE_LIMIT__TRUSTED_PROXIES" in result.stderr


@pytest.mark.smoke
def test_render_ingress_routes_to_release_entry_point() -> None:
    """The Ingress backs onto the gateway in parts mode and the app in combined.

    The gateway's nginx config already owns the prefix-to-surface map, so the
    Ingress routes to whichever service fronts the release rather than keeping a
    second copy of that map.
    """
    args = (
        "--set",
        "ingress.enabled=true",
        "--set",
        "ingress.hosts[0].host=jentic.example.com",
        "--set",
        "ingress.skipTrustedProxiesCheck=true",
    )
    for values, expected in (("local-parts", "jentic-gateway"), ("local-combined", "jentic-app")):
        docs = _manifests("-f", str(VALUES_DIR / f"{values}.yaml"), *args)
        ingress = next(doc for doc in docs if doc.get("kind") == "Ingress")
        rule = ingress["spec"]["rules"][0]
        assert rule["host"] == "jentic.example.com"
        backend = rule["http"]["paths"][0]["backend"]["service"]
        assert backend["name"] == expected, values
        assert backend["port"]["number"] == 8000


@pytest.mark.smoke
def test_render_pdb_and_network_policy_cover_enabled_surfaces() -> None:
    """Turning them on covers exactly the enabled surfaces, and spares broker egress.

    The broker's job is calling arbitrary third-party APIs, so a default-deny
    egress policy would break the data plane — it stays Ingress-only.
    """
    docs = _manifests(
        "-f",
        str(VALUES_DIR / "local-parts.yaml"),
        "--set",
        "podDisruptionBudget.enabled=true",
        "--set",
        "networkPolicy.enabled=true",
        "--set",
        "networkPolicy.restrictEgress=true",
    )
    surfaces = {"registry", "admin", "control", "broker", "gateway"}
    named = {
        doc["metadata"]["labels"]["app.kubernetes.io/name"]
        for doc in docs
        if doc.get("kind") == "PodDisruptionBudget"
    }
    assert named == surfaces
    policies = {
        doc["metadata"]["labels"]["app.kubernetes.io/name"]: doc
        for doc in docs
        if doc.get("kind") == "NetworkPolicy"
    }
    # The bundled database is policed too — a surface-only set would leave it
    # reachable from every pod in the cluster — but it gets no budget, being a
    # single instance over one PVC.
    assert set(policies) == surfaces | {"postgresql"}
    assert policies["broker"]["spec"]["policyTypes"] == ["Ingress"]
    assert "Egress" in policies["registry"]["spec"]["policyTypes"]

    db = policies["postgresql"]["spec"]
    assert db["policyTypes"] == ["Ingress"], "the database initiates nothing"
    (rule,) = db["ingress"]
    assert rule["from"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/instance": "jentic"}}}
    ], "only this release's pods, not allowFromNamespaces"
    assert rule["ports"] == [{"port": 5432, "protocol": "TCP"}]


@pytest.mark.smoke
def test_render_bundled_postgres_raises_connection_ceiling() -> None:
    """The bundled Postgres runs above the image's default max_connections.

    Each surface holds a pool per database (three databases, pool_max 10), so
    parts mode alone asks for ~120 connections against a default ceiling of 100 —
    exhaustion before a single replica is added.
    """
    docs = _manifests("-f", str(VALUES_DIR / "local-parts.yaml"))
    sts = next(doc for doc in docs if doc.get("kind") == "StatefulSet")
    container = sts["spec"]["template"]["spec"]["containers"][0]
    assert container["args"] == ["-c", "max_connections=200"]
