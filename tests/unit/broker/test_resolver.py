"""Unit tests for broker credential resolver."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialNotProvisionedError,
)
from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.schemas import APIReference


def _make_credential(
    *,
    cred_id: str = "cred_abc",
    type: str = "STATIC_BEARER_TOKEN",
    api_vendor: str = "stripe",
    api_name: str | None = "payments",
    api_version: str | None = "v1",
    active: bool = True,
    provider: str = "static",
    server_variables: dict[str, str] | None = None,
) -> MagicMock:
    cred = MagicMock()
    cred.id = cred_id
    cred.type = type
    cred.api_vendor = api_vendor
    cred.api_name = api_name
    cred.api_version = api_version
    cred.active = active
    cred.provider = provider
    cred.server_variables = server_variables
    cred.token_value_credential = None
    cred.customer_api_key = None
    cred.basic_credential = None
    cred.oauth_client_credential = None
    cred.oauth_token = None
    return cred


def _make_ctx_with_control_session(session_mock: AsyncMock) -> MagicMock:
    ctx = MagicMock()

    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[AsyncMock, None]:
        yield session_mock

    ctx.control_db.session = _fake_session
    return ctx


@pytest.mark.asyncio
async def test_resolve_bearer_token() -> None:
    cred = _make_credential()
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:token"
    cred.token_value_credential = tvc

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert isinstance(result, ResolvedCredential)
    assert result.credential_id == "cred_abc"
    assert result.wire_type == CredentialType.BEARER_TOKEN
    assert result.encrypted_secret == "enc:token"


@pytest.mark.asyncio
async def test_resolve_api_key() -> None:
    cred = _make_credential(type="API_KEY")
    cak = MagicMock()
    cak.encrypted_key = "enc:key"
    cak.location = "header"
    cak.field_name = "X-Api-Key"
    cred.customer_api_key = cak

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.wire_type == CredentialType.API_KEY
    assert result.encrypted_secret == "enc:key"
    assert result.location == "header"
    assert result.field_name == "X-Api-Key"


@pytest.mark.asyncio
async def test_resolve_basic() -> None:
    cred = _make_credential(type="BASIC_AUTH")
    bc = MagicMock()
    bc.username = "admin"
    bc.encrypted_password = "enc:pass"
    cred.basic_credential = bc

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.wire_type == CredentialType.BASIC
    assert result.username == "admin"
    assert result.encrypted_password == "enc:pass"


@pytest.mark.asyncio
async def test_resolve_oauth2() -> None:
    cred = _make_credential(type="OAUTH2_CLIENT_CREDENTIALS")
    token = MagicMock()
    token.encrypted_access_token = "enc:at"
    token.encrypted_refresh_token = "enc:rt"
    token.expires_at = None
    cred.oauth_token = token
    cred.provider_account_ref = None

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.wire_type == CredentialType.OAUTH2
    assert result.encrypted_access_token == "enc:at"
    assert result.stored_type == StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS


@pytest.mark.asyncio
async def test_resolve_not_provisioned() -> None:
    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="unknown", name="api", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resolver = CredentialResolver(ctx)
        with pytest.raises(CredentialNotProvisionedError):
            await resolver.resolve(api=api, caller="caller_1")


@pytest.mark.asyncio
async def test_resolve_ambiguous() -> None:
    cred1 = _make_credential(cred_id="cred_1")
    cred2 = _make_credential(cred_id="cred_2")

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred1, cred2],
    ):
        resolver = CredentialResolver(ctx)
        with pytest.raises(AmbiguousCredentialError):
            await resolver.resolve(api=api, caller="caller_1")


@pytest.mark.asyncio
async def test_resolve_inactive_skipped() -> None:
    active_cred = _make_credential(cred_id="cred_active")
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:active"
    active_cred.token_value_credential = tvc

    inactive_cred = _make_credential(cred_id="cred_inactive", active=False)

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[active_cred, inactive_cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_active"


@pytest.mark.asyncio
async def test_resolve_filters_by_name_and_version() -> None:
    cred_match = _make_credential(cred_id="cred_match", api_name="payments", api_version="v1")
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:match"
    cred_match.token_value_credential = tvc

    cred_other = _make_credential(cred_id="cred_other", api_name="billing", api_version="v2")

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred_match, cred_other],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_match"


@pytest.mark.asyncio
async def test_resolve_matches_non_slug_stored_name() -> None:
    """A credential stored with un-slugged name still matches the registry slug.

    Regression for #656: casing/format differences between the stored identity
    and the registry's normalized slug must not silently default-deny.
    """
    cred = _make_credential(api_vendor="stripe", api_name="Some_Name", api_version="v1")
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:token"
    cred.token_value_credential = tvc

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="some-name", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_abc"
    assert result.encrypted_secret == "enc:token"


@pytest.mark.asyncio
async def test_resolve_matches_when_only_casing_differs() -> None:
    """A registry identity that differs only by casing resolves the credential."""
    cred = _make_credential(api_vendor="stripe", api_name="Payments", api_version="v1")
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:token"
    cred.token_value_credential = tvc

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_abc"
