"""Unit tests for CompositeTokenValidator dispatch logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth.token_validation import CompositeTokenValidator
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE


def _make_identity(
    *,
    sub: str = "agnt_test1",
    actor_type: ActorType = ActorType.AGENT,
) -> Identity:
    return Identity(
        sub=sub,
        actor_type=actor_type,
        permissions=[BROKER_EXECUTE_SCOPE],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
    )


@pytest.fixture()
def opaque_resolver() -> AsyncMock:
    resolver = AsyncMock()
    resolver.resolve_access_token = AsyncMock(return_value=_make_identity(sub="usr_opaque"))
    return resolver


@pytest.fixture()
def api_key_resolver() -> AsyncMock:
    resolver = AsyncMock()
    resolver.resolve_access_token = AsyncMock(
        return_value=_make_identity(sub="agnt_apikey", actor_type=ActorType.AGENT)
    )
    return resolver


@pytest.fixture()
def toolkit_key_resolver() -> AsyncMock:
    resolver = AsyncMock()
    resolver.resolve_access_token = AsyncMock(
        return_value=_make_identity(sub="tk_toolkit1", actor_type=ActorType.TOOLKIT)
    )
    return resolver


@pytest.fixture()
def triple(
    opaque_resolver: AsyncMock, api_key_resolver: AsyncMock, toolkit_key_resolver: AsyncMock
) -> CompositeTokenValidator:
    opaque_cached = CachedTokenValidator(resolver=opaque_resolver, cache_ttl_seconds=5.0)
    api_key_cached = CachedTokenValidator(resolver=api_key_resolver, cache_ttl_seconds=5.0)
    toolkit_cached = CachedTokenValidator(resolver=toolkit_key_resolver, cache_ttl_seconds=5.0)
    return CompositeTokenValidator(
        opaque=opaque_cached, api_key=api_key_cached, toolkit_key=toolkit_cached, jwt=None
    )


@pytest.mark.asyncio
async def test_toolkit_key_routes_to_toolkit_path(
    opaque_resolver: AsyncMock,
    api_key_resolver: AsyncMock,
    toolkit_key_resolver: AsyncMock,
) -> None:
    """A jntc_live_ key routes to the toolkit resolver, not the api-key/opaque path."""
    opaque_cached = CachedTokenValidator(resolver=opaque_resolver, cache_ttl_seconds=5.0)
    api_key_cached = CachedTokenValidator(resolver=api_key_resolver, cache_ttl_seconds=5.0)
    toolkit_cached = CachedTokenValidator(resolver=toolkit_key_resolver, cache_ttl_seconds=5.0)
    triple = CompositeTokenValidator(
        opaque=opaque_cached,
        api_key=api_key_cached,
        toolkit_key=toolkit_cached,
        jwt=None,
    )

    result = await triple.validate("jntc_live_abc123")  # pragma: allowlist secret

    assert result.sub == "tk_toolkit1"
    assert result.actor_type is ActorType.TOOLKIT
    toolkit_key_resolver.resolve_access_token.assert_called_once_with(
        "jntc_live_abc123"  # pragma: allowlist secret
    )
    api_key_resolver.resolve_access_token.assert_not_called()
    opaque_resolver.resolve_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_jak_prefix_routes_to_api_key_path(
    triple: CompositeTokenValidator, api_key_resolver: AsyncMock, opaque_resolver: AsyncMock
) -> None:
    result = await triple.validate("jak_some_secret")
    assert result.sub == "agnt_apikey"
    api_key_resolver.resolve_access_token.assert_called_once_with("jak_some_secret")
    opaque_resolver.resolve_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_sak_prefix_routes_to_api_key_path(
    triple: CompositeTokenValidator, api_key_resolver: AsyncMock, opaque_resolver: AsyncMock
) -> None:
    result = await triple.validate("sak_some_secret")
    assert result.sub == "agnt_apikey"
    api_key_resolver.resolve_access_token.assert_called_once_with("sak_some_secret")
    opaque_resolver.resolve_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_opaque_token_routes_to_opaque_path(
    triple: CompositeTokenValidator, api_key_resolver: AsyncMock, opaque_resolver: AsyncMock
) -> None:
    result = await triple.validate("at_opaque_token_123")
    assert result.sub == "usr_opaque"
    opaque_resolver.resolve_access_token.assert_called_once_with("at_opaque_token_123")
    api_key_resolver.resolve_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_jwt_routes_to_jwt_path() -> None:
    """When a JWT verifier is present, JWT-shaped tokens go to the JWT path."""
    jwt_validator = AsyncMock()
    jwt_validator.validate = AsyncMock(
        return_value=_make_identity(sub="agnt_jwt", actor_type=ActorType.AGENT)
    )
    opaque_resolver = AsyncMock()
    opaque_resolver.resolve_access_token = AsyncMock()
    api_key_resolver = AsyncMock()
    api_key_resolver.resolve_access_token = AsyncMock()

    toolkit_key_resolver = AsyncMock()
    toolkit_key_resolver.resolve_access_token = AsyncMock()

    opaque_cached = CachedTokenValidator(resolver=opaque_resolver, cache_ttl_seconds=5.0)
    api_key_cached = CachedTokenValidator(resolver=api_key_resolver, cache_ttl_seconds=5.0)
    toolkit_cached = CachedTokenValidator(resolver=toolkit_key_resolver, cache_ttl_seconds=5.0)
    triple = CompositeTokenValidator(
        opaque=opaque_cached, api_key=api_key_cached, toolkit_key=toolkit_cached, jwt=jwt_validator
    )

    jwt_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature"  # pragma: allowlist secret
    result = await triple.validate(jwt_token)

    assert result.sub == "agnt_jwt"
    jwt_validator.validate.assert_called_once_with(jwt_token)
    opaque_resolver.resolve_access_token.assert_not_called()
    api_key_resolver.resolve_access_token.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_api_key_raises_typed_error(api_key_resolver: AsyncMock) -> None:
    """An API key not found in the DB raises TokenValidationError (mapped to 401)."""
    api_key_resolver.resolve_access_token = AsyncMock(return_value=None)
    opaque_resolver = AsyncMock()
    opaque_resolver.resolve_access_token = AsyncMock()
    toolkit_key_resolver = AsyncMock()
    toolkit_key_resolver.resolve_access_token = AsyncMock()

    opaque_cached = CachedTokenValidator(resolver=opaque_resolver, cache_ttl_seconds=5.0)
    api_key_cached = CachedTokenValidator(resolver=api_key_resolver, cache_ttl_seconds=5.0)
    toolkit_cached = CachedTokenValidator(resolver=toolkit_key_resolver, cache_ttl_seconds=5.0)
    triple = CompositeTokenValidator(
        opaque=opaque_cached, api_key=api_key_cached, toolkit_key=toolkit_cached, jwt=None
    )

    with pytest.raises(TokenValidationError, match="unknown_token"):
        await triple.validate("jak_bad_key")
