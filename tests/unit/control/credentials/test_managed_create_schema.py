"""Tests for provider-aware optional fields on OAuth2 credential create."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, TypeAdapter

from jentic_one.control.services.credentials.errors import InvalidCredentialInputError
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.web.schemas.credentials import (
    CredentialCreateRequest,
    NoAuthCreateRequest,
    OAuth2CreateRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    AppConfig,
    CredentialsConfig,
    DatabaseConfig,
    DatabasesConfig,
    PipedreamProviderConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import ActorType
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


def _fake_identity() -> Identity:
    return Identity(sub="user_123", actor_type=ActorType.USER)


@pytest.mark.asyncio()
async def test_static_oauth2_requires_token_url() -> None:
    ctx = _make_context()
    svc = CredentialService(ctx)
    payload = CredentialCreate(
        type=CredentialType.OAUTH2,
        name="test",
        api=APIReference(vendor="test.com", name="test", version="v1"),
        provider="static",
        client_id="id",
        client_secret="secret",
        token_url=None,
    )
    with pytest.raises(InvalidCredentialInputError, match="token_url"):
        await svc.create(payload, identity=_fake_identity())


@pytest.mark.asyncio()
async def test_static_oauth2_requires_client_id() -> None:
    ctx = _make_context()
    svc = CredentialService(ctx)
    payload = CredentialCreate(
        type=CredentialType.OAUTH2,
        name="test",
        api=APIReference(vendor="test.com", name="test", version="v1"),
        provider="static",
        token_url="https://example.com/token",
        client_id=None,
        client_secret="secret",
    )
    with pytest.raises(InvalidCredentialInputError, match="client_id"):
        await svc.create(payload, identity=_fake_identity())


@pytest.mark.asyncio()
async def test_static_oauth2_requires_client_secret() -> None:
    ctx = _make_context()
    svc = CredentialService(ctx)
    payload = CredentialCreate(
        type=CredentialType.OAUTH2,
        name="test",
        api=APIReference(vendor="test.com", name="test", version="v1"),
        provider="static",
        token_url="https://example.com/token",
        client_id="id",
        client_secret=None,
    )
    with pytest.raises(InvalidCredentialInputError, match="client_secret"):
        await svc.create(payload, identity=_fake_identity())


@pytest.mark.asyncio()
async def test_managed_oauth2_does_not_require_client_fields() -> None:
    """A managed provider should not raise InvalidCredentialInputError for missing fields."""
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
    payload = CredentialCreate(
        type=CredentialType.OAUTH2,
        name="Managed OAuth2 cred",
        api=APIReference(vendor="slack.com", name="slack", version="v1"),
        provider="my_pipedream",
        token_url=None,
        client_id=None,
        client_secret=None,
    )
    # Should not raise InvalidCredentialInputError about missing fields.
    # It will fail at the DB/encryption layer — but validation passes.
    with pytest.raises(Exception) as exc_info:
        await svc.create(payload, identity=_fake_identity())
    assert not isinstance(exc_info.value, InvalidCredentialInputError)


def test_oauth2_web_schema_fields_optional() -> None:
    """OAuth2CreateRequest should accept omitted client fields."""
    req = OAuth2CreateRequest(
        type="oauth2",
        name="test",
        api={"vendor": "test.com", "name": "test", "version": "v1"},  # type: ignore[arg-type]
        provider="my_pipedream",
    )
    assert req.client_id is None
    assert req.client_secret is None
    assert req.token_url is None


def test_oauth2_web_schema_still_accepts_full_fields() -> None:
    """OAuth2CreateRequest should still accept all fields for static provider."""
    req = OAuth2CreateRequest(
        type="oauth2",
        name="test",
        api={"vendor": "test.com", "name": "test", "version": "v1"},  # type: ignore[arg-type]
        provider="static",
        token_url="https://example.com/token",
        client_id="my_id",
        client_secret="my_secret",
    )
    assert req.client_id == "my_id"
    assert req.client_secret == "my_secret"
    assert req.token_url == "https://example.com/token"


@pytest.mark.asyncio()
async def test_non_oauth2_types_unaffected() -> None:
    """Bearer token creation should still enforce its own required fields."""
    ctx = _make_context()
    svc = CredentialService(ctx)
    payload = CredentialCreate(
        type=CredentialType.BEARER_TOKEN,
        name="test",
        api=APIReference(vendor="test.com", name="test", version="v1"),
        provider="static",
        token=None,
    )
    with pytest.raises(InvalidCredentialInputError, match="token"):
        await svc.create(payload, identity=_fake_identity())


def test_credential_create_request_accepts_no_auth() -> None:
    """The create-request union must accept a `no_auth` body.

    Regression for a 422 (union_tag_invalid) hit provisioning a no-auth API: the
    provisioning wizard auto-creates a NO_AUTH credential (no secret) so the
    credential:bind can attach the toolkit binding + rules, but the create union
    only listed bearer_token/api_key/basic/oauth2 — so POST /credentials rejected
    `type=no_auth` before reaching the (already no_auth-aware) service.
    """
    ta = TypeAdapter(CredentialCreateRequest)
    model = ta.validate_python(
        {
            "type": "no_auth",
            "provider": "static",
            "name": "country-is/country-is (no-auth)",
            "api": {"vendor": "country-is", "name": "country-is"},
        }
    )
    assert isinstance(model, NoAuthCreateRequest)
    assert model.type == "no_auth"
    assert model.name == "country-is/country-is (no-auth)"
