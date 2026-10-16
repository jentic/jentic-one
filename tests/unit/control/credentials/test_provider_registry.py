"""Tests for ProviderRegistry and from_config construction."""

from __future__ import annotations

import inspect

import pytest
from pydantic import SecretStr

from jentic_one.control.services.credentials.providers import (
    PipedreamProvider,
    ProviderRegistry,
    StaticProvider,
    UnknownProviderError,
)
from jentic_one.shared.config import (
    CredentialsConfig,
    DirectOAuth2ProviderConfig,
    PipedreamProviderConfig,
)


def test_from_config_always_includes_static() -> None:
    cfg = CredentialsConfig()
    registry = ProviderRegistry.from_config(cfg)
    provider = registry.get("static")
    assert isinstance(provider, StaticProvider)


def test_from_config_empty_providers_only_static() -> None:
    cfg = CredentialsConfig(providers={})
    registry = ProviderRegistry.from_config(cfg)
    assert registry.get("static").name == "static"
    with pytest.raises(UnknownProviderError):
        registry.get("anything_else")


def test_get_unknown_raises() -> None:
    cfg = CredentialsConfig()
    registry = ProviderRegistry.from_config(cfg)
    with pytest.raises(UnknownProviderError, match="nope"):
        registry.get("nope")


def test_unknown_provider_error_preserves_name() -> None:
    err = UnknownProviderError("my_provider")
    assert err.provider_name == "my_provider"
    assert "my_provider" in str(err)


def test_from_config_builds_pipedream_provider() -> None:
    cfg = CredentialsConfig(
        providers={
            "my_pipedream": PipedreamProviderConfig(
                project_id="proj_1",
                client_id="c_id",
                client_secret=SecretStr("c_secret"),
            ),
        }
    )
    registry = ProviderRegistry.from_config(cfg)
    provider = registry.get("my_pipedream")
    assert isinstance(provider, PipedreamProvider)
    assert provider.name == "pipedream"


def test_from_config_multiple_pipedream_instances() -> None:
    cfg = CredentialsConfig(
        providers={
            "pipedream_slack": PipedreamProviderConfig(
                project_id="proj_slack",
                client_id="c1",
                client_secret=SecretStr("s1"),
            ),
            "pipedream_github": PipedreamProviderConfig(
                project_id="proj_gh",
                client_id="c2",
                client_secret=SecretStr("s2"),
            ),
        }
    )
    registry = ProviderRegistry.from_config(cfg)
    p1 = registry.get("pipedream_slack")
    p2 = registry.get("pipedream_github")
    assert isinstance(p1, PipedreamProvider)
    assert isinstance(p2, PipedreamProvider)
    assert p1 is not p2


def test_from_config_and_dynamic_builds_runtime_pipedream() -> None:
    cfg = CredentialsConfig()
    dynamic = {
        "pipedream": {
            "kind": "pipedream",
            "project_id": "proj_dyn",
            "client_id": "c_dyn",
            "client_secret": "s_dyn",  # pragma: allowlist secret
        }
    }
    registry = ProviderRegistry.from_config_and_dynamic(cfg, dynamic)
    provider = registry.get("pipedream")
    assert isinstance(provider, PipedreamProvider)
    assert registry.get("static").name == "static"


def test_from_config_and_dynamic_overrides_yaml() -> None:
    cfg = CredentialsConfig(
        providers={
            "pipedream": PipedreamProviderConfig(
                project_id="yaml_proj",
                client_id="yaml_client",
                client_secret=SecretStr("yaml_secret"),
            ),
        }
    )
    dynamic = {
        "pipedream": {
            "kind": "pipedream",
            "project_id": "db_proj",
            "client_id": "db_client",
            "client_secret": "db_secret",  # pragma: allowlist secret
        }
    }
    registry = ProviderRegistry.from_config_and_dynamic(cfg, dynamic)
    provider = registry.get("pipedream")
    assert isinstance(provider, PipedreamProvider)
    # The dynamic entry wins: its client_id is what the built provider carries.
    assert provider._client_id == "db_client"


def test_get_stays_synchronous() -> None:
    """`.get()` must not be a coroutine (it's on the hot resolution path)."""
    cfg = CredentialsConfig()
    registry = ProviderRegistry.from_config(cfg)
    assert not inspect.iscoroutinefunction(registry.get)


def test_mints_from_account_ref_only_for_account_ref_providers() -> None:
    """Pins #1425: the "managed provider" predicate keys on the provider's
    kind, not on ``provider_account_ref``, and on the registry name the
    credential stores (any config key). Only Pipedream connects as an account
    reference; providers that keep their grant in ``oauth_tokens`` and
    unknown names do not."""
    cfg = CredentialsConfig(
        providers={
            "my_pipedream": PipedreamProviderConfig(
                project_id="proj_1",
                client_id="c_id",
                client_secret=SecretStr("c_secret"),
            ),
            "my_oauth": DirectOAuth2ProviderConfig(redirect_uri="https://app.example.com/cb"),
        }
    )
    registry = ProviderRegistry.from_config(cfg)

    assert registry.mints_from_account_ref("my_pipedream") is True
    for name in ("my_oauth", "device_authorization", "static", "not_registered"):
        assert registry.mints_from_account_ref(name) is False
    assert registry.account_ref_provider_names() == frozenset({"my_pipedream"})
