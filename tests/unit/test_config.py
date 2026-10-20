"""Tests for configuration loading and validation."""

from __future__ import annotations

import base64
import builtins
import copy
import io
import os
import threading
import time
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog.testing
import yaml
from pydantic import SecretStr, ValidationError

from jentic_one.shared import config as config_module
from jentic_one.shared.config import (
    _ONESHOT_CONFIG_CACHE,
    AdminAuthConfig,
    AdminInviteConfig,
    AppConfig,
    AuthConfig,
    BrokerResilienceConfig,
    CatalogConfig,
    CliFacadesConfig,
    ConfigError,
    ConnectConfig,
    ControlEgressConfig,
    CredentialsConfig,
    DirectOAuth2ProviderConfig,
    EgressConfig,
    EncryptionConfig,
    EncryptionKey,
    EntitlementConfig,
    HeldExecutionsConfig,
    IdempotencyConfig,
    OAuthRateLimitConfig,
    RuntimeConfig,
    SigningKeyConfig,
    TelemetryConfig,
    TrustedIssuerConfig,
    WorkerConfig,
    _apps_for_secret_guard,
    _csv_to_list,
    _deep_merge,
    _env_overrides,
    _oneshot_cache_key,
    bind_origin,
    broker_links_root_on_broker,
    check_public_url_consistency,
    connect_approval_url,
    effective_auth_base_url,
    has_spa_platform_client,
    load_config,
    resolved_auth_base_url,
    resolved_control_egress,
)
from jentic_one.shared.state import (
    MemoryBackendConfig,
    MemoryStateBackend,
    NamespaceBudget,
    StateBackendConfig,
    build_state_backend,
)


def test_loads_valid_yaml(config_file: Path, sample_config_dict: dict[str, Any]):
    config = load_config(config_file)
    assert isinstance(config, AppConfig)
    assert config.databases.registry.host == "db.local"
    assert config.databases.admin.port == 5432
    assert config.databases.control.name == "jentic"


def test_loads_minimal_config(tmp_path: Path):
    minimal = {
        "databases": {
            "registry": {"name": "reg"},
            "admin": {"name": "admin"},
            "control": {"name": "ctrl"},
        }
    }
    path = tmp_path / "minimal.yaml"
    path.write_text(yaml.dump(minimal))
    config = load_config(path)
    assert config.databases.registry.host == "localhost"
    assert config.services.request_timeout_s == 30.0
    assert config.runtime.debug is False


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.yaml")


def test_missing_required_field_raises(tmp_path: Path):
    incomplete = {"databases": {"registry": {"name": "r"}, "admin": {"name": "j"}}}
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.dump(incomplete))
    with pytest.raises(ConfigError, match="validation failed"):
        load_config(path)


def test_env_overrides_file_values(config_file: Path):
    env = {"JENTIC__DATABASES__REGISTRY__HOST": "override-host"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.databases.registry.host == "override-host"
    assert config.databases.admin.host == "db.local"


def test_env_only_config(tmp_path: Path):
    env = {
        "JENTIC__DATABASES__REGISTRY__NAME": "r",
        "JENTIC__DATABASES__REGISTRY__HOST": "h1",
        "JENTIC__DATABASES__ADMIN__NAME": "j",
        "JENTIC__DATABASES__ADMIN__HOST": "h2",
        "JENTIC__DATABASES__CONTROL__NAME": "c",
        "JENTIC__DATABASES__CONTROL__HOST": "h3",
        "JENTIC__RUNTIME__DEBUG": "true",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(tmp_path / "nonexist.yaml" if False else None)
    assert config.databases.registry.name == "r"
    assert config.runtime.debug is True


def test_env_coerces_int(config_file: Path):
    env = {"JENTIC__DATABASES__REGISTRY__PORT": "9999"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.databases.registry.port == 9999


@pytest.mark.parametrize("seconds", ["0", "-1"])
def test_events_stream_recheck_seconds_default_env_and_bounds(config_file: Path, seconds: str):
    assert load_config(config_file).admin.events.stream_recheck_seconds == 5.0

    env = {"JENTIC__ADMIN__EVENTS__STREAM_RECHECK_SECONDS": "2.5"}
    with patch.dict(os.environ, env, clear=False):
        assert load_config(config_file).admin.events.stream_recheck_seconds == 2.5

    env = {"JENTIC__ADMIN__EVENTS__STREAM_RECHECK_SECONDS": seconds}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(ConfigError, match="stream_recheck_seconds"),
    ):
        load_config(config_file)


def test_http_wire_trace_defaults_off_and_env_enables(config_file: Path):
    assert load_config(config_file).logging.http_wire_trace is False
    with patch.dict(os.environ, {"JENTIC__LOGGING__HTTP_WIRE_TRACE": "true"}, clear=False):
        config = load_config(config_file)
    assert config.logging.http_wire_trace is True


@pytest.mark.parametrize("ratio", ["-0.1", "1.5"])
def test_tracing_sample_ratio_default_yaml_env_and_bounds(
    config_file: Path, tmp_path: Path, sample_config_dict: dict[str, Any], ratio: str
):
    assert load_config(config_file).observability.tracing.sample_ratio == 1.0

    from_yaml = tmp_path / "ratio.yaml"
    from_yaml.write_text(
        yaml.dump({**sample_config_dict, "observability": {"tracing": {"sample_ratio": 0.25}}})
    )
    assert load_config(from_yaml).observability.tracing.sample_ratio == 0.25

    env = {"JENTIC__OBSERVABILITY__TRACING__SAMPLE_RATIO": "0.1"}
    with patch.dict(os.environ, env, clear=False):
        assert load_config(config_file).observability.tracing.sample_ratio == 0.1

    env = {"JENTIC__OBSERVABILITY__TRACING__SAMPLE_RATIO": ratio}
    with patch.dict(os.environ, env, clear=False), pytest.raises(ConfigError, match="sample_ratio"):
        load_config(config_file)


def test_control_connect_manual_flows_default_on_and_env_overrides(
    config_file: Path, tmp_path: Path, sample_config_dict: dict[str, Any]
):
    config = load_config(config_file)
    assert config.control.connect.manual_flows_enabled is True
    assert config.control.connect.manual_flows_ttl_hours == 72

    env = {
        "JENTIC__CONTROL__CONNECT__MANUAL_FLOWS_ENABLED": "false",
        "JENTIC__CONTROL__CONNECT__MANUAL_FLOWS_TTL_HOURS": "24",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.control.connect.manual_flows_enabled is False
    assert config.control.connect.manual_flows_ttl_hours == 24

    from_yaml = tmp_path / "manual.yaml"
    from_yaml.write_text(
        yaml.dump({**sample_config_dict, "control": {"connect": {"manual_flows_ttl_hours": 12}}})
    )
    assert load_config(from_yaml).control.connect.manual_flows_ttl_hours == 12

    bad = tmp_path / "bad-ttl.yaml"
    bad.write_text(
        yaml.dump({**sample_config_dict, "control": {"connect": {"manual_flows_ttl_hours": 0}}})
    )
    with pytest.raises(ConfigError, match="manual_flows_ttl_hours"):
        load_config(bad)


def test_env_coerces_float(config_file: Path):
    env = {"JENTIC__SERVICES__REQUEST_TIMEOUT_S": "60.5"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.services.request_timeout_s == 60.5


def test_env_indexed_keys_build_list_of_models(config_file: Path):
    # JENTIC__AUTH__ID_SIGNING__0__* addresses a list index; the env parser
    # can only build dicts, so this exercises the digit-keyed dict -> list
    # coercion that lets list[SigningKeyConfig] validate.
    env = {
        "JENTIC__AUTH__ID_SIGNING__0__KID": "k0",
        "JENTIC__AUTH__ID_SIGNING__0__PRIVATE_KEY_PEM": "pem-zero",
        "JENTIC__AUTH__ID_SIGNING__1__KID": "k1",
        "JENTIC__AUTH__ID_SIGNING__1__PRIVATE_KEY_PEM": "pem-one",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert [k.kid for k in config.auth.id_signing] == ["k0", "k1"]
    assert config.auth.id_signing[0].private_key_pem.get_secret_value() == "pem-zero"


def test_env_overrides_coerces_contiguous_digit_dict_to_list():
    env = {
        "JENTIC__AUTH__ID_SIGNING__0__KID": "k0",
        "JENTIC__AUTH__ID_SIGNING__1__KID": "k1",
    }
    with patch.dict(os.environ, env, clear=False):
        overrides = _env_overrides()
    signing = overrides["auth"]["id_signing"]
    assert isinstance(signing, list)
    assert [item["kid"] for item in signing] == ["k0", "k1"]


def test_env_overrides_leaves_non_indexed_dicts_untouched():
    # Real string keys (databases.registry) must stay a dict, and a sparse /
    # 1-based numeric set must NOT be coerced (it isn't a valid list address).
    env = {
        "JENTIC__DATABASES__REGISTRY__HOST": "h",
        "JENTIC__WIDGETS__1__NAME": "one",  # missing index 0 -> not a list
    }
    with patch.dict(os.environ, env, clear=False):
        overrides = _env_overrides()
    assert overrides["databases"]["registry"] == {"host": "h"}
    assert overrides["widgets"] == {"1": {"name": "one"}}


def test_numeric_password_preserved_as_string(config_file: Path):
    env = {"JENTIC__DATABASES__REGISTRY__PASSWORD": "123456"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.databases.registry.password.get_secret_value() == "123456"


def test_default_jwt_secret_generated_in_development():
    """With no jwt_secret configured, dev mints a random per-process secret.

    The shipped default is empty — images must not contain a secret-shaped
    literal (AWS Marketplace container policy) — so zero-config local dev
    relies on this generation. It must be non-empty and stable across repeated
    config loads in one process, or every re-read would invalidate sessions.
    """
    with patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False):
        first = AdminAuthConfig()
        second = AdminAuthConfig()
    generated = first.jwt_secret.get_secret_value()
    assert generated.strip()
    assert generated == second.jwt_secret.get_secret_value()


def test_generated_dev_secrets_differ_per_field():
    """The dev generator must not reuse one value across different secrets.

    jwt_secret / pepper / state_secret have different blast radii; a shared
    value would let one surface forge another's artifacts (e.g. sign an admin
    JWT with the connect state secret).
    """
    with patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False):
        jwt = AdminAuthConfig().jwt_secret.get_secret_value()
        pepper = AdminInviteConfig().pepper.get_secret_value()
        state = ConnectConfig().state_secret.get_secret_value()
    assert len({jwt, pepper, state}) == 3


def test_default_jwt_secret_rejected_in_production():
    """A default jwt_secret in production is a hard ConfigError.

    jenticctl install generates a real secret, so the placeholder reaching a
    production boot means the install step was skipped — fail fast rather than
    sign tokens with a publicly-known key.
    """
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match=r"admin\.auth\.jwt_secret"),
    ):
        AdminAuthConfig()


def test_explicit_jwt_secret_accepted_in_production():
    with patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False):
        cfg = AdminAuthConfig(jwt_secret=SecretStr("a-real-generated-secret"))
    assert cfg.jwt_secret.get_secret_value() == "a-real-generated-secret"


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_jwt_secret_rejected_in_production(blank: str):
    """A blank/whitespace jwt_secret in production is as dangerous as the default.

    Rejecting only the literal placeholder would let an empty value through and
    sign tokens with an effectively-known key, so the guard must fail closed on
    any non-meaningful secret.
    """
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match=r"admin\.auth\.jwt_secret"),
    ):
        AdminAuthConfig(jwt_secret=SecretStr(blank))


@pytest.mark.parametrize("placeholder", ["change-me-in-production", "ChangeMe-2026"])
def test_placeholder_jwt_secret_rejected_in_production(placeholder: str):
    """A change-me placeholder in production is as unsafe as an empty value.

    Placeholder values come from published examples and configs, so they are
    publicly known — signing tokens with one means anyone can forge admin
    JWTs. Boot must fail closed, exactly as it does for a blank.
    """
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match=r"admin\.auth\.jwt_secret"),
    ):
        AdminAuthConfig(jwt_secret=SecretStr(placeholder))


def test_placeholder_jwt_secret_replaced_in_development():
    """A change-me placeholder in dev is treated as unset, never signed with.

    The generated per-process secret takes its place, so a copied example
    config still boots locally without ever using the publicly-known value.
    """
    with patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False):
        cfg = AdminAuthConfig(jwt_secret=SecretStr("change-me-in-production"))
    generated = cfg.jwt_secret.get_secret_value()
    assert generated.strip()
    assert generated != "change-me-in-production"


def test_session_lifetime_defaults():
    """Defaults: 1 h JWT, 12 h absolute session window."""
    with patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False):
        cfg = AdminAuthConfig()
    assert cfg.jwt_ttl_seconds == 3600
    assert cfg.session_ttl_seconds == 43200


def test_session_lifetimes_env_override(config_file: Path):
    env = {
        "JENTIC__ADMIN__AUTH__JWT_TTL_SECONDS": "600",
        "JENTIC__ADMIN__AUTH__SESSION_TTL_SECONDS": "7200",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.admin.auth.jwt_ttl_seconds == 600
    assert config.admin.auth.session_ttl_seconds == 7200


def test_session_window_shorter_than_jwt_ttl_rejected():
    """A session window shorter than one JWT would break every refresh."""
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False),
        pytest.raises(ValidationError, match="session_ttl_seconds"),
    ):
        AdminAuthConfig(jwt_ttl_seconds=3600, session_ttl_seconds=60)


@pytest.mark.parametrize("field", ["jwt_ttl_seconds", "session_ttl_seconds"])
def test_non_positive_session_lifetimes_rejected(field: str):
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False),
        pytest.raises(ValidationError, match=field),
    ):
        AdminAuthConfig.model_validate({field: 0})


def test_held_executions_defaults(tmp_path: Path):
    """A minimal config gets the 24 h TTL, a pending cap of 10, and 24 h retention."""
    minimal = {
        "databases": {
            "registry": {"name": "reg"},
            "admin": {"name": "admin"},
            "control": {"name": "ctrl"},
        }
    }
    path = tmp_path / "minimal.yaml"
    path.write_text(yaml.dump(minimal))
    cfg = load_config(path).held_executions
    assert cfg.ttl_seconds == 86_400
    assert cfg.max_pending_per_agent == 10
    assert cfg.result_retention_seconds == 86_400


