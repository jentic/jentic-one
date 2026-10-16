"""Integration tests for ``TokenRefresher`` single-flight refresh (#1432).

Real control DB on both backends (``JENTIC_TEST_BACKEND``): the advisory-lock
path on Postgres, the in-process lock path on SQLite. Only the vendor's token
endpoint is faked — a ``DirectOAuth2Provider`` whose ``_post_token`` stands in
for a vendor that rotates the refresh token on every refresh and rejects a
rotated-out one with ``invalid_grant``, as GitHub/Google/Atlassian do.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import (
    CredentialResolver,
    ResolvedCredential,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import OAuthTokenRepository
from jentic_one.control.services.credentials.providers.direct_oauth2 import (
    DirectOAuth2Provider,
    InvalidGrantError,
)
from jentic_one.control.services.credentials.providers.registry import ProviderRegistry
from jentic_one.shared.config import DirectOAuth2ProviderConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_PROVIDER = "rotating_vendor"
_CRED_ID = "cred_refresh_1432"
_SKEW_S = 60
_API = APIReference(vendor="rotating", name="api", version="v1")


class _RotatingVendor(DirectOAuth2Provider):
    """Real provider refresh logic over a fake token endpoint that rotates."""

    name = _PROVIDER

    def __init__(self, *, expires_in: int, live_refresh_token: str) -> None:
        super().__init__(
            DirectOAuth2ProviderConfig(
                redirect_uri="https://app.example.com/cb", expiry_skew_seconds=_SKEW_S
            )
        )
        self.expires_in = expires_in
        self.live_refresh_token = live_refresh_token
        self.sent_refresh_tokens: list[str] = []
        self._generation = 0

    async def _post_token(self, token_url: str, payload: dict[str, str]) -> dict[str, str]:
        sent = payload["refresh_token"]
        self.sent_refresh_tokens.append(sent)
        # Widen the race window so concurrent callers queue on the lock.
        await asyncio.sleep(0.1)
        if sent != self.live_refresh_token:
            raise InvalidGrantError("refresh token reused")
        self._generation += 1
        self.live_refresh_token = f"rt-{self._generation}"
        return {
            "access_token": f"at-{self._generation}",
            "refresh_token": self.live_refresh_token,
            "expires_in": str(self.expires_in),
        }


@pytest.fixture()
async def clean_tokens(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(OAuthToken))
            await session.execute(delete(OAuthClientCredential))
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


def _install_vendor(
    ctx: Context, monkeypatch: pytest.MonkeyPatch, *, expires_in: int
) -> _RotatingVendor:
    vendor = _RotatingVendor(expires_in=expires_in, live_refresh_token="rt-0")
    monkeypatch.setattr(ctx, "_providers", ProviderRegistry({_PROVIDER: vendor}))
    return vendor


async def _seed(ctx: Context, *, access: str, refresh: str) -> None:
    """Seed a credential whose access token expired five minutes ago."""
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CRED_ID,
                type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
                name="rotating-oauth",
                api_vendor=_API.vendor,
                api_name=_API.name,
                api_version=_API.version,
                provider=_PROVIDER,
            )
        )
        await session.flush()
        session.add(
            OAuthClientCredential(
                id=_CRED_ID,
                token_url="https://idp.example.com/token",
                client_id="client-1432",
                encrypted_client_secret=ctx.encryption.encrypt("client-secret"),
            )
        )
        session.add(
            OAuthToken(
                credential_id=_CRED_ID,
                encrypted_access_token=ctx.encryption.encrypt(access),
                encrypted_refresh_token=ctx.encryption.encrypt(refresh),
                expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )


async def _rotate(ctx: Context, *, access: str, refresh: str, expires_at: datetime) -> None:
    """Stand in for another replica completing a refresh after our snapshot."""
    async with ctx.control_db.transaction() as session:
        await OAuthTokenRepository.update_tokens(
            session,
            _CRED_ID,
            encrypted_access_token=ctx.encryption.encrypt(access),
            encrypted_refresh_token=ctx.encryption.encrypt(refresh),
            expires_at=expires_at,
        )


async def _snapshot(ctx: Context) -> ResolvedCredential:
    return await CredentialResolver(ctx).resolve(api=_API, caller="agent_1432")


async def _stored_refresh_token(ctx: Context) -> str:
    async with ctx.control_db.session() as session:
        row = await OAuthTokenRepository.get_by_credential(session, _CRED_ID)
    assert row is not None and row.encrypted_refresh_token is not None
    return ctx.encryption.decrypt(row.encrypted_refresh_token)


async def test_refresh_expired_token_rotates_and_persists(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired token is refreshed once and the rotated grant is persisted."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")

    token = await TokenRefresher(ctx).ensure_fresh(resolved=await _snapshot(ctx), caller="t")

    assert token == "at-1"
    assert vendor.sent_refresh_tokens == ["rt-0"]
    assert await _stored_refresh_token(ctx) == "rt-1"


@pytest.mark.parametrize(
    ("rotated_expires_in_s", "expected_token", "expected_sent"),
    [
        # Rotated grant is itself expired: refresh with the locked row's token.
        (-3600, "at-1", ["rt-B"]),
        # Rotated grant is fresh: use it, no vendor call.
        (3600, "at-B", []),
        # Rotated grant is inside the skew window but live: another holder
        # just refreshed, so use it rather than burning another rotation.
        (_SKEW_S // 2, "at-B", []),
    ],
    ids=["rotated-expired", "rotated-fresh", "rotated-within-skew"],
)
async def test_refresh_after_concurrent_rotation_uses_locked_row(
    integration_context: Context,
    clean_tokens: None,
    monkeypatch: pytest.MonkeyPatch,
    rotated_expires_in_s: int,
    expected_token: str,
    expected_sent: list[str],
) -> None:
    """Pins #1432: the refresh must never replay the pre-lock snapshot's
    rotated-out refresh token — doing so gets ``invalid_grant`` and bricks
    the credential (or revokes the grant on reuse-detecting vendors)."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-A", refresh="rt-A")
    stale = await _snapshot(ctx)

    # Another replica rotates A -> B between our snapshot and our lock.
    await _rotate(
        ctx,
        access="at-B",
        refresh="rt-B",
        expires_at=datetime.now(UTC) + timedelta(seconds=rotated_expires_in_s),
    )
    vendor.live_refresh_token = "rt-B"

    token = await TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="t")

    assert token == expected_token
    assert vendor.sent_refresh_tokens == expected_sent
    assert "rt-A" not in vendor.sent_refresh_tokens


