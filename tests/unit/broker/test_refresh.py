"""Unit tests for the TokenRefresher service and OAuth refresh taxonomy."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog

from jentic_one.broker.core.errors import CredentialNeedsReconnectError
from jentic_one.broker.services.credentials.errors import (
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.refresh import (
    TokenRefresher,
    _true_expiry,
    parse_oauth_expires_in,
    true_oauth_expiry,
)
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.control.core.schema import ControlBase, Credential, OAuthToken
from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.providers.base import ProviderError
from jentic_one.control.services.credentials.providers.direct_oauth2 import InvalidGrantError
from jentic_one.control.services.credentials.providers.oauth2 import (
    MAX_OAUTH_EXPIRES_IN_SECONDS,
    OAuth2Provider,
    TokenExchangeError,
    classify_oauth_token_error,
    extract_oauth_error_code,
    validate_oauth_token_payload,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectChallenge,
    ConnectRequest,
    ConnectState,
)
from jentic_one.control.services.credentials.schemas.provision import (
    APIReference,
    OAuthTokenView,
    ProvisionResult,
    RefreshResult,
)
from jentic_one.control.services.integrations import device_authorization as df
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig, DatabaseConfig, EncryptionConfig
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import EncryptionService
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.http.idp_client import idp_request
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.redaction import REDACTED, redact_event, redact_value


@contextmanager
def idp_transport(
    *,
    post: object = None,
    get: object = None,
) -> Iterator[list[httpx.Request]]:
    """Fake the IdP at the transport seam; yields the requests the code under test sent.

    ``post`` / ``get`` script the reply per HTTP method: a response, an exception to
    raise, or a list of either consumed one per request.
    """
    requests: list[httpx.Request] = []
    scripts: dict[str, object] = {"POST": post, "GET": get}

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        step = scripts[request.method]
        if isinstance(step, list):
            step = step.pop(0)
        if isinstance(step, BaseException):
            raise step
        assert isinstance(step, httpx.Response)
        return httpx.Response(step.status_code, headers=step.headers, content=step.content)

    with patch(
        "jentic_one.shared.http.idp_client.build_always_pinned_transport",
        return_value=httpx.MockTransport(respond),
    ):
        yield requests


_KEY_B64 = base64.b64encode(b"\x11" * 32).decode()
_OTHER_KEY_B64 = base64.b64encode(b"\x22" * 32).decode()
_CRED_ID = "cred_test"
_URL_SECRET = "SECRET_REFRESH_VALUE"  # pragma: allowlist secret
_DUMMY_CLIENT_SECRET = "dummy-live-client-secret"  # pragma: allowlist secret


class _DummyOAuth2Provider(OAuth2Provider):
    """Minimal concrete OAuth2Provider for testing shared ``_post_token`` behavior."""

    name = "dummy_oauth2"

    @property
    def managed(self) -> bool:
        return True

    async def begin_connect(
        self, ctx: Context, *, api: APIReference, request: ConnectRequest
    ) -> ConnectChallenge:
        raise NotImplementedError

    async def complete_connect(
        self, ctx: Context, *, state: ConnectState, callback: ConnectCallback
    ) -> ProvisionResult:
        raise NotImplementedError

    async def refresh(self, ctx: Context, *, token: OAuthTokenView) -> RefreshResult:
        refresh_token = await token.decrypt()
        data = await self._post_token(
            "https://idp.example.com/oauth/token",
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            egress=None,
        )
        return RefreshResult(
            access_token=data["access_token"],
            expires_at=true_oauth_expiry(data),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
        )


def _encryption_service(key_b64: str = _KEY_B64, key_id: str = "k1") -> EncryptionService:
    cfg = EncryptionConfig.model_validate(
        {"active_id": key_id, "entries": [{"id": key_id, "material": key_b64}]}
    )
    return EncryptionService(cfg)


@pytest.fixture()
async def sqlite_ctx(tmp_path: Path) -> AsyncGenerator[MagicMock, None]:
    """Provide a Context stand-in backed by a real in-memory SQLite control database."""
    control_db = DatabaseSession(DatabaseConfig(backend="sqlite", path=":memory:"))
    admin_db = DatabaseSession(DatabaseConfig(backend="sqlite", path=":memory:"))
    await control_db.connect()
    await admin_db.connect()
    async with control_db.engine.begin() as conn:
        await conn.run_sync(ControlBase.metadata.create_all)

    ctx = MagicMock(spec=Context)
    ctx.control_db = control_db
    ctx.admin_db = admin_db
    ctx.encryption = _encryption_service()
    ctx.config = AppConfig.model_validate(
        {
            "databases": {
                "registry": {"backend": "sqlite", "path": ":memory:"},
                "admin": {"backend": "sqlite", "path": ":memory:"},
                "control": {"backend": "sqlite", "path": ":memory:"},
            }
        }
    )

    provider_mock = AsyncMock()
    provider_mock._expiry_skew_seconds = 60
    ctx.providers = MagicMock()
    ctx.providers.get.return_value = provider_mock
    ctx.providers.mints_from_account_ref.return_value = False

    try:
        yield ctx
    finally:
        await admin_db.close()
        await control_db.close()


async def _seed_credential_and_token(
    ctx: MagicMock,
    *,
    credential_id: str = _CRED_ID,
    access_token: str | None = "old-access",
    refresh_token: str | None = "old-refresh",
    raw_encrypted_refresh_token: str | None = None,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    provider: str = "direct_oauth2",
    provider_account_ref: str | None = None,
    create_token_row: bool = True,
) -> ResolvedCredential:
    encrypted_access = ctx.encryption.encrypt(access_token) if access_token is not None else None
    if raw_encrypted_refresh_token is not None:
        encrypted_refresh: str | None = raw_encrypted_refresh_token
    elif refresh_token is not None:
        encrypted_refresh = ctx.encryption.encrypt(refresh_token)
    else:
        encrypted_refresh = None

    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=credential_id,
                type=StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
                name="test",
                api_vendor="acme",
                api_name="widgets",
                api_version="v1",
                provider=provider,
                provider_account_ref=provider_account_ref,
            )
        )
        await session.flush()
        if create_token_row:
            stored_access = encrypted_access or ctx.encryption.encrypt("placeholder")
            session.add(
                OAuthToken(
                    credential_id=credential_id,
                    encrypted_access_token=stored_access,
                    encrypted_refresh_token=encrypted_refresh,
                    expires_at=expires_at,
                    revoked_at=revoked_at,
                )
            )

    return ResolvedCredential(
        credential_id=credential_id,
        name="test",
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
        provider=provider,
        encrypted_access_token=encrypted_access,
        encrypted_refresh_token=encrypted_refresh,
        token_expires_at=expires_at,
        provider_account_ref=provider_account_ref,
    )


async def _get_token_row(ctx: MagicMock, credential_id: str = _CRED_ID) -> OAuthToken | None:
    async with ctx.control_db.session() as session:
        return await OAuthTokenRepository.get_by_credential(session, credential_id)


@pytest.mark.asyncio
async def test_valid_token_returns_immediately(sqlite_ctx: MagicMock) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="my-access-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    refresher = TokenRefresher(sqlite_ctx)
    token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "my-access-token"
    sqlite_ctx.providers.get.return_value.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_null_expiry_with_token_is_valid(sqlite_ctx: MagicMock) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="token-no-expiry",
        expires_at=None,
    )

    refresher = TokenRefresher(sqlite_ctx)
    token = await refresher.ensure_fresh(resolved=resolved, caller="test")

    assert token == "token-no-expiry"
    sqlite_ctx.providers.get.return_value.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_expired_token_triggers_refresh_and_persists(sqlite_ctx: MagicMock) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="old",
        refresh_token="rt-initial",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    new_expiry = datetime.now(UTC) + timedelta(hours=1)
    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(
        return_value=RefreshResult(
            access_token="fresh-access-token",
            expires_at=new_expiry,
            refresh_token="new-refresh-token",
            scope="read:all",
        )
    )

    token = await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    assert token == "fresh-access-token"
    row = await _get_token_row(sqlite_ctx)
    assert row is not None
    assert sqlite_ctx.encryption.decrypt(row.encrypted_access_token) == "fresh-access-token"
    assert row.encrypted_refresh_token is not None
    assert sqlite_ctx.encryption.decrypt(row.encrypted_refresh_token) == "new-refresh-token"
    assert row.scope == "read:all"


@pytest.mark.asyncio
async def test_no_access_token_account_ref_mints_without_error_log(
    sqlite_ctx: MagicMock,
) -> None:
    """An account-reference provider with no local OAuthToken row mints cleanly
    without attempting a local row update or logging refresh_token_row_missing."""
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token=None,
        refresh_token=None,
        expires_at=None,
        provider_account_ref="acct_managed",
        create_token_row=False,
    )
    sqlite_ctx.providers.mints_from_account_ref.return_value = True
    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(
        return_value=RefreshResult(
            access_token="minted-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with structlog.testing.capture_logs() as captured:
        token = await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    assert token == "minted-token"
    sqlite_ctx.providers.mints_from_account_ref.assert_called_with("direct_oauth2")
    assert await _get_token_row(sqlite_ctx) is None
    error_events = [e for e in captured if e.get("log_level") == "error"]
    assert error_events == []


@pytest.mark.asyncio
async def test_local_grant_row_disappearing_mid_refresh_logs_error_and_fails_closed(
    sqlite_ctx: MagicMock,
) -> None:
    """If a local-grant credential's token row disappears before persistence,
    refresh logs refresh_token_row_missing and raises RefreshInvalidGrantError."""
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="expired",
        refresh_token="rt-1",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    real_factory = sqlite_ctx.control_db.session_factory
    opened_sessions: list[Any] = []

    def _tracking_factory() -> Any:
        sess = real_factory()
        opened_sessions.append(sess)
        return sess

    sqlite_ctx.control_db._session_factory = _tracking_factory

    async def _refresh_dropping_row(ctx: Context, *, token: OAuthTokenView) -> RefreshResult:
        # Delete the token row on the active SQLite session during provider refresh.
        await OAuthTokenRepository.delete_by_credential(opened_sessions[-1], token.credential_id)
        return RefreshResult(
            access_token="minted-after-delete",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(side_effect=_refresh_dropping_row)

    with (
        structlog.testing.capture_logs() as captured,
        pytest.raises(RefreshInvalidGrantError),
    ):
        await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    assert any(
        e.get("event") == "refresh_token_row_missing" and e.get("log_level") == "error"
        for e in captured
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True], ids=["row-missing", "row-revoked"])
@pytest.mark.parametrize("provider_account_ref", [None, "alice"], ids=["no-ref", "echo-ref"])
async def test_missing_grant_under_lock_needs_reconnect(
    sqlite_ctx: MagicMock,
    revoked: bool,
    provider_account_ref: str | None,
) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="expired",
        refresh_token="old-rt",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
        revoked_at=datetime.now(UTC) if revoked else None,
        provider_account_ref=provider_account_ref,
        create_token_row=revoked,
    )

    with pytest.raises(RefreshInvalidGrantError):
        await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    sqlite_ctx.providers.get.return_value.refresh.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refresh_token", "raw_encrypted_refresh_token", "expect_provider_called"),
    [
        (None, None, False),
        (None, "", False),
        (None, "   ", False),
        ("", None, True),
        ("   ", None, True),
        (None, "not-valid-ciphertext", True),
        (None, _encryption_service(_OTHER_KEY_B64, key_id="unknown_key").encrypt("rt"), True),
    ],
    ids=[
        "null-refresh-token",
        "empty-ciphertext",
        "whitespace-ciphertext",
        "decrypts-to-empty",
        "decrypts-to-whitespace",
        "corrupted-ciphertext",
        "unknown-key-ciphertext",
    ],
)
async def test_missing_or_undecryptable_refresh_token_maps_to_reconnect(
    sqlite_ctx: MagicMock,
    refresh_token: str | None,
    raw_encrypted_refresh_token: str | None,
    expect_provider_called: bool,
) -> None:
    """An expired access token with a NULL, empty, or undecryptable refresh token
    raises RefreshInvalidGrantError (and CredentialNeedsReconnectError via
    CredentialService) without leaking secret material."""
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="expired-access",
        refresh_token=refresh_token,
        raw_encrypted_refresh_token=raw_encrypted_refresh_token,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    async def _decrypting_refresh(ctx: Context, *, token: OAuthTokenView) -> RefreshResult:
        await token.decrypt()
        raise AssertionError("token.decrypt() should have raised InvalidGrantError")

    provider_mock = sqlite_ctx.providers.get.return_value
    provider_mock.refresh = AsyncMock(side_effect=_decrypting_refresh)

    with pytest.raises(RefreshInvalidGrantError) as refresh_exc:
        await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    assert "not-valid-ciphertext" not in str(refresh_exc.value)
    assert "unknown_key" not in str(refresh_exc.value)
    if not expect_provider_called:
        provider_mock.refresh.assert_not_called()
    else:
        assert provider_mock.refresh.await_count == 1

    # Also verify through the orchestrator seam that the caller gets
    # CredentialNeedsReconnectError rather than a transient 502.
    service = CredentialService(sqlite_ctx)
    identity = Identity(sub="agent_1", actor_type=ActorType.AGENT)
    with pytest.raises(CredentialNeedsReconnectError) as orch_exc:
        await service.inject(
            api_vendor="acme",
            api_name="widgets",
            api_version="v1",
            identity=identity,
            preresolved=resolved,
        )
    assert orch_exc.value.type == "credential_needs_reconnect"


@pytest.mark.asyncio
async def test_within_skew_triggers_refresh(sqlite_ctx: MagicMock) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="almost-expired",
        refresh_token="rt-skew",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(
        return_value=RefreshResult(
            access_token="refreshed",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    token = await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    assert token == "refreshed"


@pytest.mark.asyncio
async def test_invalid_grant_raises_refresh_invalid_grant(sqlite_ctx: MagicMock) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="expired",
        refresh_token="rt-revoked",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(
        side_effect=InvalidGrantError("revoked")
    )

    with pytest.raises(RefreshInvalidGrantError):
        await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised",
    [
        ProviderError("idp said: refresh_token=SECRET_REFRESH_VALUE"),
        httpx.ConnectError(f"connect failed near https://user:{_URL_SECRET}@idp.example/t"),
        httpx.ReadError("read failed: access_token=SECRET_REFRESH_VALUE"),
        httpx.WriteError("write failed: client_secret=SECRET_REFRESH_VALUE"),
        httpx.RemoteProtocolError("protocol error: Bearer SECRET_REFRESH_VALUE"),
        httpx.PoolTimeout("pool timeout"),
        httpx.DecodingError("decode error"),
        httpx.InvalidURL(f"invalid url https://user:{_URL_SECRET}@idp.example/t"),
    ],
)
async def test_transport_and_provider_errors_map_to_transient_and_redact_secrets(
    sqlite_ctx: MagicMock, raised: Exception
) -> None:
    """All expected HTTPX transport/protocol/URL errors and ProviderErrors map
    to RefreshTransientError with only the exception class name exposed and all
    secrets redacted in logs."""
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="expired",
        refresh_token="rt-ok",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(side_effect=raised)

    with (
        structlog.testing.capture_logs() as captured,
        pytest.raises(RefreshTransientError) as excinfo,
    ):
        await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    assert "SECRET_REFRESH_VALUE" not in str(excinfo.value)
    assert f"({type(raised).__name__})" in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    for entry in captured:
        assert "SECRET_REFRESH_VALUE" not in str(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_access_token", ["", "   "])
async def test_empty_access_token_from_provider_maps_to_refresh_transient(
    sqlite_ctx: MagicMock, bad_access_token: str
) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="expired",
        refresh_token="rt-ok",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(
        return_value=RefreshResult(
            access_token=bad_access_token,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(RefreshTransientError):
        await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")


@pytest.mark.asyncio
async def test_double_check_skips_refresh_when_another_worker_refreshed(
    sqlite_ctx: MagicMock,
) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="stale",
        refresh_token="rt-stale",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    # Another worker updates the token row in the database after our snapshot.
    async with sqlite_ctx.control_db.transaction() as session:
        await OAuthTokenRepository.update_tokens(
            session,
            _CRED_ID,
            encrypted_access_token=sqlite_ctx.encryption.encrypt("fresh-token"),
            encrypted_refresh_token=sqlite_ctx.encryption.encrypt("rt-fresh"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    token = await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    assert token == "fresh-token"
    sqlite_ctx.providers.get.return_value.refresh.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_waiters_share_single_refresh_on_sqlite(
    sqlite_ctx: MagicMock,
) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="stale",
        refresh_token="rt-0",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    calls = 0

    async def _slow_refresh(ctx: Context, *, token: OAuthTokenView) -> RefreshResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return RefreshResult(
            access_token=f"fresh-{calls}",
            refresh_token=f"rt-{calls}",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(side_effect=_slow_refresh)

    results = await asyncio.gather(
        *(TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="c") for _ in range(4))
    )

    assert results == ["fresh-1", "fresh-1", "fresh-1", "fresh-1"]
    assert calls == 1


@pytest.mark.asyncio
async def test_rotated_refresh_token_persisted_only_when_returned(
    sqlite_ctx: MagicMock,
) -> None:
    resolved = await _seed_credential_and_token(
        sqlite_ctx,
        access_token="expired",
        refresh_token="preserved-refresh-token",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    sqlite_ctx.providers.get.return_value.refresh = AsyncMock(
        return_value=RefreshResult(
            access_token="new-access",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            refresh_token=None,
            scope=None,
        )
    )

    await TokenRefresher(sqlite_ctx).ensure_fresh(resolved=resolved, caller="test")

    row = await _get_token_row(sqlite_ctx)
    assert row is not None
    assert sqlite_ctx.encryption.decrypt(row.encrypted_access_token) == "new-access"
    assert row.encrypted_refresh_token is not None
    assert sqlite_ctx.encryption.decrypt(row.encrypted_refresh_token) == "preserved-refresh-token"


@pytest.mark.parametrize(
    ("status_code", "content_type", "body", "expected_exc_type"),
    [
        (
            400,
            "application/json",
            json.dumps({"error": "invalid_grant", "error_description": "Token expired"}),
            InvalidGrantError,
        ),
        (
            401,
            "application/x-www-form-urlencoded",
            "error=invalid_grant&error_description=revoked",
            InvalidGrantError,
        ),
        (
            400,
            "application/json",
            json.dumps(
                {
                    "error": "temporarily_unavailable",
                    "error_description": "upstream mentioned invalid_grant in prose",
                }
            ),
            TokenExchangeError,
        ),
        (
            400,
            "text/html",
            "<html><body>Gateway diagnostic mentioning invalid_grant</body></html>",
            TokenExchangeError,
        ),
        (
            429,
            "application/json",
            json.dumps({"error": "invalid_grant", "error_description": "rate limited"}),
            TokenExchangeError,
        ),
        (
            500,
            "application/json",
            json.dumps({"error": "invalid_grant"}),
            TokenExchangeError,
        ),
        (
            503,
            "text/html",
            "<html><body>503 Service Unavailable: invalid_grant</body></html>",
            TokenExchangeError,
        ),
        (
            400,
            "application/json",
            "{malformed json with invalid_grant",
            TokenExchangeError,
        ),
    ],
    ids=[
        "json-invalid-grant",
        "form-invalid-grant",
        "invalid-grant-only-in-description",
        "html-with-invalid-grant-substring",
        "rate-limit-429-precedence",
        "server-500-precedence",
        "gateway-503-html-precedence",
        "malformed-json-with-substring",
    ],
)
@pytest.mark.asyncio
async def test_post_token_error_classification_and_precedence(
    status_code: int,
    content_type: str,
    body: str,
    expected_exc_type: type[ProviderError],
) -> None:
    """Only an authoritative 4xx response whose structured ``error`` field is
    ``invalid_grant`` raises InvalidGrantError; 429, 5xx, HTML, and substring
    mentions in ``error_description`` raise TokenExchangeError."""
    provider = _DummyOAuth2Provider()
    response = httpx.Response(
        status_code,
        text=body,
        headers={"content-type": content_type},
    )

    with (
        patch(
            "jentic_one.shared.http.idp_client.validate_upstream_url_async",
            new=AsyncMock(return_value="https://idp.example.com/oauth/token"),
        ),
        idp_transport(post=response),
        pytest.raises(expected_exc_type) as exc_info,
    ):
        await provider._post_token(
            "https://idp.example.com/oauth/token",
            {"grant_type": "refresh_token", "refresh_token": "rt"},
            egress=None,
        )

    if type(exc_info.value) is TokenExchangeError:
        assert exc_info.value.status == status_code
        # Raw HTML or arbitrary JSON descriptions must not be echoed in body/str.
        assert "<html>" not in exc_info.value.body
        assert "prose" not in exc_info.value.body


@pytest.mark.parametrize(
    "transport_exc",
    [
        httpx.ConnectError("connect failed"),
        httpx.ReadError("read failed"),
        httpx.WriteError("write failed"),
        httpx.RemoteProtocolError("bad http framing"),
        httpx.PoolTimeout("pool exhausted"),
        httpx.InvalidURL("bad url"),
    ],
)
@pytest.mark.asyncio
async def test_post_token_normalizes_httpx_transport_errors(
    transport_exc: Exception,
) -> None:
    provider = _DummyOAuth2Provider()
    with (
        patch(
            "jentic_one.shared.http.idp_client.validate_upstream_url_async",
            new=AsyncMock(return_value="https://idp.example.com/oauth/token"),
        ),
        idp_transport(post=transport_exc),
        pytest.raises(TokenExchangeError) as exc_info,
    ):
        await provider._post_token(
            "https://idp.example.com/oauth/token",
            {"grant_type": "refresh_token", "refresh_token": "rt"},
            egress=None,
        )

    assert exc_info.value.status == 0
    assert exc_info.value.body == type(transport_exc).__name__


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-dict",
        ["list", "payload"],
        {},
        {"access_token": ""},
        {"access_token": "   "},
        {"access_token": 12345},
        {"access_token": "at_ok", "expires_in": None},
        {"access_token": "at_ok", "expires_in": "not-a-number"},
        {"access_token": "at_ok", "expires_in": 0},
        {"access_token": "at_ok", "expires_in": -60},
        {"access_token": "at_ok", "expires_in": True},
        {"access_token": "at_ok", "expires_in": False},
        {"access_token": "at_ok", "expires_in": 3600.5},
        {"access_token": "at_ok", "expires_in": "1" + "0" * 100},
        {"access_token": "at_ok", "refresh_token": 123},
        {"access_token": "at_ok", "scope": ["read"]},
    ],
)
def test_validate_oauth_token_payload_rejects_malformed_200_responses(
    payload: object,
) -> None:
    with pytest.raises(TokenExchangeError):
        validate_oauth_token_payload(payload, status_code=200)


@pytest.mark.parametrize(
    ("raw_expires_in", "expected"),
    [
        (3600, 3600),
        ("3600", 3600),
        (" 7200 ", 7200),
        (3600.0, 3600),
        (1, 1),
        (MAX_OAUTH_EXPIRES_IN_SECONDS, MAX_OAUTH_EXPIRES_IN_SECONDS),
    ],
)
def test_parse_oauth_expires_in_accepts_valid_inputs(
    raw_expires_in: object, expected: int | None
) -> None:
    assert parse_oauth_expires_in(raw_expires_in) == expected


@pytest.mark.parametrize(
    "invalid_expires_in",
    [
        None,
        0,
        "0",
        -1,
        "-3600",
        True,
        False,
        3600.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        "",
        "   ",
        "not-a-number",
        "1" + "0" * 100,
        MAX_OAUTH_EXPIRES_IN_SECONDS + 1,
        [],
        {},
    ],
)
def test_parse_oauth_expires_in_rejects_malformed_or_non_positive_inputs(
    invalid_expires_in: object,
) -> None:
    with pytest.raises(ProviderError):
        parse_oauth_expires_in(invalid_expires_in)
    with pytest.raises(ProviderError):
        _true_expiry({"expires_in": invalid_expires_in})


def test_true_expiry_omitted_returns_none() -> None:
    assert _true_expiry({}) is None


@pytest.mark.parametrize(
    "body",
    [
        '{"access_token":"dummy-live-access-token","error":"unexpected"}',
        '{"refresh_token":"dummy-live-refresh-token","error":"unexpected"}',
        f'{{"client_secret":"{_DUMMY_CLIENT_SECRET}","error":"unexpected"}}',
        '{"code":"dummy-live-auth-code","error":"unexpected"}',
        '{"device_code":"dummy-live-device-code","error":"unexpected"}',
        '{"access_token":"dummy-live-truncated-token',
        "refresh_token=dummy-live-refresh-token&error=unexpected",
        "access_token=dummy-live-access-token&token_type=bearer",
        "client_secret=dummy-live-client-secret&grant_type=authorization_code",
        "code=dummy-live-auth-code&state=xyz",
        "device_code=dummy-live-device-code&error=slow_down",
    ],
)
def test_redact_event_scrubs_embedded_json_and_form_secrets(body: str) -> None:
    """Embedded JSON and form-urlencoded OAuth secrets in ordinary string fields
    such as ``body_snippet`` are redacted by ``redact_event``."""
    event = redact_event(None, "info", {"body_snippet": body})
    rendered = str(event["body_snippet"])
    assert "dummy-live-" not in rendered
    assert REDACTED in rendered
    # Idempotence check
    assert redact_value(rendered) == rendered


@pytest.mark.asyncio
async def test_poll_device_authorization_rejects_zero_or_malformed_expires_in() -> None:
    for bad_expires in (None, 0, -5, "not-a-number", True, "1" + "0" * 100):
        response = httpx.Response(
            200,
            json={"access_token": "at_live", "expires_in": bad_expires},
        )
        with (
            patch(
                "jentic_one.shared.http.idp_client.validate_upstream_url_async",
                new=AsyncMock(return_value="https://idp.example.com/device/token"),
            ),
            idp_transport(post=response),
            pytest.raises(df.DeviceAuthorizationUpstreamError, match="invalid expires_in"),
        ):
            await df.poll_device_authorization(
                token_endpoint="https://idp.example.com/device/token",
                client_id="client_1",
                device_code="dev_1",
                egress=None,
            )


def test_extract_and_classify_oauth_error_helpers() -> None:
    assert extract_oauth_error_code('{"error": "invalid_grant"}', "application/json") == (
        "invalid_grant"
    )
    assert extract_oauth_error_code("error=invalid_grant", "application/x-www-form-urlencoded") == (
        "invalid_grant"
    )
    assert extract_oauth_error_code('{"error": "not a slug!!"}', "application/json") is None
    err = classify_oauth_token_error(503, '{"error": "invalid_grant"}', "application/json")
    assert isinstance(err, TokenExchangeError)


class _DripStream(httpx.AsyncByteStream):
    """A body that never finishes: one byte every 50 ms, forever."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(0.05)
            yield b" "


def _drip_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, stream=_DripStream()))


@pytest.mark.asyncio()
async def test_post_token_drip_feed_is_transient_not_hang() -> None:
    """A token endpoint that trickles bytes forever hits the total deadline."""
    provider = _DummyOAuth2Provider()
    with (
        patch(
            "jentic_one.control.services.credentials.providers.oauth2.idp_request",
            partial(idp_request, deadline_s=0.5),
        ),
        patch(
            "jentic_one.shared.http.idp_client.build_always_pinned_transport",
            return_value=_drip_transport(),
        ),
        patch(
            "jentic_one.shared.http.idp_client.validate_upstream_url_async",
            new=AsyncMock(return_value="https://idp.example.com/oauth/token"),
        ),
    ):
        start = time.monotonic()
        with pytest.raises(TokenExchangeError) as exc_info:
            await provider._post_token(
                "https://idp.example.com/oauth/token",
                {"grant_type": "refresh_token", "refresh_token": "rt"},
                egress=None,
            )
    assert time.monotonic() - start < 3.0
    assert exc_info.value.status == 0
    assert exc_info.value.body == "timeout"
