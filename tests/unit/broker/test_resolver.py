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
    name: str = "default",
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
    cred.name = name
    cred.type = type
    cred.api_vendor = api_vendor
    cred.api_name = api_name
    cred.api_version = api_version
    cred.active = active
    cred.provider = provider
    cred.server_variables = server_variables
    cred.created_at = None
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
async def test_resolve_null_version_credential_matches_concrete_version() -> None:
    """A credential with NULL api_version is a wildcard and matches a concrete
    resolved version (regression for #775).

    A credential — especially a no-auth one — isn't tied to a spec version, so
    api_version is NULL ("covers all versions"). The broker resolves the
    operation to a concrete version (e.g. "4.2.3"); the credential must still
    match. Before the fix the Python filter did `c.api_version == api.version`,
    so NULL never matched a concrete version and execute 424'd
    (credential_not_provisioned) despite a valid, bound no-auth credential.
    """
    cred = _make_credential(
        cred_id="cred_noauth",
        type="NO_AUTH",
        api_vendor="country-is",
        api_name="country-is",
        api_version=None,
    )

    session = AsyncMock()
    ctx = _make_ctx_with_control_session(session)
    api = APIReference(vendor="country-is", name="country-is", version="4.2.3")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        resolver = CredentialResolver(ctx)
        result = await resolver.resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_noauth"
    assert result.wire_type == CredentialType.NO_AUTH


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


@pytest.mark.asyncio
async def test_resolve_vendor_scoped_wildcard_covers_concrete_op() -> None:
    """A vendor-scoped credential (NULL name/version) resolves for a concrete op (#775)."""
    cred = _make_credential(api_vendor="stripe", api_name=None, api_version=None)
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:wild"
    cred.token_value_credential = tvc

    ctx = _make_ctx_with_control_session(AsyncMock())
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        result = await CredentialResolver(ctx).resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_abc"
    assert result.encrypted_secret == "enc:wild"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_resolve_empty_string_stored_axis_covers_concrete_op() -> None:
    """A legacy '' name/version credential still resolves (canonicalized at read, #775)."""
    cred = _make_credential(api_vendor="stripe", api_name="", api_version="")
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:legacy"
    cred.token_value_credential = tvc

    ctx = _make_ctx_with_control_session(AsyncMock())
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[cred],
    ):
        result = await CredentialResolver(ctx).resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_abc"


@pytest.mark.asyncio
async def test_resolve_wrong_version_does_not_cover() -> None:
    """A version-pinned credential does not cover a different version."""
    cred = _make_credential(api_vendor="stripe", api_name="payments", api_version="v2")

    ctx = _make_ctx_with_control_session(AsyncMock())
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with (
        patch(
            "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
            new_callable=AsyncMock,
            return_value=[cred],
        ),
        pytest.raises(CredentialNotProvisionedError),
    ):
        await CredentialResolver(ctx).resolve(api=api, caller="caller_1")


@pytest.mark.asyncio
async def test_resolve_pin_wins_over_vendor_wildcard_no_ambiguity() -> None:
    """A vendor wildcard coexisting with a pinned credential → pin wins, no 409 (#775)."""
    wildcard = _make_credential(cred_id="cred_wild", api_name=None, api_version=None)
    pinned = _make_credential(cred_id="cred_pin", api_name="payments", api_version="v1")
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:pin"
    pinned.token_value_credential = tvc

    ctx = _make_ctx_with_control_session(AsyncMock())
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[wildcard, pinned],
    ):
        result = await CredentialResolver(ctx).resolve(api=api, caller="caller_1")

    assert result.credential_id == "cred_pin"


@pytest.mark.asyncio
async def test_resolve_same_specificity_tie_is_ambiguous() -> None:
    """Two credentials at the same specificity remain a genuine 409."""
    a = _make_credential(cred_id="cred_a", api_name="payments", api_version="v1")
    b = _make_credential(cred_id="cred_b", api_name="payments", api_version="v1")

    ctx = _make_ctx_with_control_session(AsyncMock())
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with (
        patch(
            "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
            new_callable=AsyncMock,
            return_value=[a, b],
        ),
        pytest.raises(AmbiguousCredentialError),
    ):
        await CredentialResolver(ctx).resolve(api=api, caller="caller_1")


@pytest.mark.asyncio
async def test_resolve_credential_name_selects_less_specific_covering() -> None:
    """An explicit credential_name may pick a covering-but-less-specific credential (N1).

    A vendor-wide wildcard and a name/version pin both cover the API. Naming the
    wildcard must resolve *it* — the name search runs over all covering
    credentials, before specificity narrowing — instead of raising
    CredentialNameNotFoundError because the pin out-ranked it.
    """
    wildcard = _make_credential(
        cred_id="cred_wild", name="shared-account", api_name=None, api_version=None
    )
    tvc = MagicMock()
    tvc.encrypted_token_value = "enc:wild"  # pragma: allowlist secret
    wildcard.token_value_credential = tvc
    pinned = _make_credential(
        cred_id="cred_pin", name="pinned-account", api_name="payments", api_version="v1"
    )

    ctx = _make_ctx_with_control_session(AsyncMock())
    api = APIReference(vendor="stripe", name="payments", version="v1")

    with patch(
        "jentic_one.broker.services.credentials.resolver.CredentialRepository.list_by_vendor",
        new_callable=AsyncMock,
        return_value=[wildcard, pinned],
    ):
        result = await CredentialResolver(ctx).resolve(
            api=api, caller="caller_1", credential_name="shared-account"
        )

    assert result.credential_id == "cred_wild"
