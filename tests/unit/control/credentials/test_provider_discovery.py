"""Tests for the provider discovery endpoint and service method."""

from __future__ import annotations

from pydantic import SecretStr

from jentic_one.control.services.credentials.providers import (
    ProviderRegistry,
    StaticProvider,
)
from jentic_one.control.services.credentials.schemas.credentials import (
    ProviderDiscoveryEntry,
)
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.config import (
    AppConfig,
    CredentialsConfig,
    DatabaseConfig,
    DatabasesConfig,
    DirectOAuth2ProviderConfig,
    PipedreamProviderConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models.credentials import CredentialType


def _make_context(credentials: CredentialsConfig | None = None) -> Context:
    cfg = AppConfig(
        databases=DatabasesConfig(
            registry=DatabaseConfig(backend="sqlite", path=":memory:"),
            admin=DatabaseConfig(backend="sqlite", path=":memory:"),
            control=DatabaseConfig(backend="sqlite", path=":memory:"),
        ),
        credentials=credentials or CredentialsConfig(),
    )
    return Context(cfg)


def test_list_providers_static_only() -> None:
    ctx = _make_context()
    svc = CredentialService(ctx)
    entries = svc.list_providers()

    assert len(entries) == 1
    static = entries[0]
    assert static.id == "static"
    assert static.managed is False
    assert static.configured is True
    assert set(static.types) == set(CredentialType)


def test_list_providers_includes_managed_providers() -> None:
    credentials_cfg = CredentialsConfig(
        providers={
            "my_pipedream": PipedreamProviderConfig(
                project_id="proj_1",
                client_id="c_id",
                client_secret=SecretStr("c_secret"),
            ),
        }
    )
    ctx = _make_context(credentials_cfg)
    svc = CredentialService(ctx)
    entries = svc.list_providers()

    assert len(entries) == 2
    by_id = {e.id: e for e in entries}

    assert "static" in by_id
    assert "my_pipedream" in by_id

    pd = by_id["my_pipedream"]
    assert pd.managed is True
    assert pd.types == [CredentialType.OAUTH2]
    assert pd.configured is True


def test_list_providers_with_direct_oauth2() -> None:
    credentials_cfg = CredentialsConfig(
        providers={
            "direct_oauth2": DirectOAuth2ProviderConfig(
                redirect_uri="https://example.com/callback",
            ),
        }
    )
    ctx = _make_context(credentials_cfg)
    svc = CredentialService(ctx)
    entries = svc.list_providers()

    by_id = {e.id: e for e in entries}
    assert "direct_oauth2" in by_id
    do = by_id["direct_oauth2"]
    assert do.managed is True
    assert do.types == [CredentialType.OAUTH2]
    assert do.callback_url == "https://example.com/callback"


def test_list_providers_callback_url_none_for_non_oauth2() -> None:
    ctx = _make_context()
    svc = CredentialService(ctx)
    entries = svc.list_providers()

    static = entries[0]
    assert static.callback_url is None


def test_list_providers_uses_derived_callback_when_unconfigured() -> None:
    # Issue #818: no explicit redirect_uri → discovery reflects the request-
    # derived default the connect flow would actually use.
    credentials_cfg = CredentialsConfig(
        providers={"direct_oauth2": DirectOAuth2ProviderConfig()},
    )
    ctx = _make_context(credentials_cfg)
    svc = CredentialService(ctx)
    derived = "http://127.0.0.1:8020/credentials/oauth/callback"
    entries = svc.list_providers(default_callback_url=derived)

    by_id = {e.id: e for e in entries}
    assert by_id["direct_oauth2"].callback_url == derived


def test_list_providers_configured_redirect_uri_wins_over_derived() -> None:
    credentials_cfg = CredentialsConfig(
        providers={
            "direct_oauth2": DirectOAuth2ProviderConfig(
                redirect_uri="https://explicit.example.com/credentials/oauth/callback",
            ),
        }
    )
    ctx = _make_context(credentials_cfg)
    svc = CredentialService(ctx)
    entries = svc.list_providers(
        default_callback_url="http://127.0.0.1:8020/credentials/oauth/callback"
    )

    by_id = {e.id: e for e in entries}
    assert (
        by_id["direct_oauth2"].callback_url
        == "https://explicit.example.com/credentials/oauth/callback"
    )


def test_list_providers_label_formatting() -> None:
    credentials_cfg = CredentialsConfig(
        providers={
            "direct_oauth2": DirectOAuth2ProviderConfig(
                redirect_uri="https://example.com/callback",
            ),
        }
    )
    ctx = _make_context(credentials_cfg)
    svc = CredentialService(ctx)
    entries = svc.list_providers()

    by_id = {e.id: e for e in entries}
    assert by_id["static"].label == "Static"
    assert by_id["direct_oauth2"].label == "Direct Oauth2"


def test_list_providers_returns_pydantic_models() -> None:
    ctx = _make_context()
    svc = CredentialService(ctx)
    entries = svc.list_providers()
    assert all(isinstance(e, ProviderDiscoveryEntry) for e in entries)


def test_static_provider_properties() -> None:
    provider = StaticProvider()
    assert provider.managed is False
    assert set(provider.supported_types) == set(CredentialType)


def test_registry_list_all_returns_all_providers() -> None:
    cfg = CredentialsConfig(
        providers={
            "pd": PipedreamProviderConfig(
                project_id="proj_1",
                client_id="c_id",
                client_secret=SecretStr("c_secret"),
            ),
        }
    )
    registry = ProviderRegistry.from_config(cfg)
    all_providers = registry.list_all()
    assert "static" in all_providers
    assert "pd" in all_providers
    assert len(all_providers) == 2
