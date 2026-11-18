"""Integration tests for cross-process refresh locking and connection pool usage."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete

from jentic_one.broker.services.credentials.errors import RefreshTransientError
from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import (
    CredentialResolver,
    ResolvedCredential,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.providers.direct_oauth2 import (
    DirectOAuth2Provider,
    InvalidGrantError,
)
from jentic_one.control.services.credentials.providers.registry import ProviderRegistry
from jentic_one.shared.config import (
    AppConfig,
    DirectOAuth2ProviderConfig,
    EgressConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_PROVIDER = "lock_pool_vendor"
_CRED_ID = "cred_refresh_lock_pool"
_SKEW_S = 60
_API = APIReference(vendor="lock-pool", name="api", version="v1")


class _GatedRotatingVendor(DirectOAuth2Provider):
    """DirectOAuth2Provider whose token exchange can pause at a transport event."""

    name = _PROVIDER

    def __init__(self, *, expires_in: int = 3600, live_refresh_token: str = "rt-0") -> None:
        super().__init__(
            DirectOAuth2ProviderConfig(
                redirect_uri="https://app.example.com/cb", expiry_skew_seconds=_SKEW_S
            )
        )
        self.expires_in = expires_in
        self.live_refresh_token = live_refresh_token
        self.sent_refresh_tokens: list[str] = []
        self.in_post_token = asyncio.Event()
        self.release_post_token: asyncio.Event | None = None
        self._generation = 0

    async def _post_token(
        self, token_url: str, payload: dict[str, str], *, egress: EgressConfig | None
    ) -> dict[str, str]:
        del token_url, egress
        sent = payload["refresh_token"]
        self.sent_refresh_tokens.append(sent)
        self.in_post_token.set()
        if self.release_post_token is not None:
            await self.release_post_token.wait()
        if sent != self.live_refresh_token:
            raise InvalidGrantError("refresh token reused")
        self._generation += 1
        self.live_refresh_token = f"rt-{self._generation}"
        return {
            "access_token": f"at-{self._generation}",
            "refresh_token": self.live_refresh_token,
            "expires_in": str(self.expires_in),
        }


def _require_postgres(db: DatabaseSession) -> None:
    if db.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL advisory lock and QueuePool test")


def _checked_out(db: DatabaseSession) -> int:
    pool: Any = db.engine.sync_engine.pool
    if not hasattr(pool, "checkedout"):
        return 0
    return int(pool.checkedout())


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


@asynccontextmanager
async def _second_context(
    integration_config: AppConfig,
    vendor: _GatedRotatingVendor,
    *,
    pool_max: int = 2,
) -> AsyncGenerator[Context, None]:
    cfg = integration_config.model_copy(deep=True)
    cfg.databases.control.pool_max = pool_max
    async with Context(cfg, allowed_dbs={"control"}, refresh_providers_on_boot=False) as ctx:
        ctx._providers = ProviderRegistry({_PROVIDER: vendor})
        yield ctx


async def _seed_expired_credential(ctx: Context) -> ResolvedCredential:
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CRED_ID,
                type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
                name="lock-pool-oauth",
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
                client_id="client-lock-pool",
                encrypted_client_secret=ctx.encryption.encrypt("client-secret"),
            )
        )
        session.add(
            OAuthToken(
                credential_id=_CRED_ID,
                encrypted_access_token=ctx.encryption.encrypt("at-0"),
                encrypted_refresh_token=ctx.encryption.encrypt("rt-0"),
                expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
    return await CredentialResolver(ctx).resolve(api=_API, caller="agent_lock_pool")


@pytest.mark.asyncio
async def test_waiting_refreshers_do_not_hold_pool_connections(
    integration_context: Context,
    integration_config: AppConfig,
    clean_tokens: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While one Context holds the refresh lock across a slow vendor call, 3 * pool_max
    concurrent refreshes on a second Context keep checked-out connections <= 2 and time out
    with RefreshTransientError('refresh_lock_timeout')."""
    _require_postgres(integration_context.control_db)
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.refresh.IDP_DEADLINE_SECONDS",
        0.35,
        raising=False,
    )

    vendor = _GatedRotatingVendor()
    vendor.release_post_token = asyncio.Event()
    monkeypatch.setattr(integration_context, "_providers", ProviderRegistry({_PROVIDER: vendor}))
    stale = await _seed_expired_credential(integration_context)

    pool_max = 2
    async with _second_context(integration_config, vendor, pool_max=pool_max) as ctx2:
        holder_task = asyncio.create_task(
            TokenRefresher(integration_context).ensure_fresh(resolved=stale, caller="ctx1")
        )
        waiter_tasks: list[asyncio.Task[str]] = []
        try:
            await asyncio.wait_for(vendor.in_post_token.wait(), timeout=5.0)

            waiter_tasks = [
                asyncio.create_task(
                    TokenRefresher(ctx2).ensure_fresh(resolved=stale, caller=f"ctx2_{i}")
                )
                for i in range(3 * pool_max)
            ]

            observed_ctx2_checked_out: list[int] = []
            observed_total_checked_out: list[int] = []
            for _ in range(8):
                await asyncio.sleep(0.02)
                c1 = _checked_out(integration_context.control_db)
                c2 = _checked_out(ctx2.control_db)
                observed_ctx2_checked_out.append(c2)
                observed_total_checked_out.append(c1 + c2)

            assert max(observed_ctx2_checked_out) <= 2
            assert max(observed_total_checked_out) <= 2

            outcomes = await asyncio.wait_for(
                asyncio.gather(*waiter_tasks, return_exceptions=True),
                timeout=2.0,
            )
            for outcome in outcomes:
                assert isinstance(outcome, RefreshTransientError)
                assert "refresh_lock_timeout" in str(outcome)
        finally:
            vendor.release_post_token.set()
            for t in waiter_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*waiter_tasks, return_exceptions=True)
            assert await asyncio.wait_for(holder_task, timeout=5.0) == "at-1"