async def test_short_lived_token_is_not_rerefreshed(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins #1432: the expiry skew is applied once, so a token whose
    ``expires_in`` is under twice the skew is still fresh right after its
    refresh instead of triggering another one on the next request."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=_SKEW_S + 5)
    await _seed(ctx, access="at-0", refresh="rt-0")

    before = datetime.now(UTC)
    first = await TokenRefresher(ctx).ensure_fresh(resolved=await _snapshot(ctx), caller="t")
    after = datetime.now(UTC)

    resolved = await _snapshot(ctx)
    assert resolved.token_expires_at is not None
    # Stored expiry is the vendor's true expiry, not pre-skewed.
    expires_in = timedelta(seconds=_SKEW_S + 5)
    assert before + expires_in <= resolved.token_expires_at <= after + expires_in

    second = await TokenRefresher(ctx).ensure_fresh(resolved=resolved, caller="t")

    assert first == second == "at-1"
    assert vendor.sent_refresh_tokens == ["rt-0"]


async def test_concurrent_refreshes_make_one_vendor_call(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins #1432: callers holding the same stale snapshot single-flight on
    the lock; waiters reuse the holder's token instead of replaying the
    rotated-out refresh token. ``expires_in`` under twice the skew is the
    case that exposes a double-applied skew."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=_SKEW_S + 50)
    await _seed(ctx, access="at-0", refresh="rt-0")
    stale = await _snapshot(ctx)

    async with asyncio.TaskGroup() as tg:
        tasks = [
            tg.create_task(TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="t"))
            for _ in range(5)
        ]

    assert {t.result() for t in tasks} == {"at-1"}
    assert vendor.sent_refresh_tokens == ["rt-0"]
    assert await _stored_refresh_token(ctx) == "rt-1"
