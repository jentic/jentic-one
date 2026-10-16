"""Unit tests for the TokenRefresher service."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from jentic_one.broker.services.credentials.errors import (
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.control.services.credentials.providers.base import ProviderError
from jentic_one.control.services.credentials.providers.direct_oauth2 import InvalidGrantError
from jentic_one.control.services.credentials.schemas.provision import RefreshResult
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType


def _make_resolved(
    *,
    encrypted_access_token: str | None = "enc:at",
    encrypted_refresh_token: str | None = "enc:rt",
    token_expires_at: datetime | None = None,
    provider: str = "direct_oauth2",
    provider_account_ref: str | None = None,
) -> ResolvedCredential:
    return ResolvedCredential(
        credential_id="cred_test",
        name="test",
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
        provider=provider,
        encrypted_access_token=encrypted_access_token,
        encrypted_refresh_token=encrypted_refresh_token,
        token_expires_at=token_expires_at,
        provider_account_ref=provider_account_ref,
    )


def _make_ctx(
    *,
    decrypt_map: dict[str, str] | None = None,
    encrypt_fn: object | None = None,
) -> MagicMock:
    ctx = MagicMock()
    if decrypt_map:
        ctx.encryption.decrypt.side_effect = lambda blob: decrypt_map[blob]
    else:
        ctx.encryption.decrypt.side_effect = lambda blob: f"decrypted:{blob}"
    if encrypt_fn:
        ctx.encryption.encrypt = encrypt_fn
    else:
        ctx.encryption.encrypt.side_effect = lambda val: f"enc:{val}"

    provider_mock = MagicMock()
    provider_mock._expiry_skew_seconds = 60
    ctx.providers.get.return_value = provider_mock

    mock_backend = MagicMock()
    mock_backend.dialect_name = "sqlite"
    ctx.control_db.backend = mock_backend

    return ctx


def _setup_transaction(ctx: MagicMock, row: object | None = None) -> None:
    @asynccontextmanager
    async def _txn(retries: int = 1) -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock()
        yield session

    ctx.control_db.transaction = _txn


def _row_like(resolved: ResolvedCredential, *, revoked: bool = False) -> MagicMock:
    """The token row as the snapshot saw it (nothing rewrote it since)."""
    row = MagicMock()
    row.encrypted_access_token = resolved.encrypted_access_token
    row.encrypted_refresh_token = resolved.encrypted_refresh_token
    row.expires_at = resolved.token_expires_at
    row.revoked_at = datetime.now(UTC) if revoked else None
    return row


@pytest.mark.asyncio
async def test_valid_token_returns_immediately() -> None:
    resolved = _make_resolved(
        encrypted_access_token="enc:valid",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    ctx = _make_ctx(decrypt_map={"enc:valid": "my-access-token"})

    refresher = TokenRefresher(ctx)
    token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "my-access-token"


@pytest.mark.asyncio
async def test_null_expiry_with_token_is_valid() -> None:
    resolved = _make_resolved(
        encrypted_access_token="enc:noexp",
        token_expires_at=None,
    )
    ctx = _make_ctx(decrypt_map={"enc:noexp": "token-no-expiry"})

    refresher = TokenRefresher(ctx)
    token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "token-no-expiry"


@pytest.mark.asyncio
async def test_expired_token_triggers_refresh() -> None:
    resolved = _make_resolved(
        encrypted_access_token="enc:expired",
        token_expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ctx = _make_ctx(decrypt_map={"enc:expired": "old"})
    _setup_transaction(ctx)

    refresh_result = RefreshResult(
        access_token="fresh-access-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        refresh_token="new-refresh-token",
        scope=None,
    )
    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    provider_mock.refresh = AsyncMock(return_value=refresh_result)
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=_row_like(resolved))
        mock_repo.update_tokens = AsyncMock(return_value=MagicMock())

        refresher = TokenRefresher(ctx)
        token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "fresh-access-token"


@pytest.mark.asyncio
async def test_no_access_token_triggers_refresh() -> None:
    """A managed provider holds no local token row and mints from its
    account reference."""
    resolved = _make_resolved(
        encrypted_access_token=None,
        encrypted_refresh_token=None,
        token_expires_at=None,
        provider_account_ref="acct_managed",
    )
    ctx = _make_ctx()
    _setup_transaction(ctx)

    refresh_result = RefreshResult(
        access_token="minted-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    provider_mock.refresh = AsyncMock(return_value=refresh_result)
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=None)
        mock_repo.update_tokens = AsyncMock(return_value=None)

        refresher = TokenRefresher(ctx)
        token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "minted-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True], ids=["row-missing", "row-revoked"])
async def test_missing_grant_under_lock_needs_reconnect(revoked: bool) -> None:
    """A token row gone (or revoked) by the time the lock is held means the
    grant was dropped: the refresh fails as needs-reconnect and the
    snapshot's refresh token is never handed to the provider."""
    resolved = _make_resolved(
        encrypted_access_token="enc:expired",
        encrypted_refresh_token="enc:old-rt",
        token_expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ctx = _make_ctx()
    _setup_transaction(ctx)
    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(
            return_value=_row_like(resolved, revoked=True) if revoked else None
        )

        with pytest.raises(RefreshInvalidGrantError):
            await TokenRefresher(ctx).ensure_fresh(resolved=resolved, caller="test")

    provider_mock.refresh.assert_not_called()
    ctx.encryption.decrypt.assert_not_called()


@pytest.mark.asyncio
async def test_within_skew_triggers_refresh() -> None:
    resolved = _make_resolved(
        encrypted_access_token="enc:nearly",
        token_expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    ctx = _make_ctx(decrypt_map={"enc:nearly": "almost-expired"})
    _setup_transaction(ctx)

    refresh_result = RefreshResult(
        access_token="refreshed",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    provider_mock.refresh = AsyncMock(return_value=refresh_result)
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=_row_like(resolved))
        mock_repo.update_tokens = AsyncMock(return_value=MagicMock())

        refresher = TokenRefresher(ctx)
        token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "refreshed"


@pytest.mark.asyncio
async def test_invalid_grant_raises_refresh_invalid_grant() -> None:
    resolved = _make_resolved(
        encrypted_access_token=None,
        token_expires_at=None,
    )
    ctx = _make_ctx()
    _setup_transaction(ctx)

    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    provider_mock.refresh = AsyncMock(side_effect=InvalidGrantError("revoked"))
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=_row_like(resolved))

        refresher = TokenRefresher(ctx)
        with pytest.raises(RefreshInvalidGrantError):
            await refresher.ensure_fresh(resolved=resolved, caller="test")


@pytest.mark.asyncio
async def test_provider_error_raises_refresh_transient() -> None:
    resolved = _make_resolved(
        encrypted_access_token=None,
        token_expires_at=None,
    )
    ctx = _make_ctx()
    _setup_transaction(ctx)

    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    provider_mock.refresh = AsyncMock(side_effect=ProviderError("timeout"))
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=_row_like(resolved))

        refresher = TokenRefresher(ctx)
        with pytest.raises(RefreshTransientError):
            await refresher.ensure_fresh(resolved=resolved, caller="test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised",
    [
        httpx.ConnectError("connect failed near SECRET_REFRESH_VALUE"),
        ProviderError("idp said: refresh_token=SECRET_REFRESH_VALUE"),
    ],
)
async def test_refresh_transient_detail_omits_exception_message(raised: Exception) -> None:
    """The exception text can quote request material, so the caller-facing
    message carries only its class — and the raw error is not chained."""
    resolved = _make_resolved(encrypted_access_token=None, token_expires_at=None)
    ctx = _make_ctx()
    _setup_transaction(ctx)

    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    provider_mock.refresh = AsyncMock(side_effect=raised)
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=_row_like(resolved))

        refresher = TokenRefresher(ctx)
        with pytest.raises(RefreshTransientError) as excinfo:
            await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert "SECRET_REFRESH_VALUE" not in str(excinfo.value)
    assert f"({type(raised).__name__})" in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_double_check_skips_refresh_when_another_worker_refreshed() -> None:
    resolved = _make_resolved(
        encrypted_access_token="enc:stale",
        token_expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ctx = _make_ctx(decrypt_map={"enc:stale": "old", "enc:fresh-by-other": "fresh-token"})
    _setup_transaction(ctx)

    already_refreshed_row = MagicMock()
    already_refreshed_row.encrypted_access_token = "enc:fresh-by-other"
    already_refreshed_row.expires_at = datetime.now(UTC) + timedelta(hours=1)
    already_refreshed_row.revoked_at = None

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=already_refreshed_row)

        refresher = TokenRefresher(ctx)
        token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "fresh-token"


@pytest.mark.asyncio
async def test_rotated_refresh_token_persisted_only_when_returned() -> None:
    resolved = _make_resolved(
        encrypted_access_token="enc:expired",
        token_expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    ctx = _make_ctx(decrypt_map={"enc:expired": "old"})
    _setup_transaction(ctx)

    refresh_result = RefreshResult(
        access_token="new-access",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        refresh_token=None,
        scope=None,
    )
    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    provider_mock.refresh = AsyncMock(return_value=refresh_result)
    ctx.providers.get.return_value = provider_mock

    with patch("jentic_one.broker.services.credentials.refresh.OAuthTokenRepository") as mock_repo:
        mock_repo.get_by_credential = AsyncMock(return_value=_row_like(resolved))
        mock_repo.update_tokens = AsyncMock(return_value=MagicMock())

        refresher = TokenRefresher(ctx)
        await refresher.ensure_fresh(resolved=resolved, caller="test")

        update_call = mock_repo.update_tokens.call_args
        assert update_call.kwargs["encrypted_refresh_token"] is None