@pytest.mark.asyncio
async def test_try_lock_fallback_still_serialises_cross_process_refresh(
    integration_context: Context,
    integration_config: AppConfig,
    clean_tokens: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second Context waiting behind an in-flight refresh polls try_acquire_refresh_lock,
    observes the refreshed token after the holder commits, and makes no second vendor call."""
    vendor = _GatedRotatingVendor()
    vendor.release_post_token = asyncio.Event()
    monkeypatch.setattr(integration_context, "_providers", ProviderRegistry({_PROVIDER: vendor}))
    stale = await _seed_expired_credential(integration_context)

    async with _second_context(integration_config, vendor, pool_max=2) as ctx2:
        holder_task = asyncio.create_task(
            TokenRefresher(integration_context).ensure_fresh(resolved=stale, caller="ctx1")
        )
        waiter_task: asyncio.Task[str] | None = None
        try:
            await asyncio.wait_for(vendor.in_post_token.wait(), timeout=5.0)

            if integration_context.control_db.engine.dialect.name == "postgresql":
                async with ctx2.control_db.transaction() as session:
                    acquired = await OAuthTokenRepository.try_acquire_refresh_lock(
                        session, _CRED_ID
                    )
                assert acquired is False

            waiter_task = asyncio.create_task(
                TokenRefresher(ctx2).ensure_fresh(resolved=stale, caller="ctx2")
            )
            await asyncio.sleep(0.08)
            vendor.release_post_token.set()

            holder_token, waiter_token = await asyncio.wait_for(
                asyncio.gather(holder_task, waiter_task),
                timeout=5.0,
            )
        finally:
            vendor.release_post_token.set()
            if waiter_task is not None and not waiter_task.done():
                waiter_task.cancel()
                await asyncio.gather(waiter_task, return_exceptions=True)
            if not holder_task.done():
                await asyncio.gather(holder_task, return_exceptions=True)

    assert holder_token == "at-1"
    assert waiter_token == "at-1"
    assert vendor.sent_refresh_tokens == ["rt-0"]