def test_held_executions_yaml_and_env_override(tmp_path: Path):
    """The section is top-level: YAML sets it and ``JENTIC__HELD_EXECUTIONS__*`` wins."""
    doc = {
        "databases": {
            "registry": {"name": "reg"},
            "admin": {"name": "admin"},
            "control": {"name": "ctrl"},
        },
        "held_executions": {"ttl_seconds": 600, "max_pending_per_agent": 3},
    }
    path = tmp_path / "ea.yaml"
    path.write_text(yaml.dump(doc))
    assert load_config(path).held_executions.ttl_seconds == 600
    env = {
        "JENTIC__HELD_EXECUTIONS__TTL_SECONDS": "120",
        "JENTIC__HELD_EXECUTIONS__MAX_PENDING_PER_AGENT": "5",
        "JENTIC__HELD_EXECUTIONS__RESULT_RETENTION_SECONDS": "3600",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = load_config(path).held_executions
    assert cfg.ttl_seconds == 120
    assert cfg.max_pending_per_agent == 5
    assert cfg.result_retention_seconds == 3600


@pytest.mark.parametrize(
    "field", ["ttl_seconds", "max_pending_per_agent", "result_retention_seconds"]
)
def test_held_executions_non_positive_rejected(field: str):
    with pytest.raises(ValidationError, match=field):
        HeldExecutionsConfig.model_validate({field: 0})


_MINIMAL_DATABASES = {
    "registry": {"name": "reg"},
    "admin": {"name": "admin"},
    "control": {"name": "ctrl"},
}


def test_cli_facades_defaults(tmp_path: Path):
    """Off by default: every vendor disabled, github in host mode, 10 min TTL, 1 h cap."""
    path = tmp_path / "minimal.yaml"
    path.write_text(yaml.dump({"databases": _MINIMAL_DATABASES}))
    cfg = load_config(path).cli_facades
    assert cfg.enabled is False
    assert cfg.vendors.github.mode == "host"
    assert cfg.vendors.cloudflare.mode == "path"
    assert cfg.vendors.aws.mode == "path"
    assert not any(cfg.vendor_enabled(v) for v in ("github", "cloudflare", "aws"))
    assert cfg.invocation_ttl_seconds == 600
    assert cfg.invocation_hard_cap_seconds == 3600
    assert cfg.mint_rate_limit_per_minute == 60


def test_cli_facades_yaml_and_env_override(tmp_path: Path):
    """The section is top-level: YAML sets it and ``JENTIC__CLI_FACADES__*`` wins."""
    doc = {
        "databases": _MINIMAL_DATABASES,
        "cli_facades": {
            "enabled": True,
            "vendors": {"cloudflare": {"enabled": True}},
            "invocation_ttl_seconds": 300,
        },
    }
    path = tmp_path / "cf.yaml"
    path.write_text(yaml.dump(doc))
    cfg = load_config(path).cli_facades
    assert cfg.vendor_enabled("cloudflare")
    assert not cfg.vendor_enabled("github")
    assert cfg.invocation_ttl_seconds == 300
    env = {
        "JENTIC__CLI_FACADES__VENDORS__GITHUB__ENABLED": "true",
        "JENTIC__CLI_FACADES__VENDORS__GITHUB__HOST": "GitHub.Jentic.Example.com",
        "JENTIC__CLI_FACADES__VENDORS__GITHUB__PUBLIC_ADDRESS": "gh.example.com",
        "JENTIC__CLI_FACADES__MINT_RATE_LIMIT_PER_MINUTE": "5",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = load_config(path).cli_facades
    assert cfg.vendor_enabled("github")
    assert cfg.vendors.github.host == "github.jentic.example.com"
    assert cfg.vendors.github.public_address == "gh.example.com"
    assert cfg.mint_rate_limit_per_minute == 5


def test_cli_facades_master_switch_gates_every_vendor():
    cfg = CliFacadesConfig.model_validate({"vendors": {"aws": {"enabled": True}}})
    assert not cfg.vendor_enabled("aws")
    assert not CliFacadesConfig(enabled=True).vendor_enabled("gitlab")


@pytest.mark.parametrize(
    ("doc", "match"),
    [
        ({"vendors": {"github": {"mode": "path"}}}, "github.mode"),
        ({"vendors": {"cloudflare": {"mode": "host"}}}, "cloudflare.mode"),
        ({"vendors": {"aws": {"mode": "host", "host": "aws.example.com"}}}, "aws.mode"),
        ({"vendors": {"aws": {"host": "aws.example.com"}}}, "only used in 'host' mode"),
        ({"vendors": {"github": {"enabled": True}}}, "requires host"),
        ({"vendors": {"github": {"host": "https://github.example.com"}}}, "bare hostname"),
        ({"vendors": {"github": {"host": "github.example.com:8443"}}}, "bare hostname"),
        ({"vendors": {"gitlab": {"mode": "host"}}}, "gitlab"),
        ({"invocation_ttl_seconds": 0}, "invocation_ttl_seconds"),
        ({"invocation_hard_cap_seconds": 0}, "invocation_hard_cap_seconds"),
        ({"invocation_ttl_seconds": 900, "invocation_hard_cap_seconds": 600}, "hard_cap"),
        ({"mint_rate_limit_per_minute": 0}, "mint_rate_limit_per_minute"),
    ],
)
def test_cli_facades_invalid_rejected(doc: dict[str, Any], match: str):
    with pytest.raises(ValidationError, match=match):
        CliFacadesConfig.model_validate(doc)


def test_cli_facades_git_defaults_and_overrides(tmp_path: Path):
    """Governed git rides the GitHub facade's switch; its limits default to GitHub's push cap."""
    cfg = CliFacadesConfig()
    assert cfg.git.enabled is True
    assert cfg.git.max_push_bytes == 2 * 1024 * 1024 * 1024
    assert not cfg.git_enabled()
    on = CliFacadesConfig.model_validate(
        {"enabled": True, "vendors": {"github": {"enabled": True, "host": "gh.example.com"}}}
    )
    assert on.git_enabled()
    on.git.enabled = False
    assert not on.git_enabled()

    path = tmp_path / "git.yaml"
    path.write_text(
        yaml.dump({"databases": _MINIMAL_DATABASES, "cli_facades": {"git": {"idle_timeout_s": 30}}})
    )
    with patch.dict(os.environ, {"JENTIC__CLI_FACADES__GIT__MAX_PUSH_BYTES": "1024"}, clear=False):
        loaded = load_config(path).cli_facades.git
    assert loaded.idle_timeout_s == 30
    assert loaded.max_push_bytes == 1024
    with pytest.raises(ValidationError, match="max_push_bytes"):
        CliFacadesConfig.model_validate({"git": {"max_push_bytes": 0}})


def test_catalog_jitter_ratio_default():
    """The sweep jitter ratio defaults to a bounded 15%."""
    assert CatalogConfig().update_sweep_jitter_ratio == 0.15


@pytest.mark.parametrize("bad", [-0.01, 1.01, 10.0])
def test_catalog_jitter_ratio_out_of_bounds_rejected(bad: float):
    """The ratio is bounded [0, 1]: a value outside that fails fast at load.

    Without the Field(ge=0, le=1) bound a value like 10.0 would silently make the
    "cadence stays ~daily" contract false (up to 11x the interval), so reject it.
    """
    with pytest.raises(ValidationError, match="update_sweep_jitter_ratio"):
        CatalogConfig.model_validate({"update_sweep_jitter_ratio": bad})


def test_catalog_jitter_ratio_bounds_accepted():
    """The inclusive bounds 0.0 (disable) and 1.0 (max) are both valid."""
    assert CatalogConfig(update_sweep_jitter_ratio=0.0).update_sweep_jitter_ratio == 0.0
    assert CatalogConfig(update_sweep_jitter_ratio=1.0).update_sweep_jitter_ratio == 1.0


def test_default_invite_pepper_rejected_in_production():
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match=r"admin\.invite\.pepper"),
    ):
        AdminInviteConfig()


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_invite_pepper_rejected_in_production(blank: str):
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match=r"admin\.invite\.pepper"),
    ):
        AdminInviteConfig(pepper=SecretStr(blank))


def test_explicit_invite_pepper_accepted_in_production():
    with patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False):
        cfg = AdminInviteConfig(pepper=SecretStr("a-real-generated-pepper"))
    assert cfg.pepper.get_secret_value() == "a-real-generated-pepper"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_connect_state_secret_rejected_in_production(blank: str):
    """The connect state_secret gets the same fail-closed posture as the rest.

    It signs the OAuth connect state; running production with a generated
    per-process value would break multi-replica deployments silently, so a
    missing value must be a boot error, not a fallback.
    """
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match=r"credentials\.connect\.state_secret"),
    ):
        ConnectConfig(state_secret=SecretStr(blank))


def test_explicit_connect_state_secret_accepted_in_production():
    with patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False):
        cfg = ConnectConfig(state_secret=SecretStr("a-real-generated-state-secret"))
    assert cfg.state_secret.get_secret_value() == "a-real-generated-state-secret"


_ALL_GUARDED = {
    "JENTIC__ADMIN__AUTH__JWT_SECRET": "admin.auth.jwt_secret",
    "JENTIC__ADMIN__INVITE__PEPPER": "admin.invite.pepper",
    "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET": "credentials.connect.state_secret",
}


@pytest.mark.parametrize(
    ("apps", "required"),
    [
        # Mirrors the Helm chart's per-surface mounts (charts/common _app-secrets.tpl).
        ("broker", set()),
        ("registry", {"JENTIC__ADMIN__AUTH__JWT_SECRET"}),
        (
            "control",
            {"JENTIC__ADMIN__AUTH__JWT_SECRET", "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET"},
        ),
        ("admin,auth", {"JENTIC__ADMIN__AUTH__JWT_SECRET", "JENTIC__ADMIN__INVITE__PEPPER"}),
        # Auth issues session JWTs and derives its flow keys from the JWT secret.
        ("auth", {"JENTIC__ADMIN__AUTH__JWT_SECRET"}),
        ("registry,admin,control,auth", set(_ALL_GUARDED)),
        # An unaudited (e.g. extension) surface keeps the guard strict.
        ("broker,enterprise-thing", set(_ALL_GUARDED)),
    ],
)
def test_production_guard_requires_only_secrets_enabled_surfaces_read(
    config_file: Path, apps: str, required: set[str]
):
    """In production each standalone surface needs only the secrets it reads.

    Every required secret missing on its own is a boot error naming the field;
    with all required secrets present the config loads, and the secrets the
    surfaces never read are a per-process random value, never empty.
    """
    for missing in required:
        env = {"JENTIC_ENV": "production", "JENTIC__APPS": apps}
        env |= {k: "a-real-generated-secret" for k in required if k != missing}
        with (
            patch.dict(os.environ, env, clear=False),
            pytest.raises(ConfigError, match=_ALL_GUARDED[missing].replace(".", r"\.")),
        ):
            load_config(config_file)

    env = {"JENTIC_ENV": "production", "JENTIC__APPS": apps}
    env |= dict.fromkeys(required, "a-real-generated-secret")
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    values = {
        "JENTIC__ADMIN__AUTH__JWT_SECRET": config.admin.auth.jwt_secret,
        "JENTIC__ADMIN__INVITE__PEPPER": config.admin.invite.pepper,
        "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET": config.credentials.connect.state_secret,
    }
    for key, value in values.items():
        secret = value.get_secret_value()
        if key in required:
            assert secret == "a-real-generated-secret"
        else:
            assert secret.strip()
            assert secret != "a-real-generated-secret"


@pytest.mark.parametrize("raw", [[], "registry", ["registry", 1], {"registry": True}])
def test_production_guard_stays_strict_for_unreadable_apps(raw: object):
    """An apps value the guard cannot read as surface names keeps it strict."""
    assert _apps_for_secret_guard(raw) is None


def test_production_guard_default_apps_require_every_secret(config_file: Path):
    """No ``apps`` override means the combined default, which reads all three."""
    env = {"JENTIC_ENV": "production"}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(ConfigError, match=r"admin\.auth\.jwt_secret"),
    ):
        os.environ.pop("JENTIC__APPS", None)
        load_config(config_file)


def test_production_guard_stays_strict_outside_load_config(config_file: Path):
    """Direct model construction has no surface context and so stays strict.

    A relaxed load (standalone broker) must not leak its surface set into
    later validation in the same process.
    """
    env = {"JENTIC_ENV": "production", "JENTIC__APPS": "broker"}
    with patch.dict(os.environ, env, clear=False):
        load_config(config_file)
        with pytest.raises(ConfigError, match=r"admin\.invite\.pepper"):
            AdminInviteConfig()


_LOCAL_DEV_KEY_SEC1 = """\
-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIBG7o+PPPIdPqMK4RwNWnj+UaW8fZFzxw7oZD5XFqW5CoAoGCCqGSM49
AwEHoUQDQgAElriD/rpklmqTXbUOa9uLHAB2l+qr+DoeDmmykYLGblbxs+a1qvxB
369JIs2Ej4zMfkjBTGES38wMDs1J+PJG6g==
-----END EC PRIVATE KEY-----"""

_LOCAL_DEV_KEY_PKCS8 = """\
-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgEbuj4888h0+owrhH
A1aeP5Rpbx9kXPHDuhkPlcWpbkKhRANCAASWuIP+umSWapNdtQ5r24scAHaX6qv4
Oh4OabKRgsZuVvGz5rWq/EHfr0kizYSPjMx+SMFMYRLfzAwOzUn48kbq
-----END PRIVATE KEY-----"""


def test_local_dev_signing_key_rejected_in_production_sec1():
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match="local-dev signing key material"),
    ):
        AuthConfig(
            id_signing=[
                SigningKeyConfig(kid="custom-kid", private_key_pem=SecretStr(_LOCAL_DEV_KEY_SEC1))
            ]
        )


def test_local_dev_signing_key_rejected_in_production_pkcs8():
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match="local-dev signing key material"),
    ):
        AuthConfig(
            id_signing=[
                SigningKeyConfig(kid="custom-kid", private_key_pem=SecretStr(_LOCAL_DEV_KEY_PKCS8))
            ]
        )


def test_local_dev_signing_kid_rejected_in_production():
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ConfigError, match="local-dev key id"),
    ):
        AuthConfig(
            id_signing=[
                SigningKeyConfig(
                    kid="local-dev-key", private_key_pem=SecretStr(_LOCAL_DEV_KEY_SEC1)
                )
            ]
        )


