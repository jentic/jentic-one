"""Unit tests for credential name disambiguation in the resolver."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialNameNotFoundError,
)
from jentic_one.broker.services.credentials.resolver import CredentialResolver
from jentic_one.shared.schemas import APIReference


def _make_credential(
    *,
    cred_id: str = "cred_abc",
    name: str = "default",
    type: str = "STATIC_BEARER_TOKEN",
    api_vendor: str = "stripe",
    api_name: str | None = "payments",
    api_version: str | None = "v1",
    active: bool = True,
    provider: str = "static",
) -> MagicMock:
    cred = MagicMock()
    cred.id = cred_id
    cred.name = name
    cred.type = type
    cred.api_vendor = api_vendor
    cred.api_name = api_name
    cred.api_version = api_version
    cred.active = active
    cred.provider = provider
    cred.server_variables = None
    cred.created_at = None
    cred.token_value_credential = MagicMock(encrypted_token_value="enc:tok")
    cred.customer_api_key = None
    cred.basic_credential = None
    cred.oauth_token = None
    return cred


def _make_ctx(session_mock: AsyncMock) -> MagicMock:
    ctx = MagicMock()

    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[AsyncMock, None]:
        yield session_mock

    ctx.control_db.session = _fake_session
    return ctx


@pytest.mark.asyncio
async def test_single_match_no_header_resolves_normally() -> None:
    cred = _make_credential(name="read-only")
    session = AsyncMock()
    ctx = _make_ctx(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        result = await CredentialResolver(ctx).resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_abc"


@pytest.mark.asyncio
async def test_multiple_matches_no_header_raises_ambiguous_with_candidates() -> None:
    cred1 = _make_credential(cred_id="cred_1", name="read-only")
    cred2 = _make_credential(cred_id="cred_2", name="admin")
    session = AsyncMock()
    ctx = _make_ctx(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with (
        patch(
            "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
            new_callable=AsyncMock,
            return_value=[cred1, cred2],
        ),
        pytest.raises(AmbiguousCredentialError) as exc,
    ):
        await CredentialResolver(ctx).resolve(api=api, caller="caller_1")

    assert {c.name for c in exc.value.candidates} == {"read-only", "admin"}
    assert {c.id for c in exc.value.candidates} == {"cred_1", "cred_2"}
    assert all(c.last4 == c.id[-4:] for c in exc.value.candidates)


@pytest.mark.asyncio
async def test_multiple_matches_with_valid_header_resolves_named() -> None:
    cred1 = _make_credential(cred_id="cred_1", name="read-only")
    cred2 = _make_credential(cred_id="cred_2", name="admin")
    session = AsyncMock()
    ctx = _make_ctx(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred1, cred2],
    ):
        result = await CredentialResolver(ctx).resolve(
            api=api, caller="caller_1", credential_name="admin"
        )

    assert result.credential_id == "cred_2"


@pytest.mark.asyncio
async def test_multiple_matches_with_invalid_header_raises_not_found() -> None:
    cred1 = _make_credential(cred_id="cred_1", name="read-only")
    cred2 = _make_credential(cred_id="cred_2", name="admin")
    session = AsyncMock()
    ctx = _make_ctx(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with (
        patch(
            "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
            new_callable=AsyncMock,
            return_value=[cred1, cred2],
        ),
        pytest.raises(CredentialNameNotFoundError) as exc,
    ):
        await CredentialResolver(ctx).resolve(
            api=api, caller="caller_1", credential_name="nonexistent"
        )

    assert exc.value.requested_name == "nonexistent"
    assert {c.name for c in exc.value.candidates} == {"read-only", "admin"}


@pytest.mark.asyncio
async def test_single_match_with_matching_header_resolves() -> None:
    cred = _make_credential(cred_id="cred_1", name="production")
    session = AsyncMock()
    ctx = _make_ctx(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        result = await CredentialResolver(ctx).resolve(
            api=api, caller="caller_1", credential_name="production"
        )

    assert result.credential_id == "cred_1"


@pytest.mark.asyncio
async def test_single_match_with_non_matching_header_raises_not_found() -> None:
    cred = _make_credential(cred_id="cred_1", name="production")
    session = AsyncMock()
    ctx = _make_ctx(session)
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with (
        patch(
            "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
            new_callable=AsyncMock,
            return_value=[cred],
        ),
        pytest.raises(CredentialNameNotFoundError) as exc,
    ):
        await CredentialResolver(ctx).resolve(api=api, caller="caller_1", credential_name="staging")

    assert exc.value.requested_name == "staging"
    assert [c.name for c in exc.value.candidates] == ["production"]
