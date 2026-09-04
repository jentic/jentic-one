"""Tests for configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from pydantic import SecretStr, ValidationError

from jentic_one.shared.config import (
    AdminAuthConfig,
    AdminInviteConfig,
    AppConfig,
    AuthConfig,
    CatalogConfig,
    ConfigError,
    ConnectConfig,
    CredentialsConfig,
    EgressConfig,
    EncryptionConfig,
    EntitlementConfig,
    RuntimeConfig,
    SigningKeyConfig,
    TelemetryConfig,
    _csv_to_list,
    _deep_merge,
    _env_overrides,
    load_config,
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
    """The approval-status poll bucket: generous defaults (a browser polls
    every few seconds), independently tunable from /authorize."""
    config = load_config(config_file)
    assert config.auth.oauth_rate_limit.approval_status_rpm == 60
    assert config.auth.oauth_rate_limit.approval_status_burst == 30


def test_oauth_approval_status_rate_limit_env_overrides(config_file: Path):
    env = {
        "JENTIC__AUTH__OAUTH_RATE_LIMIT__APPROVAL_STATUS_RPM": "6",
        "JENTIC__AUTH__OAUTH_RATE_LIMIT__APPROVAL_STATUS_BURST": "3",
    }
    with patch.dict(os.environ, env, clear=False):
        config = load_config(config_file)
    assert config.auth.oauth_rate_limit.approval_status_rpm == 6
    assert config.auth.oauth_rate_limit.approval_status_burst == 3


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


def test_csv_to_list_rejects_non_string_non_list():
    with pytest.raises(TypeError, match="expected list or comma-separated string"):
        _csv_to_list(123)


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