def test_boolean_like_password_preserved_as_string(config_file: Path):
    env = {"JENTIC__DATABASES__ADMIN__PASSWORD": "true"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.databases.admin.password.get_secret_value() == "true"


def test_file_plus_env(config_file: Path):
    env = {
        "JENTIC__RUNTIME__LOG_LEVEL": "WARNING",
        "JENTIC__SERVICES__RETRY_MAX": "10",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.runtime.log_level == "WARNING"
    assert config.services.retry_max == 10
    assert config.databases.registry.host == "db.local"


def test_password_not_in_repr(config_file: Path):
    config = load_config(config_file)
    repr_str = repr(config)
    assert "reg_secret" not in repr_str
    assert "admin_secret" not in repr_str
    assert "ctrl_secret" not in repr_str


def test_password_not_in_str(config_file: Path):
    config = load_config(config_file)
    str_val = str(config)
    assert "reg_secret" not in str_val


def test_deep_merge_nested_override():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    override = {"a": {"b": 99}, "e": 4}
    result = _deep_merge(base, override)
    assert result == {"a": {"b": 99, "c": 2}, "d": 3, "e": 4}


def test_deep_merge_non_dict_replaces_dict():
    base = {"a": {"nested": 1}}
    override = {"a": "flat"}
    result = _deep_merge(base, override)
    assert result == {"a": "flat"}


def test_runtime_config_reload_applies_overrides():
    cfg = RuntimeConfig(debug=False, log_level="INFO")
    reloaded = cfg.reload({"debug": True, "log_level": "DEBUG"})
    assert reloaded.debug is True
    assert reloaded.log_level == "DEBUG"
    assert cfg.debug is False


def test_runtime_config_reload_preserves_unmodified():
    cfg = RuntimeConfig(debug=True, log_level="WARNING", maintenance_mode=True)
    reloaded = cfg.reload({"log_level": "ERROR"})
    assert reloaded.debug is True
    assert reloaded.maintenance_mode is True
    assert reloaded.log_level == "ERROR"


def test_uses_jentic_config_file_env(config_file: Path):
    with patch.dict(os.environ, {"JENTIC_CONFIG_FILE": str(config_file)}, clear=False):
        config = load_config()
    assert config.databases.registry.host == "db.local"


def test_uses_default_path_when_it_exists(
    tmp_path: Path, sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    yaml_path = tmp_path / "jentic-one.yaml"
    yaml_path.write_text(yaml.dump(sample_config_dict))
    monkeypatch.delenv("JENTIC_CONFIG_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.databases.registry.host == "db.local"


def test_empty_yaml_file_uses_env_overrides(tmp_path: Path):
    empty_path = tmp_path / "empty.yaml"
    empty_path.write_text("")
    env = {
        "JENTIC__DATABASES__REGISTRY__NAME": "r",
        "JENTIC__DATABASES__ADMIN__NAME": "a",
        "JENTIC__DATABASES__CONTROL__NAME": "c",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(empty_path)
    assert config.databases.registry.name == "r"


def test_non_dict_yaml_ignored(tmp_path: Path):
    path = tmp_path / "scalar.yaml"
    path.write_text("just a string\n")
    env = {
        "JENTIC__DATABASES__REGISTRY__NAME": "r",
        "JENTIC__DATABASES__ADMIN__NAME": "a",
        "JENTIC__DATABASES__CONTROL__NAME": "c",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(path)
    assert config.databases.registry.name == "r"


def test_apps_env_single_value(config_file: Path):
    env = {"JENTIC__APPS": "registry"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.apps == ["registry"]


def test_apps_env_comma_separated(config_file: Path):
    env = {"JENTIC__APPS": "registry,admin"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.apps == ["registry", "admin"]


def test_apps_env_comma_separated_with_spaces(config_file: Path):
    env = {"JENTIC__APPS": " registry , admin , control "}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.apps == ["registry", "admin", "control"]


def test_mcp_oauth_config_defaults(config_file: Path):
    """MCP OAuth seam, D9 as amended: off by default, and approval-first —
    auto-approve is an explicit opt-in, false by default."""
    config = load_config(config_file)
    assert config.server.mcp.oauth.enabled is False
    assert config.server.mcp.oauth.auto_approve_clients is False
    assert config.server.mcp.oauth.registration_gc_days == 90


def test_mcp_oauth_env_overrides(config_file: Path):
    env = {
        "JENTIC__SERVER__MCP__OAUTH__ENABLED": "true",
        "JENTIC__SERVER__MCP__OAUTH__AUTO_APPROVE_CLIENTS": "true",
        "JENTIC__SERVER__MCP__OAUTH__REGISTRATION_GC_DAYS": "30",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.server.mcp.oauth.enabled is True
    assert config.server.mcp.oauth.auto_approve_clients is True
    assert config.server.mcp.oauth.registration_gc_days == 30


def test_oauth_registration_rate_limit_knobs(config_file: Path):
    env = {
        "JENTIC__AUTH__OAUTH_RATE_LIMIT__REGISTRATION_RPM": "3",
        "JENTIC__AUTH__OAUTH_RATE_LIMIT__REGISTRATION_BURST": "2",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.auth.oauth_rate_limit.registration_rpm == 3
    assert config.auth.oauth_rate_limit.registration_burst == 2


def test_oauth_approval_status_rate_limit_defaults(config_file: Path):
    """The approval-status poll bucket: generous defaults (one tab polls at
    12 rpm; 120/60 holds ~10 NAT'd tabs), independently tunable from
    /authorize."""
    config = load_config(config_file)
    assert config.auth.oauth_rate_limit.approval_status_rpm == 120
    assert config.auth.oauth_rate_limit.approval_status_burst == 60


def test_oauth_approval_status_rate_limit_env_overrides(config_file: Path):
    env = {
        "JENTIC__AUTH__OAUTH_RATE_LIMIT__APPROVAL_STATUS_RPM": "6",
        "JENTIC__AUTH__OAUTH_RATE_LIMIT__APPROVAL_STATUS_BURST": "3",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.auth.oauth_rate_limit.approval_status_rpm == 6
    assert config.auth.oauth_rate_limit.approval_status_burst == 3


def test_oauth_trusted_proxies_default_empty(config_file: Path):
    config = load_config(config_file)
    assert config.auth.oauth_rate_limit.trusted_proxies == []
    assert config.auth.oauth_rate_limit.trusted_networks == ()


def test_oauth_trusted_proxies_env_comma_separated(config_file: Path):
    """The env var takes a comma-separated list of IPs and CIDRs (#1880)."""
    env = {"JENTIC__AUTH__OAUTH_RATE_LIMIT__TRUSTED_PROXIES": "10.0.0.0/8, 172.17.0.1,fd00::/8"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    rl = config.auth.oauth_rate_limit
    assert rl.trusted_proxies == ["10.0.0.0/8", "172.17.0.1", "fd00::/8"]
    assert [str(n) for n in rl.trusted_networks] == ["10.0.0.0/8", "172.17.0.1/32", "fd00::/8"]


def test_oauth_trusted_proxies_yaml_list(tmp_path: Path):
    config = _load(
        tmp_path,
        {"auth": {"oauth_rate_limit": {"trusted_proxies": [" 10.0.0.0/8 ", "2001:db8::1"]}}},
    )
    rl = config.auth.oauth_rate_limit
    assert rl.trusted_proxies == ["10.0.0.0/8", "2001:db8::1"]
    assert [str(n) for n in rl.trusted_networks] == ["10.0.0.0/8", "2001:db8::1/128"]


@pytest.mark.parametrize(
    "entry", ["10.0.0.0/33", "10.0.0.256", "proxy.internal", "", "fe80::1%eth0"]
)
def test_oauth_trusted_proxies_invalid_entry_fails_load(tmp_path: Path, entry: str):
    with pytest.raises(ConfigError, match=r"trusted_proxies entry"):
        _load(tmp_path, {"auth": {"oauth_rate_limit": {"trusted_proxies": [entry]}}})


@pytest.mark.parametrize(
    ("entry", "warned"),
    [
        ("10.0.0.0/8", False),
        ("172.17.0.1", False),
        ("fd00::/8", False),
        ("::ffff:192.168.0.0/112", False),
        ("0.0.0.0/0", True),
        ("::/0", True),
        ("203.0.113.7", True),
        ("8.0.0.0/7", True),
    ],
)
def test_oauth_trusted_proxies_warns_on_public_network(
    entry: str, warned: bool, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config_module, "_warned_public_trusted_proxies", set())
    with structlog.testing.capture_logs() as logs:
        OAuthRateLimitConfig(trusted_proxies=[entry])
        OAuthRateLimitConfig(trusted_proxies=[entry])
    events = [e for e in logs if e["event"] == "trusted_proxies_network_includes_public_addresses"]
    # Config is validated more than once per process: warn once per network.
    assert len(events) == (1 if warned else 0)
    if warned:
        assert events[0]["entry"] == entry


def test_oauth_trusted_networks_follow_later_changes():
    """The parsed cache is keyed on the entries, so it never goes stale."""
    cfg = OAuthRateLimitConfig(trusted_proxies=["10.0.0.0/8"])
    assert [str(n) for n in cfg.trusted_networks] == ["10.0.0.0/8"]
    copied = cfg.model_copy(update={"trusted_proxies": ["192.168.0.0/16"]})
    assert [str(n) for n in copied.trusted_networks] == ["192.168.0.0/16"]
    cfg.trusted_proxies = ["172.16.0.0/12"]
    assert [str(n) for n in cfg.trusted_networks] == ["172.16.0.0/12"]
    assert cfg.trusted_networks is cfg.trusted_networks


def test_encryption_config_defaults():
    cfg = EncryptionConfig()
    assert cfg.active_id == "v1"
    assert cfg.entries == []


def test_credentials_config_defaults():
    creds = CredentialsConfig()
    assert creds.encryption.active_id == "v1"
    assert creds.encryption.entries == []


def test_app_config_credentials_defaults(config_file: Path):
    config = load_config(config_file)
    assert config.credentials.encryption.active_id == "v1"
    assert config.credentials.encryption.entries == []


def test_encryption_config_yaml_override(tmp_path: Path, sample_config_dict: dict[str, Any]):
    sample_config_dict["credentials"] = {
        "encryption": {
            "active_id": "prod-v2",
            "entries": [{"id": "prod-v2", "material": "dGVzdC1rZXktbWF0ZXJpYWwtMzItYnl0ZXMtcGFk"}],
        }
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    config = load_config(path)
    assert config.credentials.encryption.active_id == "prod-v2"
    assert len(config.credentials.encryption.entries) == 1
    assert config.credentials.encryption.entries[0].id == "prod-v2"


def test_encryption_active_id_env_override(config_file: Path):
    env = {"JENTIC__CREDENTIALS__ENCRYPTION__ACTIVE_ID": "env-id"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.credentials.encryption.active_id == "env-id"


# A syntactically valid key for source-resolution tests: material_file content
# is vetted as base64 of exactly 32 bytes at config load.
_KEY_B64 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def test_encryption_key_inline_material():
    key = EncryptionKey(id="v1", material=SecretStr(_KEY_B64))
    assert key.resolved_material.get_secret_value() == _KEY_B64


def test_encryption_key_material_env():
    with patch.dict(os.environ, {"TEST_ENC_KEY": _KEY_B64}, clear=False):
        key = EncryptionKey(id="v1", material_env="TEST_ENC_KEY")
    assert key.resolved_material.get_secret_value() == _KEY_B64
    # Resolved once at validation — later env mutation is irrelevant.
    assert key.material is not None


def test_encryption_key_material_env_strips_whitespace():
    with patch.dict(os.environ, {"TEST_ENC_KEY": f"  {_KEY_B64}\n"}, clear=False):
        key = EncryptionKey(id="v1", material_env="TEST_ENC_KEY")
    assert key.resolved_material.get_secret_value() == _KEY_B64


def test_encryption_key_material_env_unset_fails():
    env = dict(os.environ)
    env.pop("TEST_ENC_KEY_MISSING", None)
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValidationError, match="TEST_ENC_KEY_MISSING"),
    ):
        EncryptionKey(id="v1", material_env="TEST_ENC_KEY_MISSING")


def test_encryption_key_material_file(tmp_path: Path):
    key_file = tmp_path / "enc.key"
    key_file.write_text(_KEY_B64 + "\n")
    key = EncryptionKey(id="v1", material_file=str(key_file))
    assert key.resolved_material.get_secret_value() == _KEY_B64


def test_encryption_key_material_file_missing_fails(tmp_path: Path):
    with pytest.raises(ValidationError, match="cannot read material_file"):
        EncryptionKey(id="v1", material_file=str(tmp_path / "nope.key"))


def test_encryption_key_material_file_empty_fails(tmp_path: Path):
    key_file = tmp_path / "empty.key"
    key_file.write_text("  \n")
    with pytest.raises(ValidationError, match="is empty"):
        EncryptionKey(id="v1", material_file=str(key_file))


def test_encryption_key_requires_exactly_one_source():
    with pytest.raises(ValidationError, match="exactly one"):
        EncryptionKey(id="v1")
    with pytest.raises(ValidationError, match="exactly one"):
        EncryptionKey(id="v1", material=SecretStr(_KEY_B64), material_env="TEST_ENC_KEY")


@pytest.mark.parametrize("inline", ["", "   ", " \n\t"])
def test_encryption_key_inline_material_empty_fails(inline: str):
    """An empty/whitespace inline material fails at validation, not at first
    credential use — all three sources reject empties identically."""
    with pytest.raises(ValidationError, match="material is empty"):
        EncryptionKey(id="v1", material=SecretStr(inline))


def test_encryption_key_inline_material_strips_whitespace():
    """Inline material strips like the env/file sources do, so identical bytes
    produce identical keys regardless of which source carried them."""
    key = EncryptionKey(id="v1", material=SecretStr(f"  {_KEY_B64}\n"))
    assert key.resolved_material.get_secret_value() == _KEY_B64


@pytest.mark.parametrize("value", ["", "   ", " \n"])
def test_encryption_key_material_env_empty_fails(value: str):
    """A set-but-empty (or whitespace-only) variable must fail like an unset
    one — it must never resolve to a zero-length key."""
    with (
        patch.dict(os.environ, {"TEST_ENC_KEY": value}, clear=False),
        pytest.raises(ValidationError, match=r"is not set \(or empty\)"),
    ):
        EncryptionKey(id="v1", material_env="TEST_ENC_KEY")


def test_encryption_key_material_file_rejects_fifo(tmp_path: Path):
    """A pipe cannot be re-read, so it must be rejected up front — a writer-less
    FIFO would otherwise block boot forever with no timeout."""
    fifo = tmp_path / "key.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValidationError, match="must be a regular file"):
        EncryptionKey(id="v1", material_file=str(fifo))


def test_encryption_key_material_file_not_base64_fails(tmp_path: Path):
    key_file = tmp_path / "enc.key"
    key_file.write_text("this is not base64!\n")
    with pytest.raises(ValidationError, match="does not contain a base64-encoded key"):
        EncryptionKey(id="v1", material_file=str(key_file))


def test_encryption_key_material_file_wrong_length_fails_without_length_echo(tmp_path: Path):
    """The failure must not echo the observed byte length: material_file is
    reachable with env-write privilege, and echoing the length turns any
    readable path into a content-length oracle."""
    key_file = tmp_path / "enc.key"
    key_file.write_text(base64.b64encode(b"short-key").decode())
    with pytest.raises(ValidationError, match="does not contain a 32-byte key") as excinfo:
        EncryptionKey(id="v1", material_file=str(key_file))
    assert "got" not in str(excinfo.value)
    assert "9 bytes" not in str(excinfo.value)


def test_encryption_key_material_file_too_large_fails(tmp_path: Path):
    key_file = tmp_path / "enc.key"
    key_file.write_bytes(b"A" * 8192)
    with pytest.raises(ValidationError, match="does not contain a base64-encoded key"):
        EncryptionKey(id="v1", material_file=str(key_file))


def test_encryption_key_material_file_permissive_mode_warns(tmp_path: Path):
    """A group/other-readable key file is the feature's whole security boundary
    silently gone; the load must say so."""
    key_file = tmp_path / "enc.key"
    key_file.write_text(_KEY_B64)
    key_file.chmod(0o644)
    with structlog.testing.capture_logs() as logs:
        EncryptionKey(id="v1", material_file=str(key_file))
    assert any(log["event"] == "encryption_material_file_permissive" for log in logs)


def test_encryption_key_material_file_private_mode_does_not_warn(tmp_path: Path):
    key_file = tmp_path / "enc.key"
    key_file.write_text(_KEY_B64)
    key_file.chmod(0o600)
    with structlog.testing.capture_logs() as logs:
        EncryptionKey(id="v1", material_file=str(key_file))
    assert not any(log["event"] == "encryption_material_file_permissive" for log in logs)


def test_encryption_key_resolution_logs_source_and_fingerprint_not_material(tmp_path: Path):
    """The boot trail must record where the key came from (id, source, origin,
    fingerprint) and must never carry the material itself."""
    key_file = tmp_path / "enc.key"
    key_file.write_text(_KEY_B64)
    with structlog.testing.capture_logs() as logs:
        EncryptionKey(id="v1", material_file=str(key_file))
    resolved = [log for log in logs if log["event"] == "encryption_key_material_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["key_id"] == "v1"
    assert resolved[0]["source"] == "material_file"
    assert resolved[0]["origin"] == str(key_file)
    assert len(resolved[0]["fingerprint"]) == 16
    assert all(_KEY_B64 not in str(v) for v in resolved[0].values())


def test_encryption_key_resolved_key_revalidates(tmp_path: Path):
    """A resolved key survives dump -> validate: the source field is cleared,
    so re-validation neither trips the exactly-one check nor re-reads the file."""
    key_file = tmp_path / "enc.key"
    key_file.write_text(_KEY_B64)
    key = EncryptionKey(id="v1", material_file=str(key_file))
    key_file.unlink()  # re-validation must not go back to the source
    again = EncryptionKey.model_validate(key.model_dump())
    assert again.resolved_material.get_secret_value() == _KEY_B64
    assert again.material_file is None


def test_encryption_key_material_file_yaml_roundtrip(
    tmp_path: Path, sample_config_dict: dict[str, Any]
):
    key_file = tmp_path / "enc.key"
    key_file.write_text(_KEY_B64)
    sample_config_dict["credentials"] = {
        "encryption": {
            "active_id": "v1",
            "entries": [{"id": "v1", "material_file": str(key_file)}],
        }
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    config = load_config(path)
    entry = config.credentials.encryption.entries[0]
    assert entry.resolved_material.get_secret_value() == _KEY_B64


def test_load_config_from_pipe_is_cached(sample_config_dict: dict[str, Any]):
    """A config handed on a one-shot fd (pipe / /dev/fd) survives repeat loads.

    Supervisors that keep secrets off disk pass JENTIC_CONFIG_FILE=/dev/fd/N;
    consumers like the Alembic env call load_config once per database, so the
    first read must be cached rather than hitting EOF on the second load.
    """
    doc = yaml.dump(sample_config_dict).encode()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, doc)
        os.close(write_fd)
        pipe_path = Path(f"/dev/fd/{read_fd}")
        first = load_config(pipe_path)
        with structlog.testing.capture_logs() as logs:
            second = load_config(pipe_path)  # would be EOF without the cache
        assert first.databases.registry.name == second.databases.registry.name
        # The reuse leaves a trail: "why didn't my config change take effect"
        # must be answerable from the logs.
        assert any(log["event"] == "oneshot_config_cache_reused" for log in logs)
    finally:
        os.close(read_fd)
        _ONESHOT_CONFIG_CACHE.clear()


def test_load_config_from_regular_file_is_not_cached(
    tmp_path: Path, sample_config_dict: dict[str, Any]
):
    """Regular files re-read on every load — caching them would defeat keyset
    rotation (edit the file, reload, still get the stale document)."""
    path = tmp_path / "cfg.yaml"
    sample_config_dict["databases"]["registry"]["name"] = "before-rotation"
    path.write_text(yaml.dump(sample_config_dict))
    first = load_config(path)
    sample_config_dict["databases"]["registry"]["name"] = "after-rotation"
    path.write_text(yaml.dump(sample_config_dict))
    second = load_config(path)
    assert first.databases.registry.name == "before-rotation"
    assert second.databases.registry.name == "after-rotation"
    assert _ONESHOT_CONFIG_CACHE == {}


def test_load_config_oneshot_empty_source_fails_loudly_and_is_not_cached():
    """A drained (or never-written) one-shot source must raise a ConfigError
    naming the real cause — and must NOT be cached, or the process would be
    permanently pinned to an empty config with no recovery."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)  # supervisor closed its end without writing
    try:
        with pytest.raises(ConfigError, match="yielded no content"):
            load_config(Path(f"/dev/fd/{read_fd}"))
        assert _ONESHOT_CONFIG_CACHE == {}
    finally:
        os.close(read_fd)
        _ONESHOT_CONFIG_CACHE.clear()


def test_oneshot_cache_is_keyed_on_source_identity_not_path(
    sample_config_dict: dict[str, Any],
):
    """fd numbers are recycled, so two different pipes can both be /dev/fd/N in
    one process. The cache must key on the source's identity, never the path
    string — a path-keyed cache silently serves the first pipe's document for
    the second."""
    sample_config_dict["databases"]["registry"]["name"] = "config-a"
    doc_a = yaml.dump(sample_config_dict).encode()
    sample_config_dict["databases"]["registry"]["name"] = "config-b"
    doc_b = yaml.dump(sample_config_dict).encode()

    read_a, write_a = os.pipe()
    read_b = -1
    try:
        os.write(write_a, doc_a)
        os.close(write_a)
        first = load_config(Path(f"/dev/fd/{read_a}"))
        os.close(read_a)

        read_b, write_b = os.pipe()
        os.write(write_b, doc_b)
        os.close(write_b)
        # Force the second pipe onto the first pipe's recycled fd number.
        if read_b != read_a:
            os.dup2(read_b, read_a)
            os.close(read_b)
        read_b = read_a
        second = load_config(Path(f"/dev/fd/{read_b}"))

        assert first.databases.registry.name == "config-a"
        assert second.databases.registry.name == "config-b"
    finally:
        if read_b >= 0:
            os.close(read_b)
        _ONESHOT_CONFIG_CACHE.clear()


def test_oneshot_cache_entry_with_recycled_inode_is_not_served(
    sample_config_dict: dict[str, Any],
):
    """Pins #1499: (device, inode) is not a stable identity — macOS reports
    pipes with st_dev 0 and hands a freed pipe's inode to the next pipe. An
    entry cached for an earlier source on the same device, inode, and path
    (but a different ctime) must never be served for the new source."""
    sample_config_dict["databases"]["registry"]["name"] = "fresh"
    doc = yaml.dump(sample_config_dict)
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, doc.encode())
        os.close(write_fd)
        pipe_path = Path(f"/dev/fd/{read_fd}")
        dev, ino, ctime_ns, _ = _oneshot_cache_key(os.fstat(read_fd), pipe_path)
        _ONESHOT_CONFIG_CACHE[(dev, ino, ctime_ns - 1, str(pipe_path))] = "stale: true"
        loaded = load_config(pipe_path)
        assert loaded.databases.registry.name == "fresh"
    finally:
        os.close(read_fd)
        _ONESHOT_CONFIG_CACHE.clear()


def test_oneshot_cache_survives_writes_after_open(
    sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Every pipe write bumps its ctime, so a supervisor still streaming the
    document after the loaders opened the fd must not leave any of them keyed
    on a pre-drain identity: a concurrent loader waiting on the drain, and a
    repeat load after it, must both take the cached document instead of
    hitting EOF on the drained pipe.

    The test couples to _read_config_text at three seams, and each failure
    message names the seam it depends on:

    - open: every open of the pipe path goes through io.open or builtins.open
      (Path.open included); the opened file is where a loader parks.
    - lock: _ONESHOT_CACHE_LOCK is looked up at call time, so a loader that
      would block on it parks too.
    - read gate: the writer holds its first write until a loader starts
      draining the opened file through read(), readline(), readlines(), or
      iteration. The drainer's in-lock fstat runs before that, which pins it
      to the pre-write identity. A drain that bypasses the file object (e.g.
      os.read on its fileno) never opens the gate, and the writer reports
      that it is still waiting for a loader to start reading."""
    # One deadline bounds every wait and join in the test, and all threads are
    # daemons, so a wedged loader fails the test without holding the process.
    timeout_s = 10.0
    # The writer's read gate expires this long before the shared deadline.
    gate_margin_s = 1.0
    deadline = time.monotonic() + timeout_s

    def remaining() -> float:
        return max(0.0, deadline - time.monotonic())

    # Each write lands on a later timestamp tick so it moves the ctime: a Linux
    # tick is one jiffy (at most 10 ms), and macOS pipe timestamps are finer.
    ctime_tick_s = 0.05
    doc = yaml.dump(sample_config_dict).encode()
    read_fd, write_fd = os.pipe()
    pipe_path = f"/dev/fd/{read_fd}"
    loaded: list[AppConfig] = []
    errors: list[BaseException] = []

    # The writer and both loaders meet at one barrier before any byte is
    # written. A loader parks right after it opens the pipe (it has stat'ed the
    # pre-write identity and holds the fd), or when it would block on the cache
    # lock, so an implementation that opens under the lock parks too. The hooks
    # see every open of the pipe through io.open or builtins.open (Path.open
    # included) and every acquire of _ONESHOT_CACHE_LOCK.
    released = threading.Event()
    rendezvous = threading.Barrier(3, action=released.set, timeout=timeout_s)
    parked = {name: threading.Event() for name in ("loader-0", "loader-1")}
    read_started = threading.Event()
    # Set on teardown so the writer stops waiting on the read gate.
    stop = threading.Event()

    def park() -> None:
        signal = parked.get(threading.current_thread().name)
        if signal is None or signal.is_set() or released.is_set():
            return
        signal.set()
        rendezvous.wait(remaining())

    class _ReadGateFile:
        """Wraps the opened pipe so any read-style call or iteration opens the gate."""

        def __init__(self, f: Any) -> None:
            self._f = f

        def __getattr__(self, name: str) -> Any:
            return getattr(self._f, name)

        def __enter__(self) -> _ReadGateFile:
            self._f.__enter__()
            return self

        def __exit__(self, *exc_info: Any) -> Any:
            return self._f.__exit__(*exc_info)

        def read(self, *args: Any) -> Any:
            read_started.set()
            return self._f.read(*args)

        def readline(self, *args: Any) -> Any:
            read_started.set()
            return self._f.readline(*args)

        def readlines(self, *args: Any) -> Any:
            read_started.set()
            return self._f.readlines(*args)

        def __iter__(self) -> _ReadGateFile:
            return self

        def __next__(self) -> Any:
            read_started.set()
            return next(self._f)

    real_open = io.open

    def hooked_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        f: Any = real_open(file, *args, **kwargs)
        if not isinstance(file, str | os.PathLike) or os.fspath(file) != pipe_path:
            return f
        try:
            park()
        except BaseException:
            f.close()
            raise
        return _ReadGateFile(f)

    class _ParkingLock:
        """Stands in for _ONESHOT_CACHE_LOCK; a loader that would block parks first."""

        def __init__(self) -> None:
            self._lock = threading.Lock()

        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            if self._lock.acquire(blocking=False):
                return True
            if not blocking:
                return False
            park()
            return self._lock.acquire(timeout=timeout)

        def release(self) -> None:
            self._lock.release()

        def __enter__(self) -> bool:
            return self.acquire()

        def __exit__(self, *exc_info: object) -> None:
            self.release()

    monkeypatch.setattr(config_module, "_ONESHOT_CACHE_LOCK", _ParkingLock())
    monkeypatch.setattr(io, "open", hooked_open)
    monkeypatch.setattr(builtins, "open", hooked_open)

    def stream() -> None:
        try:
            rendezvous.wait(remaining())
            # The drainer takes its in-lock fstat before it reads, so holding
            # the first write until a read starts pins that fstat to the
            # pre-write identity: a key taken there must never be what gets
            # stored. The gate gives up a margin before the shared deadline so
            # its error is recorded before the main thread's joins expire.
            while not read_started.wait(0.05):
                if stop.is_set():
                    return
                if remaining() <= gate_margin_s:
                    errors.append(
                        AssertionError(
                            f"writer still waiting for a loader to start reading {pipe_path}: "
                            "the read gate needs _read_config_text to drain the opened file "
                            "through read(), readline(), readlines(), or iteration"
                        )
                    )
                    return
            for chunk in (doc[: len(doc) // 2], doc[len(doc) // 2 :]):
                time.sleep(ctime_tick_s)
                os.write(write_fd, chunk)
        except threading.BrokenBarrierError:
            return
        finally:
            # Closing even on a failed handshake lets a blocked loader see EOF.
            os.close(write_fd)

    def load() -> None:
        try:
            loaded.append(load_config(Path(pipe_path)))
        except BaseException as exc:  # surfaced via the assertion below
            errors.append(exc)
            # A loader that dies before the rendezvous breaks it, so the writer
            # and the other loader stop waiting for a party that never arrives.
            rendezvous.abort()

    writer = threading.Thread(target=stream, name="writer", daemon=True)
    loaders = [threading.Thread(target=load, name=name, daemon=True) for name in parked]
    threads = [writer, *loaders]
    try:
        writer.start()
        # Start the second loader only once the first has parked: on macOS,
        # stat() of /dev/fd/N can fail with EBADF while another thread opens the
        # same /dev/fd/N (#1803), a platform race and not the one under test.
        for loader in loaders:
            loader.start()
            while not parked[loader.name].wait(0.05) and loader.is_alive() and remaining():
                pass
            assert parked[loader.name].is_set(), (
                f"{loader.name} never parked: the rendezvous needs _read_config_text to "
                f"open {pipe_path} through io.open or builtins.open (Path.open included), "
                f"or to block on _ONESHOT_CACHE_LOCK; errors={errors!r}"
            )
        # Loaders first: a hung loader is the thread the failure names.
        for loader in loaders:
            loader.join(remaining())
            if loader.is_alive():
                stage = (
                    "wedged in read (a read started; the loader is stuck in it or "
                    "behind the cache lock)"
                    if read_started.is_set()
                    else "and the writer is still waiting for a loader to start reading"
                )
                raise AssertionError(
                    f"{loader.name} still running after {timeout_s}s, {stage}; {errors=!r}"
                )
        writer.join(remaining())
        assert not writer.is_alive(), f"writer still running after {timeout_s}s; {errors=!r}"
        assert errors == []
        loaded.append(load_config(Path(pipe_path)))
        assert {c.databases.registry.name for c in loaded} == {
            sample_config_dict["databases"]["registry"]["name"]
        }
        assert len(loaded) == 3
    finally:
        rendezvous.abort()
        stop.set()
        for t in threads:
            if t.is_alive():
                t.join(remaining())
        # A thread still running may be reading read_fd; closing it under that
        # thread could hand the fd number to an unrelated file, so it leaks.
        if not any(t.is_alive() for t in threads):
            os.close(read_fd)
        _ONESHOT_CONFIG_CACHE.clear()


def test_broker_jobs_api_base_url_defaults_to_none(config_file: Path):
    config = load_config(config_file)
    assert config.broker.jobs_api_base_url is None


def test_broker_jobs_api_base_url_from_yaml(tmp_path: Path, sample_config_dict: dict[str, Any]):
    sample_config_dict["broker"] = {"jobs_api_base_url": "https://api.example.com"}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    config = load_config(path)
    assert config.broker.jobs_api_base_url == "https://api.example.com"


def test_broker_jobs_api_base_url_env_override(config_file: Path):
    env = {"JENTIC__BROKER__JOBS_API_BASE_URL": "https://env.example.com"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.broker.jobs_api_base_url == "https://env.example.com"


def test_broker_streaming_persist_max_concurrency_default(config_file: Path) -> None:
    assert load_config(config_file).broker.streaming_persist_max_concurrency == 5


def test_broker_streaming_persist_max_concurrency_from_yaml(
    tmp_path: Path, sample_config_dict: dict[str, Any]
) -> None:
    sample_config_dict["broker"] = {"streaming_persist_max_concurrency": 3}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    assert load_config(path).broker.streaming_persist_max_concurrency == 3


def test_broker_streaming_persist_max_concurrency_env_override(config_file: Path) -> None:
    env = {"JENTIC__BROKER__STREAMING_PERSIST_MAX_CONCURRENCY": "7"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.broker.streaming_persist_max_concurrency == 7


@pytest.mark.parametrize("value", ["0", "-1"])
def test_broker_streaming_persist_max_concurrency_must_be_positive(
    config_file: Path, value: str
) -> None:
    env = {"JENTIC__BROKER__STREAMING_PERSIST_MAX_CONCURRENCY": value}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(ConfigError, match="streaming_persist_max_concurrency"),
    ):
        load_config(config_file)


def test_broker_streaming_persist_acquire_timeout_default_and_overrides(
    tmp_path: Path, sample_config_dict: dict[str, Any], config_file: Path
) -> None:
    assert load_config(config_file).broker.streaming_persist_acquire_timeout_s == 5.0

    sample_config_dict["broker"] = {"streaming_persist_acquire_timeout_s": 2.5}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    assert load_config(path).broker.streaming_persist_acquire_timeout_s == 2.5

    env = {"JENTIC__BROKER__STREAMING_PERSIST_ACQUIRE_TIMEOUT_S": "0.75"}
    with patch.dict(os.environ, env, clear=False):
        assert load_config(config_file).broker.streaming_persist_acquire_timeout_s == 0.75


@pytest.mark.parametrize("value", ["0", "-1"])
def test_broker_streaming_persist_acquire_timeout_must_be_positive(
    config_file: Path, value: str
) -> None:
    env = {"JENTIC__BROKER__STREAMING_PERSIST_ACQUIRE_TIMEOUT_S": value}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(ConfigError, match="streaming_persist_acquire_timeout_s"),
    ):
        load_config(config_file)


@pytest.mark.parametrize("value", [False, "false"])
def test_broker_retired_direct_bindings_flag_false_is_rejected(
    tmp_path: Path, sample_config_dict: dict[str, Any], value: object
) -> None:
    """Pinning the deleted toolkit path must fail boot, not silently switch paths."""
    sample_config_dict["broker"] = {"direct_bindings_enabled": value}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    with pytest.raises(ConfigError, match="direct_bindings_enabled was removed"):
        load_config(path)


def test_broker_retired_direct_bindings_flag_false_env_is_rejected(config_file: Path) -> None:
    env = {"JENTIC__BROKER__DIRECT_BINDINGS_ENABLED": "false"}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(ConfigError, match="direct_bindings_enabled was removed"),
    ):
        load_config(config_file)


def test_broker_retired_direct_bindings_flag_true_is_ignored(
    tmp_path: Path, sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``true`` was the 0.40 default: harmless, dropped with a one-time warning."""
    sample_config_dict["broker"] = {"direct_bindings_enabled": True}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    monkeypatch.setattr(config_module, "_retired_direct_bindings_flag_warned", threading.Event())
    with structlog.testing.capture_logs() as logs:
        config = load_config(path)
        load_config(path)
    assert not hasattr(config.broker, "direct_bindings_enabled")
    warnings = [e for e in logs if e["event"] == "config_retired_setting_ignored"]
    assert len(warnings) == 1, "the deprecation warning is logged once per process"
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["setting"] == "broker.direct_bindings_enabled"


def test_broker_retired_account_linking_base_url_is_ignored(
    tmp_path: Path, sample_config_dict: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Denials link to the agent's open connect session; the old base URL is dropped."""
    sample_config_dict["broker"] = {
        "account_linking_base_url": "https://console.example.com",
        "jobs_api_base_url": "https://api.example.com",
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    monkeypatch.setattr(config_module, "_retired_account_linking_warned", threading.Event())
    with structlog.testing.capture_logs() as logs:
        config = load_config(path)
        load_config(path)
    assert config.broker.jobs_api_base_url == "https://api.example.com"
    assert not hasattr(config.broker, "account_linking_base_url")
    warnings = [e for e in logs if e["event"] == "config_retired_setting_ignored"]
    assert len(warnings) == 1, "the warning is logged once per process"
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["setting"] == "broker.account_linking_base_url"


def test_broker_retired_account_linking_base_url_env_is_ignored(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_retired_account_linking_warned", threading.Event())
    env = {"JENTIC__BROKER__ACCOUNT_LINKING_BASE_URL": "https://console.example.com"}
    with patch.dict(os.environ, env, clear=False), structlog.testing.capture_logs() as logs:
        load_config(config_file)
    assert [e["setting"] for e in logs if e["event"] == "config_retired_setting_ignored"] == [
        "broker.account_linking_base_url"
    ]


def test_connect_approval_url_is_token_less_and_absolute(tmp_path: Path) -> None:
    config = _load(tmp_path, {"server": {"public_base_url": "https://j1.example.com/"}})
    assert connect_approval_url(config, "cs_1") == "https://j1.example.com/app/agents?approve=cs_1"
    bare = _load(tmp_path, {"server": {"host": "127.0.0.1", "port": 8123}})
    assert connect_approval_url(bare, "cs_1") == "http://127.0.0.1:8123/app/agents?approve=cs_1"


@pytest.mark.parametrize("value", [24, "0"])
def test_services_retired_sa_sweep_age_is_ignored(
    tmp_path: Path,
    sample_config_dict: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    value: object,
) -> None:
    """The 0.40 sweep age gate is harmless: dropped with a one-time warning."""
    sample_config_dict["services"] = {
        "service_account_sweep_min_stamp_age_hours": value,
        "retry_max": 5,
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    monkeypatch.setattr(config_module, "_retired_sa_sweep_age_warned", threading.Event())
    with structlog.testing.capture_logs() as logs:
        config = load_config(path)
        load_config(path)
    assert config.services.retry_max == 5
    assert not hasattr(config.services, "service_account_sweep_min_stamp_age_hours")
    warnings = [e for e in logs if e["event"] == "config_retired_setting_ignored"]
    assert len(warnings) == 1, "the warning is logged once per process"
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["setting"] == "services.service_account_sweep_min_stamp_age_hours"


def test_services_retired_sa_sweep_age_env_is_ignored(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_retired_sa_sweep_age_warned", threading.Event())
    env = {"JENTIC__SERVICES__SERVICE_ACCOUNT_SWEEP_MIN_STAMP_AGE_HOURS": "48"}
    with patch.dict(os.environ, env, clear=False), structlog.testing.capture_logs() as logs:
        load_config(config_file)
    assert [e["setting"] for e in logs if e["event"] == "config_retired_setting_ignored"] == [
        "services.service_account_sweep_min_stamp_age_hours"
    ]


def test_server_backend_defaults_to_local(config_file: Path):
    config = load_config(config_file)
    assert config.server.backend == "local"


def test_server_backend_from_yaml(tmp_path: Path, sample_config_dict: dict[str, Any]):
    sample_config_dict["server"] = {"backend": "remote"}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    config = load_config(path)
    assert config.server.backend == "remote"


def test_server_backend_env_override(config_file: Path):
    env = {"JENTIC__SERVER__BACKEND": "remote"}
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.server.backend == "remote"


def test_server_backend_rejects_invalid_value(tmp_path: Path, sample_config_dict: dict[str, Any]):
    sample_config_dict["server"] = {"backend": "cloud"}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(sample_config_dict))
    with pytest.raises(ConfigError):
        load_config(path)


def test_local_yaml_has_valid_encryption_config():
    """config/local.yaml must ship a usable encryption keyset for local dev."""
    local_path = Path(__file__).resolve().parents[2] / "config" / "local.yaml"
    config = load_config(local_path)
    assert config.credentials.encryption.active_id == "v1"
    assert len(config.credentials.encryption.entries) >= 1
    matching = [e for e in config.credentials.encryption.entries if e.id == "v1"]
    assert len(matching) == 1


def test_local_sqlite_yaml_has_valid_encryption_config():
    """config/local-sqlite.yaml must ship a usable encryption keyset for local dev."""
    local_path = Path(__file__).resolve().parents[2] / "config" / "local-sqlite.yaml"
    config = load_config(local_path)
    assert config.credentials.encryption.active_id == "v1"
    assert len(config.credentials.encryption.entries) >= 1
    matching = [e for e in config.credentials.encryption.entries if e.id == "v1"]
    assert len(matching) == 1


def test_local_configs_register_direct_oauth2_provider():
    """Both local configs must register the direct_oauth2 provider for connect flows."""
    base = Path(__file__).resolve().parents[2] / "config"
    for name in ("local.yaml", "local-sqlite.yaml"):
        config = load_config(base / name)
        providers = config.credentials.providers
        assert "direct_oauth2" in providers, f"{name} missing direct_oauth2 provider"
        assert providers["direct_oauth2"].kind == "direct_oauth2"


def test_egress_single_cidr_string_coerced():
    cfg = EgressConfig(allowed_private_subnets="10.0.0.0/8")  # type: ignore[arg-type]
    assert cfg.allowed_private_subnets == ["10.0.0.0/8"]


def test_egress_comma_separated_cidrs_coerced():
    cfg = EgressConfig(allowed_private_subnets="10.0.0.0/8,172.16.0.0/12")  # type: ignore[arg-type]
    assert cfg.allowed_private_subnets == ["10.0.0.0/8", "172.16.0.0/12"]


def test_egress_comma_separated_with_spaces_stripped():
    cfg = EgressConfig(allowed_private_subnets=" 10.0.0.0/8 , 172.16.0.0/12 ")  # type: ignore[arg-type]
    assert cfg.allowed_private_subnets == ["10.0.0.0/8", "172.16.0.0/12"]


def test_egress_list_passes_through_unchanged():
    cfg = EgressConfig(allowed_private_subnets=["10.0.0.0/8"])
    assert cfg.allowed_private_subnets == ["10.0.0.0/8"]


def test_egress_cidr_validator_rejects_invalid_after_coercion():
    with pytest.raises(Exception, match="invalid CIDR"):
        EgressConfig(allowed_private_subnets="not-a-cidr")  # type: ignore[arg-type]


def test_egress_allowed_internal_domains_string_coerced():
    cfg = EgressConfig(allowed_internal_domains=".svc.cluster.local")  # type: ignore[arg-type]
    assert cfg.allowed_internal_domains == [".svc.cluster.local"]


def test_egress_allowed_internal_domains_comma_separated():
    cfg = EgressConfig(allowed_internal_domains=".svc.cluster.local,.internal")  # type: ignore[arg-type]
    assert cfg.allowed_internal_domains == [".svc.cluster.local", ".internal"]


def test_egress_empty_string_produces_empty_list():
    cfg = EgressConfig(allowed_private_subnets="")  # type: ignore[arg-type]
    assert cfg.allowed_private_subnets == []


def _config_with(**sections: Any) -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "reg"},
                "admin": {"name": "admin"},
                "control": {"name": "ctrl"},
            },
            **sections,
        }
    )


def test_control_egress_defaults_to_strict_broker_policy():
    config = _config_with()
    assert config.control.egress == ControlEgressConfig()
    assert config.control.egress.allowed_private_subnets is None
    assert config.control.egress.allowed_internal_domains is None
    assert resolved_control_egress(config) == EgressConfig()


def test_control_egress_inherits_broker_egress_when_unset():
    config = _config_with(
        broker={
            "egress": {
                "allowed_private_subnets": ["10.50.0.0/16"],
                "allowed_internal_domains": [".corp.internal"],
                "dns_pinning_enabled": False,
            }
        }
    )
    assert resolved_control_egress(config) == config.broker.egress


def test_control_egress_override_differs_from_broker():
    config = _config_with(
        broker={"egress": {"allowed_private_subnets": ["10.50.0.0/16"]}},
        control={"egress": {"allowed_private_subnets": ["10.60.0.0/16"]}},
    )
    assert resolved_control_egress(config).allowed_private_subnets == ["10.60.0.0/16"]
    # The broker's own policy is untouched.
    assert config.broker.egress.allowed_private_subnets == ["10.50.0.0/16"]


def test_control_egress_unset_fields_fall_back_per_field():
    config = _config_with(
        broker={
            "egress": {
                "allowed_private_subnets": ["10.50.0.0/16"],
                "allowed_internal_domains": [".corp.internal"],
            }
        },
        control={"egress": {"allowed_internal_domains": [".svc.cluster.local"]}},
    )
    resolved = resolved_control_egress(config)
    assert resolved.allowed_private_subnets == ["10.50.0.0/16"]
    assert resolved.allowed_internal_domains == [".svc.cluster.local"]


def test_control_egress_explicit_empty_list_is_stricter_than_broker():
    config = _config_with(
        broker={"egress": {"allowed_private_subnets": ["10.50.0.0/16"]}},
        control={"egress": {"allowed_private_subnets": []}},
    )
    assert resolved_control_egress(config).allowed_private_subnets == []


def test_control_egress_from_env_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.dump(
            {
                "databases": {
                    "registry": {"name": "reg"},
                    "admin": {"name": "admin"},
                    "control": {"name": "ctrl"},
                },
                "broker": {"egress": {"allowed_internal_domains": [".corp.internal"]}},
            }
        )
    )
    monkeypatch.setenv(
        "JENTIC__CONTROL__EGRESS__ALLOWED_PRIVATE_SUBNETS", "10.50.0.0/16,10.51.0.0/16"
    )
    config = load_config(path)
    assert config.control.egress.allowed_internal_domains is None
    resolved = resolved_control_egress(config)
    assert resolved.allowed_private_subnets == ["10.50.0.0/16", "10.51.0.0/16"]
    # Not set for control, so inherited from broker.egress.
    assert resolved.allowed_internal_domains == [".corp.internal"]
    assert config.broker.egress.allowed_private_subnets == []


def test_control_egress_empty_env_string_is_an_explicit_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.dump(
            {
                "databases": {
                    "registry": {"name": "reg"},
                    "admin": {"name": "admin"},
                    "control": {"name": "ctrl"},
                },
                "broker": {"egress": {"allowed_private_subnets": ["10.50.0.0/16"]}},
            }
        )
    )
    monkeypatch.setenv("JENTIC__CONTROL__EGRESS__ALLOWED_PRIVATE_SUBNETS", "")
    config = load_config(path)
    assert resolved_control_egress(config).allowed_private_subnets == []


@pytest.mark.parametrize(
    "key", ["dns_pinning_enabled", "allowed_private_subnet", "unknown"], ids=str
)
def test_control_egress_rejects_keys_outside_the_allowlist(key: str):
    # Control-plane calls always pin DNS, so a pinning toggle here would be a
    # silent no-op; it and any other unknown key fail validation instead.
    with pytest.raises(ValidationError, match=key):
        _config_with(control={"egress": {key: False}})


def test_control_egress_dns_pinning_env_var_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.dump(
            {
                "databases": {
                    "registry": {"name": "reg"},
                    "admin": {"name": "admin"},
                    "control": {"name": "ctrl"},
                }
            }
        )
    )
    monkeypatch.setenv("JENTIC__CONTROL__EGRESS__DNS_PINNING_ENABLED", "false")
    with pytest.raises(ConfigError, match="dns_pinning_enabled"):
        load_config(path)


def test_control_egress_round_trips_through_dump_and_validate():
    # "Unset" is an explicit None in the model, so a dump/re-validate round trip
    # keeps inheritance intact.
    config = _config_with(
        broker={"egress": {"allowed_private_subnets": ["10.50.0.0/16"]}},
        control={"egress": {"allowed_internal_domains": [".corp.internal"]}},
    )
    again = AppConfig.model_validate(config.model_dump(mode="python"))
    assert resolved_control_egress(again) == resolved_control_egress(config)
    assert resolved_control_egress(again).allowed_private_subnets == ["10.50.0.0/16"]


def test_control_egress_invalid_cidr_rejected():
    with pytest.raises(ValidationError, match="invalid CIDR"):
        _config_with(control={"egress": {"allowed_private_subnets": ["not-a-cidr"]}})


def test_csv_to_list_rejects_non_string_non_list():
    with pytest.raises(TypeError, match="expected list or comma-separated string"):
        _csv_to_list(123)


# --- server.public_base_url + derived-URL consolidation (issue #818) ---------


def _min_dbs() -> dict[str, Any]:
    return {
        "registry": {"name": "reg"},
        "admin": {"name": "admin"},
        "control": {"name": "ctrl"},
    }


def _load(tmp_path: Path, extra: dict[str, Any]) -> AppConfig:
    data: dict[str, Any] = {"databases": _min_dbs(), **extra}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_config(path)


def test_public_base_url_default_empty(tmp_path: Path):
    config = _load(tmp_path, {})
    assert config.server.public_base_url == ""


def test_public_base_url_from_yaml(tmp_path: Path):
    config = _load(tmp_path, {"server": {"public_base_url": "https://jentic.example.com/"}})
    # normalized: trailing slash stripped
    assert config.server.public_base_url == "https://jentic.example.com"


def test_public_base_url_env_override(tmp_path: Path):
    with patch.dict(os.environ, {"JENTIC__SERVER__PUBLIC_BASE_URL": "http://127.0.0.1:8020"}):
        config = _load(tmp_path, {})
    assert config.server.public_base_url == "http://127.0.0.1:8020"


def test_public_base_url_rejects_userinfo(tmp_path: Path):
    with pytest.raises(ConfigError):
        _load(tmp_path, {"server": {"public_base_url": "http://user:pw@host"}})


def test_redirect_uri_now_optional(tmp_path: Path):
    config = _load(
        tmp_path,
        {"credentials": {"providers": {"direct_oauth2": {"kind": "direct_oauth2"}}}},
    )
    pc = config.credentials.providers["direct_oauth2"]
    assert isinstance(pc, DirectOAuth2ProviderConfig)
    assert pc.redirect_uri is None


def test_effective_auth_base_url_precedence(tmp_path: Path):
    # explicit auth canonical wins over public_base_url
    config = _load(
        tmp_path,
        {
            "server": {"public_base_url": "https://public.example.com"},
            "auth": {"canonical_base_url": "https://auth.example.com"},
        },
    )
    assert effective_auth_base_url(config) == "https://auth.example.com"

    # public_base_url alone flows through
    config = _load(tmp_path, {"server": {"public_base_url": "https://public.example.com"}})
    assert effective_auth_base_url(config) == "https://public.example.com"

    # both unset ≡ empty (today's behaviour)
    config = _load(tmp_path, {})
    assert effective_auth_base_url(config) == ""


def test_broker_links_root_on_broker_only_for_an_unpinned_standalone_broker(tmp_path: Path):
    standalone = {"apps": ["broker"]}
    assert broker_links_root_on_broker(_load(tmp_path, standalone)) is True
    pinned = {**standalone, "broker": {"jobs_api_base_url": "https://jentic.example.com"}}
    assert broker_links_root_on_broker(_load(tmp_path, pinned)) is False
    assert broker_links_root_on_broker(_load(tmp_path, {"apps": ["broker", "admin"]})) is False
    assert broker_links_root_on_broker(_load(tmp_path, {})) is False


def test_check_public_url_consistency(tmp_path: Path):
    # unset fields self-derive → no mismatch
    config = _load(tmp_path, {"server": {"host": "127.0.0.1", "port": 8020}})
    assert check_public_url_consistency(config) == []

    # loopback-equivalent explicit URL against a 0.0.0.0 bind → no mismatch
    config = _load(
        tmp_path,
        {
            "server": {"host": "0.0.0.0", "port": 8020},
            "auth": {"canonical_base_url": "http://127.0.0.1:8020"},
        },
    )
    assert check_public_url_consistency(config) == []

    # port mismatch against the serving bind → exactly one entry
    config = _load(
        tmp_path,
        {
            "server": {"host": "127.0.0.1", "port": 8020},
            "auth": {"canonical_base_url": "http://127.0.0.1:8000"},
        },
    )
    mismatches = check_public_url_consistency(config)
    assert len(mismatches) == 1
    assert mismatches[0].field == "auth.canonical_base_url"

    # public_base_url explains the origin → no mismatch even when it differs
    # from the bind
    config = _load(
        tmp_path,
        {
            "server": {
                "host": "0.0.0.0",
                "port": 8020,
                "public_base_url": "https://gw.example.com",
            },
            "auth": {"canonical_base_url": "https://gw.example.com"},
        },
    )
    assert check_public_url_consistency(config) == []


def test_check_public_url_consistency_excludes_provider_redirect_uri(tmp_path: Path):
    # A provider redirect_uri override that legitimately differs from the public
    # origin (split-origin gateway/NodePort) must NOT be flagged — otherwise the
    # reference Helm deployment fires a false positive on every boot.
    config = _load(
        tmp_path,
        {
            "server": {"public_base_url": "http://localhost:8000"},
            "credentials": {
                "providers": {
                    "direct_oauth2": {
                        "kind": "direct_oauth2",
                        "redirect_uri": "http://127.0.0.1:30080/credentials/oauth/callback",
                    }
                }
            },
        },
    )
    assert check_public_url_consistency(config) == []


def test_check_public_url_consistency_never_crashes_on_bad_port(tmp_path: Path):
    # An out-of-range port must be rejected at config load (ValueError), not
    # crash the consistency check at startup — the "warn, don't crash" contract.
    with pytest.raises(ConfigError):
        _load(tmp_path, {"auth": {"canonical_base_url": "http://h:99999"}})


@pytest.mark.parametrize(
    ("host", "port", "expected"),
    [
        ("0.0.0.0", 8000, "http://127.0.0.1:8000"),
        ("::", 8020, "http://127.0.0.1:8020"),
        ("127.0.0.1", 8020, "http://127.0.0.1:8020"),
        ("localhost", 9000, "http://localhost:9000"),
        ("::1", 8000, "http://[::1]:8000"),
        ("10.0.0.5", 8000, "http://10.0.0.5:8000"),
    ],
)
def test_bind_origin(tmp_path: Path, host: str, port: int, expected: str):
    config = _load(tmp_path, {"server": {"host": host, "port": port}})
    assert bind_origin(config) == expected


def test_resolved_auth_base_url_falls_back_to_bind(tmp_path: Path):
    # Request-less consumers (issuer, JWT-Bearer audience, DCR) follow the port
    # with nothing pinned — the #818 any-port contract.
    config = _load(tmp_path, {"server": {"host": "127.0.0.1", "port": 8020}})
    assert effective_auth_base_url(config) == ""
    assert resolved_auth_base_url(config) == "http://127.0.0.1:8020"

    config = _load(
        tmp_path,
        {"server": {"host": "127.0.0.1", "port": 8020, "public_base_url": "https://x.example"}},
    )
    assert resolved_auth_base_url(config) == "https://x.example"


def _spa_redirects(config: AppConfig) -> list[str]:
    return next(
        pc.redirect_uris for pc in config.auth.platform_clients if pc.client_id == "jentic-one-spa"
    )


def test_spa_platform_client_derived_from_loopback_bind(tmp_path: Path):
    config = _load(tmp_path, {"server": {"host": "127.0.0.1", "port": 8020}})
    assert _spa_redirects(config) == [
        "http://127.0.0.1:8020/app/auth/callback",
        "http://localhost:8020/app/auth/callback",
    ]


def test_spa_platform_client_derived_from_public_base_url(tmp_path: Path):
    config = _load(tmp_path, {"server": {"public_base_url": "https://jentic.example.com"}})
    assert _spa_redirects(config) == ["https://jentic.example.com/app/auth/callback"]


def test_spa_platform_client_explicit_wins(tmp_path: Path):
    explicit = {"client_id": "jentic-one-spa", "redirect_uris": ["https://spa.example/cb"]}
    config = _load(
        tmp_path,
        {
            "server": {"public_base_url": "https://jentic.example.com"},
            "auth": {"platform_clients": [explicit]},
        },
    )
    assert _spa_redirects(config) == ["https://spa.example/cb"]


def test_spa_platform_client_skipped_for_plain_http_lan_bind(tmp_path: Path):
    # Platform redirect URIs refuse plain http off localhost; config must still
    # load (SPA login then needs an explicit https public_base_url).
    config = _load(tmp_path, {"server": {"host": "10.0.0.5", "port": 8000}})
    assert not any(pc.client_id == "jentic-one-spa" for pc in config.auth.platform_clients)


def test_check_public_url_consistency_flags_loopback_public_url_on_wrong_port(tmp_path: Path):
    # The #818 repro: a loopback bind on 8020 with public_base_url still pinned
    # to :8000 — every derived URL is unreachable.
    config = _load(
        tmp_path,
        {"server": {"host": "127.0.0.1", "port": 8020, "public_base_url": "http://127.0.0.1:8000"}},
    )
    mismatches = check_public_url_consistency(config)
    assert [m.field for m in mismatches] == ["server.public_base_url"]
    assert mismatches[0].expected == "http://127.0.0.1:8020"


def test_check_public_url_consistency_flags_stale_loopback_redirect_uri(tmp_path: Path):
    config = _load(
        tmp_path,
        {
            "server": {"host": "127.0.0.1", "port": 8020},
            "credentials": {
                "providers": {
                    "direct_oauth2": {
                        "kind": "direct_oauth2",
                        "redirect_uri": "http://127.0.0.1:8000/credentials/oauth/callback",
                    }
                }
            },
        },
    )
    mismatches = check_public_url_consistency(config)
    assert [m.field for m in mismatches] == ["credentials.providers.direct_oauth2.redirect_uri"]


def test_check_public_url_consistency_loopback_check_skips_all_interfaces_bind(tmp_path: Path):
    # A 0.0.0.0 bind can sit behind a port mapping / NodePort that legitimately
    # fronts it on another loopback port — no warning.
    config = _load(
        tmp_path,
        {"server": {"host": "0.0.0.0", "port": 8000, "public_base_url": "http://localhost:30080"}},
    )
    assert check_public_url_consistency(config) == []


def test_check_public_url_consistency_loopback_check_ignores_non_loopback_urls(tmp_path: Path):
    # A loopback bind behind a same-host reverse proxy serving a real hostname.
    config = _load(
        tmp_path,
        {
            "server": {
                "host": "127.0.0.1",
                "port": 8000,
                "public_base_url": "https://jentic.example.com",
            }
        },
    )
    assert check_public_url_consistency(config) == []


def test_check_public_url_consistency_skips_overrides_on_all_interfaces_bind(tmp_path: Path):
    # No public_base_url + a 0.0.0.0 bind: the public origin is unknowable
    # (proxy / ingress), so a correctly-proxied override must not be flagged.
    config = _load(
        tmp_path,
        {
            "server": {"host": "0.0.0.0", "port": 8000},
            "auth": {"canonical_base_url": "https://jentic.example.com"},
            "control": {"access_requests": {"canonical_base_url": "https://jentic.example.com"}},
        },
    )
    assert check_public_url_consistency(config) == []


def test_check_public_url_consistency_ignores_broker_urls(tmp_path: Path):
    # The broker's jobs URL names another service's origin.
    config = _load(
        tmp_path,
        {
            "server": {"host": "127.0.0.1", "port": 8000},
            "broker": {"jobs_api_base_url": "https://api.example.com"},
        },
    )
    assert check_public_url_consistency(config) == []


def test_redirect_uri_kept_byte_identical(tmp_path: Path):
    # The IdP exact-matches the registered redirect URI: no slash stripping,
    # and a query string is legal (RFC 6749 §3.1.2).
    for uri in (
        "https://app.example.com/credentials/oauth/callback/",
        "https://app.example.com/credentials/oauth/callback?tenant=a",
    ):
        config = _load(
            tmp_path,
            {
                "credentials": {
                    "providers": {"direct_oauth2": {"kind": "direct_oauth2", "redirect_uri": uri}}
                }
            },
        )
        pc = config.credentials.providers["direct_oauth2"]
        assert isinstance(pc, DirectOAuth2ProviderConfig)
        assert pc.redirect_uri == uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://app.example.com/cb#frag",
        "ftp://app.example.com/cb",
        "https://user:pw@app.example.com/cb",
        "https://app.example.com:99999/cb",
    ],
)
def test_redirect_uri_rejects_invalid(tmp_path: Path, uri: str):
    with pytest.raises(ConfigError):
        _load(
            tmp_path,
            {
                "credentials": {
                    "providers": {"direct_oauth2": {"kind": "direct_oauth2", "redirect_uri": uri}}
                }
            },
        )


@pytest.mark.parametrize("host", ["localhost", "0.0.0.0"])
def test_spa_platform_client_registers_both_loopback_aliases(tmp_path: Path, host: str):
    # A local browser reaches the process as 127.0.0.1 or localhost whichever
    # alias the bind names.
    config = _load(tmp_path, {"server": {"host": host, "port": 8020}})
    redirects = _spa_redirects(config)
    assert "http://127.0.0.1:8020/app/auth/callback" in redirects
    assert "http://localhost:8020/app/auth/callback" in redirects
    assert len(redirects) == len(set(redirects))


def test_has_spa_platform_client(tmp_path: Path):
    assert has_spa_platform_client(_load(tmp_path, {"server": {"host": "127.0.0.1"}}))
    assert not has_spa_platform_client(_load(tmp_path, {"server": {"host": "10.0.0.5"}}))


def test_shipped_local_configs_follow_the_port(monkeypatch: pytest.MonkeyPatch):
    # Guards against re-pinning :8000 in the shipped local configs.
    monkeypatch.setenv("JENTIC__SERVER__PORT", "8020")
    root = Path(__file__).resolve().parents[2]
    for name in ("local.yaml", "local-sqlite.yaml"):
        config = load_config(root / "config" / name)
        assert resolved_auth_base_url(config) == "http://127.0.0.1:8020", name
        assert "http://127.0.0.1:8020/app/auth/callback" in _spa_redirects(config), name
        assert check_public_url_consistency(config) == [], name


def test_telemetry_host_os_defaults_to_none():
    """Hand-rolled configs (no CLI stamp) leave host_os unset → runtime fallback."""
    assert TelemetryConfig().host_os is None


def test_telemetry_host_os_from_yaml(tmp_path: Path):
    minimal = {
        "databases": {
            "registry": {"name": "reg"},
            "admin": {"name": "admin"},
            "control": {"name": "ctrl"},
        },
        "telemetry": {"enabled": True, "host_os": "darwin"},
    }
    path = tmp_path / "telemetry.yaml"
    path.write_text(yaml.dump(minimal))
    config = load_config(path)
    assert config.telemetry.host_os == "darwin"


def test_telemetry_host_os_env_override(config_file: Path):
    with patch.dict(os.environ, {"JENTIC__TELEMETRY__HOST_OS": "windows"}):
        config = load_config(config_file)
    assert config.telemetry.host_os == "windows"


# --- EntitlementConfig (AWS Marketplace license gate) -------------------------


def test_entitlement_defaults_off(config_file: Path):
    config = load_config(config_file)
    assert config.entitlement.enabled is False
    assert config.entitlement.product_code is None
    # The live listing is contract-priced, hence the default.
    assert config.entitlement.pricing_model == "contract"
    assert config.entitlement.license_dimensions == []


def test_entitlement_enabled_requires_product_code():
    with pytest.raises(ValidationError, match=r"entitlement\.product_code"):
        EntitlementConfig(enabled=True)


def test_entitlement_contract_requires_sku():
    with pytest.raises(ValidationError, match=r"entitlement\.license_sku"):
        EntitlementConfig(enabled=True, product_code="prod-abc", pricing_model="contract")


def test_entitlement_env_override_round_trip(config_file: Path):
    env = {
        "JENTIC__ENTITLEMENT__ENABLED": "true",
        "JENTIC__ENTITLEMENT__PRODUCT_CODE": "prod-abc123",
        "JENTIC__ENTITLEMENT__LICENSE_SKU": "prod-id-abc123",
        "JENTIC__ENTITLEMENT__LICENSE_DIMENSIONS": "users,executions",
        "JENTIC__ENTITLEMENT__REGION": "eu-west-1",
        "JENTIC__ENTITLEMENT__REFRESH_INTERVAL_SECONDS": "600",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.entitlement.enabled is True
    assert config.entitlement.product_code == "prod-abc123"
    assert config.entitlement.license_sku == "prod-id-abc123"
    assert config.entitlement.license_dimensions == ["users", "executions"]
    assert config.entitlement.region == "eu-west-1"
    assert config.entitlement.refresh_interval_seconds == 600


def test_db_connection_tunables_default_and_env_override(
    config_file: Path, tmp_path: Path, sample_config_dict: dict[str, Any]
):
    """Pre-ping, pool recycle and TCP keepalive are per-database ``databases.*`` settings."""
    default = load_config(config_file).databases.registry
    assert default.pool_pre_ping is True
    assert default.pool_recycle_s == config_module.POOL_RECYCLE_S == 1800
    assert default.tcp_keepalive is True
    assert default.tcp_keepalive_idle_s == config_module.TCP_KEEPALIVE_IDLE_S == 60
    assert default.tcp_keepalive_interval_s == config_module.TCP_KEEPALIVE_INTERVAL_S == 10
    assert default.tcp_keepalive_count == config_module.TCP_KEEPALIVE_COUNT == 5
    env = {
        "JENTIC__DATABASES__REGISTRY__POOL_PRE_PING": "false",
        "JENTIC__DATABASES__REGISTRY__POOL_RECYCLE_S": "0",
        "JENTIC__DATABASES__REGISTRY__TCP_KEEPALIVE": "false",
        "JENTIC__DATABASES__CONTROL__TCP_KEEPALIVE_IDLE_S": "30",
        "JENTIC__DATABASES__CONTROL__TCP_KEEPALIVE_INTERVAL_S": "3",
        "JENTIC__DATABASES__CONTROL__TCP_KEEPALIVE_COUNT": "9",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.databases.registry.pool_pre_ping is False
    assert config.databases.registry.pool_recycle_s == 0
    assert config.databases.registry.tcp_keepalive is False
    control = config.databases.control
    assert control.pool_pre_ping is True
    assert (control.tcp_keepalive_idle_s, control.tcp_keepalive_interval_s) == (30, 3)
    assert control.tcp_keepalive_count == 9
    assert config.databases.admin.tcp_keepalive_idle_s == 60

    doc = copy.deepcopy(sample_config_dict)
    doc["databases"]["admin"]["pool_pre_ping"] = False
    from_yaml = tmp_path / "no-pre-ping.yaml"
    from_yaml.write_text(yaml.dump(doc))
    databases = load_config(from_yaml).databases
    assert databases.admin.pool_pre_ping is False
    assert databases.registry.pool_pre_ping is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("POOL_RECYCLE_S", "-1"),
        ("TCP_KEEPALIVE_IDLE_S", "0"),
        ("TCP_KEEPALIVE_IDLE_S", "32768"),
        ("TCP_KEEPALIVE_INTERVAL_S", "0"),
        ("TCP_KEEPALIVE_INTERVAL_S", "32768"),
        ("TCP_KEEPALIVE_COUNT", "0"),
        ("TCP_KEEPALIVE_COUNT", "128"),
    ],
)
def test_db_connection_tunables_out_of_bounds_rejected(config_file: Path, key: str, value: str):
    env = {f"JENTIC__DATABASES__REGISTRY__{key}": value}
    with patch.dict(os.environ, env), pytest.raises(ConfigError, match=key.lower()):
        load_config(config_file)


def test_worker_reconnect_backoff_default_and_env_override(config_file: Path):
    default = load_config(config_file).worker
    assert (default.reconnect_backoff_base_s, default.reconnect_backoff_max_s) == (2.0, 30.0)
    env = {
        "JENTIC__WORKER__RECONNECT_BACKOFF_BASE_S": "0.5",
        "JENTIC__WORKER__RECONNECT_BACKOFF_MAX_S": "120",
    }
    with patch.dict(os.environ, env, clear=False):
        worker = load_config(config_file).worker
    assert (worker.reconnect_backoff_base_s, worker.reconnect_backoff_max_s) == (0.5, 120.0)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RECONNECT_BACKOFF_BASE_S", "0"),
        ("RECONNECT_BACKOFF_BASE_S", "301"),
        ("RECONNECT_BACKOFF_MAX_S", "0"),
        ("RECONNECT_BACKOFF_MAX_S", "3601"),
    ],
)
def test_worker_reconnect_backoff_out_of_bounds_rejected(config_file: Path, key: str, value: str):
    with (
        patch.dict(os.environ, {f"JENTIC__WORKER__{key}": value}),
        pytest.raises(ConfigError, match=key.lower()),
    ):
        load_config(config_file)


# --- trusted-issuer jwks_url scheme (#1723) -----------------------------------

_HTTP_JWKS_EVENT = "trusted_issuer_jwks_url_plain_http"


def _http_jwks_warnings(logs: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [log for log in logs if log["event"] == _HTTP_JWKS_EVENT]


def test_trusted_issuer_http_jwks_url_accepted_outside_production():
    """Outside production a plain-http JWKS is accepted, with one warning naming the issuer."""
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "development"}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        cfg = TrustedIssuerConfig(issuer="https://idp.local", jwks_url="http://idp.local/jwks")
    assert cfg.jwks_url == "http://idp.local/jwks"
    warnings = _http_jwks_warnings(logs)
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["issuer"] == "https://idp.local"


def test_trusted_issuer_http_jwks_url_accepted_when_jentic_env_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    """Unset JENTIC_ENV is development — the same default every production guard uses."""
    monkeypatch.delenv("JENTIC_ENV", raising=False)
    with structlog.testing.capture_logs() as logs:
        TrustedIssuerConfig(issuer="https://idp.local", jwks_url="http://idp.local/jwks")
    assert len(_http_jwks_warnings(logs)) == 1


def test_trusted_issuer_http_jwks_url_rejected_in_production():
    """Production keeps https mandatory; the error names the field and the rule."""
    with (
        patch.dict(os.environ, {"JENTIC_ENV": "production"}, clear=False),
        pytest.raises(ValidationError) as excinfo,
    ):
        TrustedIssuerConfig(issuer="https://idp.local", jwks_url="http://idp.local/jwks")
    message = str(excinfo.value)
    assert "jwks_url must be https:// in production" in message
    assert "http:// is only allowed outside production" in message
    assert "https://idp.local" in message


@pytest.mark.parametrize("env", ["production", "development"])
def test_trusted_issuer_https_jwks_url_accepted_without_warning(env: str):
    with (
        patch.dict(os.environ, {"JENTIC_ENV": env}, clear=False),
        structlog.testing.capture_logs() as logs,
    ):
        cfg = TrustedIssuerConfig(issuer="https://idp.local", jwks_url="https://idp.local/jwks")
    assert cfg.jwks_url == "https://idp.local/jwks"
    assert _http_jwks_warnings(logs) == []


@pytest.mark.parametrize("env", ["production", "development"])
@pytest.mark.parametrize(
    "url", ["file:///etc/jwks.json", "ftp://idp.local/jwks", "idp.local/jwks", "HTTP://idp/x"]
)
def test_trusted_issuer_non_http_jwks_url_rejected_everywhere(env: str, url: str):
    """Only http(s) is a JWKS endpoint — the fetcher (urllib) would also open file://."""
    with (
        patch.dict(os.environ, {"JENTIC_ENV": env}, clear=False),
        pytest.raises(ValidationError, match="jwks_url must be"),
    ):
        TrustedIssuerConfig(issuer="https://idp.local", jwks_url=url)


_ISSUER_ENV = {
    "JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__0__ISSUER": "https://idp-a.local",
    "JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__0__JWKS_URL": "http://idp-a.local/jwks",
    "JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__1__ISSUER": "https://idp-b.local",
    "JENTIC__BROKER__JWT_VERIFICATION__TRUSTED_ISSUERS__1__JWKS_URL": "http://idp-b.local/jwks",
}


def test_http_jwks_url_env_override_outside_production(config_file: Path):
    """The env-var path loads http issuers outside production, one warning per issuer."""
    env = {"JENTIC_ENV": "development"} | _ISSUER_ENV
    with patch.dict(os.environ, env, clear=False), structlog.testing.capture_logs() as logs:
        config = load_config(config_file)
    issuers = config.broker.jwt_verification.trusted_issuers
    assert [i.jwks_url for i in issuers] == ["http://idp-a.local/jwks", "http://idp-b.local/jwks"]
    warned = sorted(log["issuer"] for log in _http_jwks_warnings(logs))
    assert warned == ["https://idp-a.local", "https://idp-b.local"]
    assert all("jwks_url" not in log for log in _http_jwks_warnings(logs))


def test_http_jwks_url_env_override_rejected_in_production(config_file: Path):
    env = {"JENTIC_ENV": "production", "JENTIC__APPS": "broker"} | _ISSUER_ENV
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(ConfigError, match=r"jwks_url must be https:// in production"),
    ):
        load_config(config_file)


def test_streaming_persist_and_drain_budgets_default(tmp_path: Path):
    config = _load(tmp_path, {})
    assert config.broker.streaming_persist_timeout_s == 10.0
    assert config.services.background_task_drain_timeout_s == 10.0


def test_streaming_persist_and_drain_budgets_from_yaml_and_env(tmp_path: Path):
    config = _load(
        tmp_path,
        {
            "broker": {"streaming_persist_timeout_s": 2.5},
            "services": {"background_task_drain_timeout_s": 0},
        },
    )
    assert config.broker.streaming_persist_timeout_s == 2.5
    assert config.services.background_task_drain_timeout_s == 0
    env = {
        "JENTIC__BROKER__STREAMING_PERSIST_TIMEOUT_S": "4",
        "JENTIC__SERVICES__BACKGROUND_TASK_DRAIN_TIMEOUT_S": "7.5",
    }
    with patch.dict(os.environ, env, clear=False):
        config = _load(tmp_path, {})
    assert config.broker.streaming_persist_timeout_s == 4.0
    assert config.services.background_task_drain_timeout_s == 7.5


@pytest.mark.parametrize(
    "extra",
    [
        {"broker": {"streaming_persist_timeout_s": 0}},
        {"services": {"background_task_drain_timeout_s": -1}},
    ],
)
def test_streaming_persist_and_drain_budgets_reject_invalid(tmp_path: Path, extra: dict[str, Any]):
    with pytest.raises(ConfigError):
        _load(tmp_path, extra)


def test_worker_poll_and_concurrency_defaults_keep_serial_2s_poll():
    cfg = WorkerConfig()
    assert cfg.poll_interval_s == 2.0
    assert cfg.concurrency == 1


def test_worker_poll_and_concurrency_from_yaml(tmp_path: Path):
    config = _load(tmp_path, {"worker": {"poll_interval_s": 0.25, "concurrency": 4}})
    assert config.worker.poll_interval_s == 0.25
    assert config.worker.concurrency == 4


def test_worker_poll_and_concurrency_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JENTIC__WORKER__POLL_INTERVAL_S", "0.5")
    monkeypatch.setenv("JENTIC__WORKER__CONCURRENCY", "4")
    config = _load(tmp_path, {})
    assert config.worker.poll_interval_s == 0.5
    assert config.worker.concurrency == 4


@pytest.mark.parametrize("value", [0.25, 60.0])
def test_worker_poll_interval_accepts_bounds(value: float):
    assert WorkerConfig(poll_interval_s=value).poll_interval_s == value


@pytest.mark.parametrize("value", [0.0, 0.05, 0.249, 60.01, -1.0])
def test_worker_poll_interval_rejects_out_of_bounds(value: float):
    with pytest.raises(ValidationError):
        WorkerConfig(poll_interval_s=value)


@pytest.mark.parametrize("value", [0, -1, 65])
def test_worker_concurrency_rejects_out_of_bounds(value: int):
    with pytest.raises(ValidationError):
        WorkerConfig(concurrency=value)


def _with_admin(tmp_path: Path, admin: dict[str, Any], concurrency: int) -> AppConfig:
    dbs = {**_min_dbs(), "admin": admin}
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump({"databases": dbs, "worker": {"concurrency": concurrency}}))
    return load_config(path)


def test_worker_concurrency_above_one_is_refused_on_sqlite(tmp_path: Path):
    """The handler's SQLite write lock spans the upstream call: only serial is safe."""
    sqlite_admin = {"backend": "sqlite", "path": str(tmp_path / "admin.db")}
    assert _with_admin(tmp_path, sqlite_admin, 1).worker.concurrency == 1
    with pytest.raises(ConfigError, match=r"worker.concurrency=2 is not supported with a sqlite"):
        _with_admin(tmp_path, sqlite_admin, 2)


def test_worker_concurrency_above_one_is_refused_on_sqlite_via_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("JENTIC__WORKER__CONCURRENCY", "3")
    sqlite_admin = {"backend": "sqlite", "path": str(tmp_path / "admin.db")}
    with pytest.raises(ConfigError, match=r"not supported with a sqlite admin database"):
        _with_admin(tmp_path, sqlite_admin, 1)


@pytest.mark.parametrize(
    ("pool_max", "concurrency", "ok"),
    [
        (10, 4, True),  # 8 connections < 10
        (10, 5, False),  # 10 connections: no headroom
        (10, 6, False),
        (9, 4, True),
        (8, 4, False),
        (2, 1, True),  # serial is never refused
        (129, 64, True),
    ],
)
def test_worker_concurrency_must_fit_the_admin_pool_on_postgres(
    tmp_path: Path, pool_max: int, concurrency: int, ok: bool
):
    """Each in-flight job holds ~2 admin connections; 2 x concurrency must stay below pool_max."""
    admin = {"name": "admin", "pool_max": pool_max}
    if ok:
        assert _with_admin(tmp_path, admin, concurrency).worker.concurrency == concurrency
    else:
        with pytest.raises(ConfigError, match=r"2 per in-flight job.*pool_max"):
            _with_admin(tmp_path, admin, concurrency)


def test_worker_concurrency_pool_error_suggests_a_fitting_value(tmp_path: Path):
    with pytest.raises(ConfigError, match=r"lower worker.concurrency to 4"):
        _with_admin(tmp_path, {"name": "admin", "pool_max": 10}, 8)


def test_worker_sweep_interval_default_yaml_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert WorkerConfig().sweep_interval_s == 120.0
    assert _load(tmp_path, {"worker": {"sweep_interval_s": 30}}).worker.sweep_interval_s == 30.0
    monkeypatch.setenv("JENTIC__WORKER__SWEEP_INTERVAL_S", "600")
    assert _load(tmp_path, {}).worker.sweep_interval_s == 600.0


@pytest.mark.parametrize("value", [1.0, 86_400.0])
def test_worker_sweep_interval_accepts_bounds(value: float):
    assert WorkerConfig(sweep_interval_s=value).sweep_interval_s == value


@pytest.mark.parametrize("value", [0.0, 0.99, 86_400.1, -5.0])
def test_worker_sweep_interval_rejects_out_of_bounds(value: float):
    with pytest.raises(ValidationError):
        WorkerConfig(sweep_interval_s=value)


# --- memory shared-state backend budgets --------------------------------------


def test_memory_backend_namespace_budgets_are_configurable(config_file: Path):
    env = {
        "JENTIC__STATE__MEMORY__MAX_ENTRIES_PER_NAMESPACE": "500",
        "JENTIC__STATE__MEMORY__MAX_BYTES_PER_NAMESPACE": "1000000",
    }
    with patch.dict(os.environ, env, clear=False):
        memory = load_config(config_file).state.memory
    assert memory.max_entries_per_namespace == 500
    assert memory.max_bytes_per_namespace == 1_000_000
    assert memory.budgets()["idem"].max_entries == 250_000


def test_memory_backend_total_cap_defaults_and_is_configurable(config_file: Path):
    default = load_config(config_file).state.memory
    assert default.max_total_bytes == 192 * 1024 * 1024
    assert default.max_bytes_per_namespace == 16 * 1024 * 1024
    assert default.budgets()["idem"].max_bytes == 128 * 1024 * 1024
    assert default.budgets()["idem-body"].max_bytes == 32 * 1024 * 1024
    env = {"JENTIC__STATE__MEMORY__MAX_TOTAL_BYTES": "5000"}
    with patch.dict(os.environ, env, clear=False):
        memory = load_config(config_file).state.memory
    assert memory.max_total_bytes == 5_000
    backend = build_state_backend(StateBackendConfig(memory=memory))
    assert isinstance(backend, MemoryStateBackend)
    assert backend._max_total_bytes == 5_000
    with pytest.raises(ValidationError):
        MemoryBackendConfig(max_total_bytes=0)


# --- top-level ``state`` and the deprecated broker.resilience.backend alias ----


@pytest.fixture()
def _fresh_state_deprecation_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_deprecated_state_section_warned", threading.Event())


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(data))
    return path


def test_state_defaults_to_memory(config_file: Path):
    config = load_config(config_file)
    assert config.state == StateBackendConfig()
    assert config.state.backend == "memory"
    assert "backend" not in BrokerResilienceConfig.model_fields


def test_state_section_from_yaml_and_env(tmp_path: Path, sample_config_dict: dict[str, Any]):
    sample_config_dict["state"] = {"backend": "custom", "options": {"url": "x://y"}}
    path = _write(tmp_path, sample_config_dict)
    assert load_config(path).state.options == {"url": "x://y"}
    with patch.dict(os.environ, {"JENTIC__STATE__BACKEND": "other"}, clear=False):
        assert load_config(path).state.backend == "other"


@pytest.mark.usefixtures("_fresh_state_deprecation_latch")
def test_the_deprecated_yaml_section_is_read_into_state(
    tmp_path: Path, sample_config_dict: dict[str, Any]
):
    sample_config_dict["broker"] = {
        "resilience": {
            "max_in_flight": 7,
            "backend": {"backend": "memory", "memory": {"max_total_bytes": 4096}},
        }
    }
    path = _write(tmp_path, sample_config_dict)
    with structlog.testing.capture_logs() as logs:
        config = load_config(path)
        load_config(path)
    assert config.state.memory.max_total_bytes == 4096
    assert config.broker.resilience.max_in_flight == 7
    warnings = [e for e in logs if e["event"] == "config_deprecated_setting"]
    assert len(warnings) == 1, "the deprecation warning is logged once per process"
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["setting"] == "broker.resilience.backend"
    assert warnings[0]["replacement"] == "state"


@pytest.mark.usefixtures("_fresh_state_deprecation_latch")
def test_the_deprecated_env_vars_are_read_into_state(config_file: Path):
    env = {
        "JENTIC__BROKER__RESILIENCE__BACKEND__BACKEND": "redis",
        "JENTIC__BROKER__RESILIENCE__BACKEND__REDIS_URL": "redis://r:6379/0",
        "JENTIC__BROKER__RESILIENCE__BACKEND__MEMORY__MAX_TOTAL_BYTES": "5000",
    }
    with patch.dict(os.environ, env, clear=False):
        state = load_config(config_file).state
    assert state.backend == "redis"
    assert state.redis_url is not None
    assert state.redis_url.get_secret_value() == "redis://r:6379/0"
    assert state.memory.max_total_bytes == 5000


@pytest.mark.usefixtures("_fresh_state_deprecation_latch")
def test_old_and_new_sections_merge_when_they_set_different_keys(
    tmp_path: Path, sample_config_dict: dict[str, Any]
):
    sample_config_dict["state"] = {"memory": {"max_total_bytes": 4096}}
    path = _write(tmp_path, sample_config_dict)
    env = {"JENTIC__BROKER__RESILIENCE__BACKEND__MEMORY__MAX_ENTRIES_PER_NAMESPACE": "9"}
    with patch.dict(os.environ, env, clear=False):
        state = load_config(path).state
    assert state.memory.max_total_bytes == 4096
    assert state.memory.max_entries_per_namespace == 9


@pytest.mark.usefixtures("_fresh_state_deprecation_latch")
def test_old_and_new_sections_may_set_the_same_value(
    tmp_path: Path, sample_config_dict: dict[str, Any]
):
    """An env-var string and a YAML int for the same value are not a conflict."""
    sample_config_dict["state"] = {"memory": {"max_total_bytes": 4096}}
    path = _write(tmp_path, sample_config_dict)
    env = {"JENTIC__BROKER__RESILIENCE__BACKEND__MEMORY__MAX_TOTAL_BYTES": "4096"}
    with patch.dict(os.environ, env, clear=False):
        assert load_config(path).state.memory.max_total_bytes == 4096


@pytest.mark.usefixtures("_fresh_state_deprecation_latch")
@pytest.mark.parametrize(
    ("new", "old_env", "field"),
    [
        (
            {"backend": "memory"},
            {"JENTIC__BROKER__RESILIENCE__BACKEND__BACKEND": "redis"},
            "backend",
        ),
        (
            {"memory": {"max_total_bytes": 4096}},
            {"JENTIC__BROKER__RESILIENCE__BACKEND__MEMORY__MAX_TOTAL_BYTES": "5000"},
            "memory",
        ),
    ],
)
def test_conflicting_old_and_new_sections_fail_to_load(
    tmp_path: Path,
    sample_config_dict: dict[str, Any],
    new: dict[str, Any],
    old_env: dict[str, str],
    field: str,
):
    sample_config_dict["state"] = new
    path = _write(tmp_path, sample_config_dict)
    with (
        patch.dict(os.environ, old_env, clear=False),
        pytest.raises(ConfigError, match=r"broker\.resilience\.backend both set") as exc,
    ):
        load_config(path)
    assert field in str(exc.value)


@pytest.mark.usefixtures("_fresh_state_deprecation_latch")
def test_an_invalid_deprecated_value_under_a_valid_state_value_is_a_conflict(
    tmp_path: Path, sample_config_dict: dict[str, Any]
):
    """A key set under both, valid only under ``state``, must not be silently dropped."""
    sample_config_dict["state"] = {"memory": {"max_total_bytes": 4096}}
    path = _write(tmp_path, sample_config_dict)
    env = {"JENTIC__BROKER__RESILIENCE__BACKEND__MEMORY__MAX_TOTAL_BYTES": "0"}
    with (
        patch.dict(os.environ, env, clear=False),
        pytest.raises(ConfigError, match=r"broker\.resilience\.backend both set memory"),
    ):
        load_config(path)


@pytest.mark.usefixtures("_fresh_state_deprecation_latch")
def test_the_deprecated_section_may_be_a_model_instance(sample_config_dict: dict[str, Any]):
    """A programmatic caller passing a StateBackendConfig keeps working."""
    sample_config_dict["broker"] = {
        **sample_config_dict.get("broker", {}),
        "resilience": {
            "backend": StateBackendConfig.model_validate({"memory": {"max_total_bytes": 4096}})
        },
    }
    config = AppConfig.model_validate(sample_config_dict)
    assert config.state.memory.max_total_bytes == 4096


def test_a_backend_name_must_be_well_formed(tmp_path: Path, sample_config_dict: dict[str, Any]):
    sample_config_dict["state"] = {"backend": "Not A Name"}
    with pytest.raises(ConfigError, match=r"state\.backend"):
        load_config(_write(tmp_path, sample_config_dict))


def test_an_idem_override_keeps_the_per_caller_share_unless_set():
    memory = MemoryBackendConfig.model_validate(
        {
            "namespaces": {
                "idem": {"max_entries": 10, "max_bytes": 10_000},
                "idem-body": {"max_entries": 10, "max_bytes": 10_000, "max_owner_share": 1.0},
                "cb": {"max_entries": 10, "max_bytes": 10_000},
            }
        }
    )
    budgets = memory.budgets()
    assert budgets["idem"].max_owner_share == 0.1
    assert budgets["idem-body"].max_owner_share == 1.0
    assert budgets["cb"].max_owner_share is None


def test_login_csrf_has_a_per_client_share_sized_like_other_namespaces():
    default = MemoryBackendConfig().budgets()["login-csrf"]
    assert default.max_owner_share == 0.02
    assert default.max_entries == 100_000
    assert default.max_bytes == 16 * 1024 * 1024

    # It follows the per-namespace defaults, and an override keeps the share.
    resized = MemoryBackendConfig(max_entries_per_namespace=7, max_bytes_per_namespace=9_000)
    assert resized.budgets()["login-csrf"] == NamespaceBudget(
        max_entries=7, max_bytes=9_000, max_owner_share=0.02
    )
    overridden = MemoryBackendConfig.model_validate(
        {"namespaces": {"login-csrf": {"max_entries": 10, "max_bytes": 10_000}}}
    )
    assert overridden.budgets()["login-csrf"].max_owner_share == 0.02


@pytest.mark.parametrize(
    ("backend", "configured", "expected"),
    [
        ("memory", None, 3_600.0),
        ("redis", None, 86_400.0),
        ("redis-cluster", None, 86_400.0),
        ("memory", 120.0, 120.0),
        ("redis", 120.0, 120.0),
    ],
)
def test_idempotency_replay_ttl_defaults_by_backend(
    backend: str, configured: float | None, expected: float
):
    assert IdempotencyConfig(ttl_s=configured).replay_ttl_s(backend) == expected
