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
from typing import Any

import pytest
import structlog
from sqlalchemy import delete, event, text

from jentic_one.broker.services.credentials.errors import (
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.refresh import TokenRefresher
from jentic_one.broker.services.credentials.resolver import (
    CredentialResolver,
    ResolvedCredential,
)
from jentic_one.control.core.schema.connect_nonces import ConnectNonce
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import OAuthTokenRepository
from jentic_one.control.services.credentials.connect_service import ConnectService
from jentic_one.control.services.credentials.providers.direct_oauth2 import (
    DirectOAuth2Provider,
    InvalidGrantError,
)
from jentic_one.control.services.credentials.providers.registry import ProviderRegistry
from jentic_one.control.services.credentials.schemas.connect import (
    AuthCodeChallenge,
    ConnectCallback,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.credentials import CredentialUpdate
from jentic_one.control.services.credentials.schemas.provision import ProvisionResult
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.credentials.state import new_browser_binding
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    AppConfig,
    ControlEgressConfig,
    DirectOAuth2ProviderConfig,
    EgressConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import StoredCredentialType
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.schemas import APIReference
from tests.harness.postgres import superuser_config

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
        self.sent_to: list[tuple[str, str]] = []
        self.sent_egress: list[EgressConfig | None] = []
        # When set, a refresh pauses after reading the locked token row and
        # before it reads the client material (token_url, secret).
        self.hold_before_client_read: asyncio.Event | None = None
        self.paused = asyncio.Event()
        # When set, _post_token pauses right after rotating live_refresh_token
        # on the vendor side, before returning the HTTP 200 payload.
        self.hold_after_rotation: asyncio.Event | None = None
        self.rotated = asyncio.Event()
        self.post_delay_s: float = 0.1
        self._generation = 0

    async def _resolve_client_material(
        self, ctx: Context, *, credential_id: str
    ) -> tuple[str, str, str]:
        if self.hold_before_client_read is not None:
            self.paused.set()
            await self.hold_before_client_read.wait()
        return await super()._resolve_client_material(ctx, credential_id=credential_id)

    async def _post_token(
        self, token_url: str, payload: dict[str, str], *, egress: EgressConfig | None
    ) -> dict[str, str]:
        sent = payload["refresh_token"]
        self.sent_refresh_tokens.append(sent)
        self.sent_to.append((token_url, sent))
        self.sent_egress.append(egress)
        # Widen the race window so concurrent callers queue on the lock.
        await asyncio.sleep(self.post_delay_s)
        if sent != self.live_refresh_token:
            raise InvalidGrantError("refresh token reused")
        self._generation += 1
        self.live_refresh_token = f"rt-{self._generation}"
        self.rotated.set()
        if self.hold_after_rotation is not None:
            await self.hold_after_rotation.wait()
        return {
            "access_token": f"at-{self._generation}",
            "refresh_token": self.live_refresh_token,
            "expires_in": str(self.expires_in),
        }


@pytest.fixture()
async def clean_tokens(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ConnectNonce))
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


async def _seed(
    ctx: Context, *, access: str, refresh: str, provider_account_ref: str | None = None
) -> None:
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
                provider_account_ref=provider_account_ref,
            )
        )
        await session.flush()
        session.add(
            OAuthClientCredential(
                id=_CRED_ID,
                authorize_url="https://idp.example.com/authorize",
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


async def test_broker_refresh_uses_this_process_control_egress(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broker's refresh posts under the policy from its own config's ``control.egress``.

    In a split deployment that is the broker surface's config, so the allowlist
    must be set there as well as on the control surface.
    """
    ctx = integration_context
    monkeypatch.setattr(ctx.config.broker.egress, "allowed_private_subnets", ["10.50.0.0/16"])
    monkeypatch.setattr(
        ctx.config.control,
        "egress",
        ControlEgressConfig(allowed_internal_domains=[".corp.internal"]),
    )
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")

    await TokenRefresher(ctx).ensure_fresh(resolved=await _snapshot(ctx), caller="t")

    assert len(vendor.sent_egress) == 1
    egress = vendor.sent_egress[0]
    assert egress is not None
    assert (egress.allowed_private_subnets, egress.allowed_internal_domains) == (
        ["10.50.0.0/16"],
        [".corp.internal"],
    )


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


_OLD_TOKEN_URL = "https://idp.example.com/token"
_NEW_TOKEN_URL = "https://login.other.example.org/token"
_ADMIN = Identity(sub="admin_1425", permissions=["org:admin"])


async def _move_token_url(ctx: Context) -> None:
    """The cross-origin token_url PATCH (re-entering the client secret)."""
    await CredentialService(ctx).update(
        _CRED_ID,
        CredentialUpdate(
            type=CredentialType.OAUTH2,
            token_url=_NEW_TOKEN_URL,
            client_secret="client-secret-new",  # pragma: allowlist secret
        ),
        identity=_ADMIN,
    )


@pytest.mark.parametrize(
    "provider_account_ref", [None, "alice"], ids=["no-ref", "identity-echo-ref"]
)
async def test_refresh_after_token_url_move_needs_reconnect(
    integration_context: Context,
    clean_tokens: None,
    monkeypatch: pytest.MonkeyPatch,
    provider_account_ref: str | None,
) -> None:
    """Pins #1425: a snapshot taken before a cross-origin token_url move must
    not present its refresh token anywhere once the move has dropped the
    grant; the refresh fails as needs-reconnect without a vendor call. A
    connect-session finalise writes the identity-echo display name into
    ``provider_account_ref``; on a provider that keeps its grant in
    ``oauth_tokens`` that ref does not stand in for the dropped row."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0", provider_account_ref=provider_account_ref)
    stale = await _snapshot(ctx)

    await _move_token_url(ctx)

    with pytest.raises(RefreshInvalidGrantError):
        await TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="t")
    with pytest.raises(RefreshInvalidGrantError):
        await TokenRefresher(ctx).ensure_fresh(resolved=await _snapshot(ctx), caller="t")
    assert vendor.sent_to == []


async def test_inflight_refresh_never_sends_old_token_to_new_origin(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins #1425: a refresh that already holds the lock and read the token
    row when the cross-origin PATCH arrives finishes against the stored
    origin; the PATCH waits for it, then drops the grant, and the next
    refresh needs a reconnect. The old refresh token never reaches the new
    origin."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")
    stale = await _snapshot(ctx)
    release = asyncio.Event()
    vendor.hold_before_client_read = release

    async with asyncio.TaskGroup() as tg:
        refresh = tg.create_task(TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="t"))
        await asyncio.wait_for(vendor.paused.wait(), timeout=5)
        move = tg.create_task(_move_token_url(ctx))
        # The PATCH is held behind the refresh until the refresh commits.
        await asyncio.sleep(0.3)
        assert not move.done()
        release.set()

    assert refresh.result() == "at-1"
    assert vendor.sent_to == [(_OLD_TOKEN_URL, "rt-0")]
    async with ctx.control_db.session() as session:
        assert await OAuthTokenRepository.get_by_credential(session, _CRED_ID) is None

    vendor.hold_before_client_read = None
    with pytest.raises(RefreshInvalidGrantError):
        await TokenRefresher(ctx).ensure_fresh(resolved=await _snapshot(ctx), caller="t")
    assert all(url == _OLD_TOKEN_URL for url, _ in vendor.sent_to)


async def test_cancelled_inflight_refresh_persists_rotated_grant_and_reraises(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins #1432: cancelling a caller after the vendor rotates the refresh
    token still persists the rotated grant before re-raising ``CancelledError``
    so the credential is not left holding the dead refresh token."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")
    stale = await _snapshot(ctx)

    release_response = asyncio.Event()
    vendor.hold_after_rotation = release_response

    with structlog.testing.capture_logs() as captured:
        task = asyncio.create_task(TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="t"))
        await asyncio.wait_for(vendor.rotated.wait(), timeout=5)
        task.cancel()
        release_response.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert await _stored_refresh_token(ctx) == "rt-1"
    vendor.hold_after_rotation = None
    # Subsequent caller reuses the persisted access token without replaying rt-0.
    assert await TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="t2") == "at-1"
    assert vendor.sent_refresh_tokens == ["rt-0"]
    for entry in captured:
        assert "rt-0" not in str(entry)
        assert "rt-1" not in str(entry)
        assert "at-1" not in str(entry)


async def _terminate_postgres_backends(
    integration_config: AppConfig, *, where_clause: str, params: dict[str, Any] | None = None
) -> int:
    super_cfg = integration_config.model_copy(
        update={
            "databases": integration_config.databases.model_copy(
                update={"control": superuser_config(integration_config.databases.control)}
            )
        }
    )
    async with (
        Context(super_cfg, allowed_dbs={"control"}, refresh_providers_on_boot=False) as sctx,
        sctx.control_db.session() as session,
    ):
        rows = (
            await session.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND pid <> pg_backend_pid()"
                    f" AND ({where_clause})"
                ),
                params or {},
            )
        ).all()
        return len(rows)


async def test_commit_failure_and_idle_tx_kill_recover_rotated_grant_on_postgres(
    integration_config: AppConfig,
    integration_context: Context,
    clean_tokens: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins #1432: no transaction sits open across the vendor HTTP call, and a
    connection lost while committing the rotated tokens retries on a fresh
    session so the rotated refresh token is preserved."""
    if integration_context.control_db.backend.dialect_name != "postgres":
        pytest.skip("Postgres backend termination test")

    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")
    stale = await _snapshot(ctx)

    release_response = asyncio.Event()
    vendor.hold_after_rotation = release_response

    # Simulate both:
    # 1. Killing any 'idle in transaction' sessions while the vendor HTTP
    #    exchange is in flight (managed Postgres idle_in_transaction timeout).
    # 2. Terminating the committing connection right after the first UPDATE of
    #    oauth_tokens flushes (before COMMIT), forcing commit recovery.
    killed_first_update = False
    sync_engine = ctx.control_db.engine.sync_engine

    def _drop_first_token_update(conn: Any, _cursor: Any, statement: str, *_args: object) -> None:
        nonlocal killed_first_update
        if not killed_first_update and "UPDATE oauth_tokens" in statement:
            killed_first_update = True
            raw_driver_conn = conn.connection.driver_connection
            raw_driver_conn.terminate()

    event.listen(sync_engine, "after_cursor_execute", _drop_first_token_update)
    try:
        with structlog.testing.capture_logs() as captured:
            task = asyncio.create_task(TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="t"))
            await asyncio.wait_for(vendor.rotated.wait(), timeout=5)
            killed_idle_tx = await _terminate_postgres_backends(
                integration_config, where_clause="state = 'idle in transaction'"
            )
            release_response.set()
            token = await task
    finally:
        event.remove(sync_engine, "after_cursor_execute", _drop_first_token_update)

    assert killed_idle_tx == 0
    assert killed_first_update is True
    assert token == "at-1"
    assert await _stored_refresh_token(ctx) == "rt-1"
    assert vendor.sent_refresh_tokens == ["rt-0"]
    for entry in captured:
        assert "rt-0" not in str(entry)
        assert "rt-1" not in str(entry)
        assert "at-1" not in str(entry)


def _constrain_pool(db: DatabaseSession, *, pool_size: int, timeout_s: float) -> None:
    pool: Any = db.engine.sync_engine.pool
    pool._pool.maxsize = pool_size
    pool._max_overflow = 0
    pool._timeout = timeout_s


async def _tiny_pool_context(
    integration_config: AppConfig, *, pool_max: int, timeout_s: float
) -> Context:
    cfg = integration_config.model_copy(
        update={
            "databases": integration_config.databases.model_copy(
                update={
                    "control": integration_config.databases.control.model_copy(
                        update={"pool_max": pool_max}
                    )
                }
            )
        }
    )
    ctx = Context(cfg, allowed_dbs={"control"}, refresh_providers_on_boot=False)
    await ctx.startup()
    _constrain_pool(ctx.control_db, pool_size=pool_max, timeout_s=timeout_s)
    return ctx


async def test_concurrent_waiters_and_replicas_do_not_starve_small_postgres_pool(
    integration_config: AppConfig,
    integration_context: Context,
    clean_tokens: None,
) -> None:
    """Pins #1432: 30 concurrent refreshes across two replicas plus concurrent
    resolver reads complete without pool timeout on a strict 2-connection pool,
    making exactly one vendor call."""
    if integration_context.control_db.backend.dialect_name != "postgres":
        pytest.skip("Postgres pool starvation test")

    await _seed(integration_context, access="at-0", refresh="rt-0")
    stale = await _snapshot(integration_context)

    vendor = _RotatingVendor(expires_in=3600, live_refresh_token="rt-0")
    vendor.post_delay_s = 0.25

    ctx_a = await _tiny_pool_context(integration_config, pool_max=2, timeout_s=1.5)
    ctx_b = await _tiny_pool_context(integration_config, pool_max=2, timeout_s=1.5)
    try:
        ctx_a._providers = ProviderRegistry({_PROVIDER: vendor})
        ctx_b._providers = ProviderRegistry({_PROVIDER: vendor})

        async with asyncio.timeout(10), asyncio.TaskGroup() as tg:
            refreshes_a = [
                tg.create_task(TokenRefresher(ctx_a).ensure_fresh(resolved=stale, caller=f"a-{i}"))
                for i in range(20)
            ]
            refreshes_b = [
                tg.create_task(TokenRefresher(ctx_b).ensure_fresh(resolved=stale, caller=f"b-{i}"))
                for i in range(10)
            ]
            reads = [
                tg.create_task(CredentialResolver(ctx_a).resolve(api=_API, caller=f"r-{i}"))
                for i in range(10)
            ]

        assert {t.result() for t in refreshes_a + refreshes_b} == {"at-1"}
        assert len(reads) == 10 and all(r.result().credential_id == _CRED_ID for r in reads)
        assert vendor.sent_refresh_tokens == ["rt-0"]
    finally:
        await ctx_b.shutdown()
        await ctx_a.shutdown()

    assert await _stored_refresh_token(integration_context) == "rt-1"


async def test_concurrent_distinct_credentials_do_not_starve_small_postgres_pool(
    integration_config: AppConfig,
    integration_context: Context,
    clean_tokens: None,
) -> None:
    """Pins #1432: refreshing 8 distinct credentials concurrently against a slow
    token endpoint (8 * 0.35s > 1.5s pool timeout) plus concurrent reads does
    not hold pooled DB connections across upstream HTTP or exhaust a 2-connection pool."""
    if integration_context.control_db.backend.dialect_name != "postgres":
        pytest.skip("Postgres pool starvation test")

    num_creds = 8
    vendors: dict[str, _RotatingVendor] = {}
    snapshots: list[ResolvedCredential] = []

    for idx in range(num_creds):
        cid = f"cred_pool_{idx}"
        prov_name = f"rotating_pool_{idx}"
        api = APIReference(vendor=f"vendor_pool_{idx}", name="api", version="v1")
        v = _RotatingVendor(expires_in=3600, live_refresh_token=f"rt0-{idx}")
        v.name = prov_name
        v.post_delay_s = 0.35
        vendors[prov_name] = v

        async with integration_context.control_db.transaction() as session:
            session.add(
                Credential(
                    id=cid,
                    type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
                    name=f"cred-{idx}",
                    api_vendor=api.vendor,
                    api_name=api.name,
                    api_version=api.version,
                    provider=prov_name,
                )
            )
            await session.flush()
            session.add(
                OAuthClientCredential(
                    id=cid,
                    token_url="https://idp.example.com/token",
                    client_id=f"client-{idx}",
                    encrypted_client_secret=integration_context.encryption.encrypt("secret"),
                )
            )
            session.add(
                OAuthToken(
                    credential_id=cid,
                    encrypted_access_token=integration_context.encryption.encrypt(f"at0-{idx}"),
                    encrypted_refresh_token=integration_context.encryption.encrypt(f"rt0-{idx}"),
                    expires_at=datetime.now(UTC) - timedelta(minutes=5),
                )
            )
        snapshots.append(await CredentialResolver(integration_context).resolve(api=api, caller="t"))

    ctx = await _tiny_pool_context(integration_config, pool_max=2, timeout_s=1.5)
    try:
        ctx._providers = ProviderRegistry(dict(vendors))
        read_api = APIReference(vendor="vendor_pool_0", name="api", version="v1")

        async with asyncio.timeout(10), asyncio.TaskGroup() as tg:
            refresh_tasks = [
                tg.create_task(TokenRefresher(ctx).ensure_fresh(resolved=snap, caller=f"w-{w}"))
                for snap in snapshots
                for w in range(2)
            ]
            read_tasks = [
                tg.create_task(CredentialResolver(ctx).resolve(api=read_api, caller=f"r-{i}"))
                for i in range(8)
            ]

        assert len(refresh_tasks) == num_creds * 2
        assert all(t.result() == "at-1" for t in refresh_tasks)
        assert all(r.result().credential_id == "cred_pool_0" for r in read_tasks)
        for idx, prov_name in enumerate(vendors):
            assert vendors[prov_name].sent_refresh_tokens == [f"rt0-{idx}"]
        assert ctx.control_db._keyed_locks == {}
    finally:
        await ctx.shutdown()


async def test_cancelled_leader_satisfies_waiters_and_evicts_keyed_lock(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins #1432: when the refresh leader is cancelled mid-exchange, its
    shielded commit finishes before ``CancelledError`` propagates, uncancelled
    waiters receive the fresh token from that single vendor call, and the
    in-process lock map evicts the credential key once idle."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")
    stale = await _snapshot(ctx)

    release_response = asyncio.Event()
    vendor.hold_after_rotation = release_response

    leader = asyncio.create_task(TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="leader"))
    await asyncio.wait_for(vendor.rotated.wait(), timeout=5)
    waiters = [
        asyncio.create_task(TokenRefresher(ctx).ensure_fresh(resolved=stale, caller=f"w-{i}"))
        for i in range(4)
    ]
    await asyncio.sleep(0.05)

    leader.cancel()
    release_response.set()

    with pytest.raises(asyncio.CancelledError):
        await leader
    results = await asyncio.gather(*waiters)

    assert results == ["at-1", "at-1", "at-1", "at-1"]
    assert vendor.sent_refresh_tokens == ["rt-0"]
    assert await _stored_refresh_token(ctx) == "rt-1"
    assert ctx.control_db._keyed_locks == {}


async def _stored_token_snapshot(ctx: Context) -> tuple[str, str, str | None]:
    async with ctx.control_db.session() as session:
        row = await OAuthTokenRepository.get_by_credential(session, _CRED_ID)
    assert row is not None and row.encrypted_refresh_token is not None
    return (
        ctx.encryption.decrypt(row.encrypted_access_token),
        ctx.encryption.decrypt(row.encrypted_refresh_token),
        row.scope,
    )


async def test_reconnect_racing_inflight_refresh_preserves_reconnected_grant(
    integration_context: Context, clean_tokens: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins #1432: a reconnect completing while an older grant's refresh is in
    flight waits on the grant lock and lands last, so the reconnected grant and
    its scopes are never overwritten by the older refresh."""
    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")
    stale = await _snapshot(ctx)

    binding = new_browser_binding()
    challenge = await ConnectService(ctx).begin(
        _CRED_ID,
        ConnectRequest(scopes=["read", "write"]),
        identity=_ADMIN,
        redirect_uri="https://app.example.com/cb",
        browser_binding=binding.claim,
    )
    assert isinstance(challenge, AuthCodeChallenge)

    async def _complete_connect(_ctx: Context, *, state: Any, callback: Any) -> ProvisionResult:
        return ProvisionResult(
            access_token="at-reconnect",
            refresh_token="rt-reconnect",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            scope="read write",
            token_url=_OLD_TOKEN_URL,
        )

    monkeypatch.setattr(vendor, "complete_connect", _complete_connect)

    release_response = asyncio.Event()
    vendor.hold_after_rotation = release_response

    async with asyncio.timeout(10), asyncio.TaskGroup() as tg:
        refresh_task = tg.create_task(
            TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="agent")
        )
        await asyncio.wait_for(vendor.rotated.wait(), timeout=5)
        reconnect_task = tg.create_task(
            ConnectService(ctx).complete(
                challenge.state,
                ConnectCallback(code="auth-code-1432"),
                browser_binding=binding.value,
            )
        )
        await asyncio.sleep(0.15)
        assert not reconnect_task.done()
        release_response.set()

    assert reconnect_task.result() == _CRED_ID
    assert refresh_task.result() == "at-1"
    assert await _stored_token_snapshot(ctx) == ("at-reconnect", "rt-reconnect", "read write")


async def test_cas_preserves_concurrent_reconnect_when_lock_connection_drops_on_postgres(
    integration_config: AppConfig,
    integration_context: Context,
    clean_tokens: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins #1432: even if the advisory-lock session drops mid-refresh so a
    concurrent reconnect commits first, compare-and-swap on the pre-refresh
    ciphertext keeps the reconnected grant intact and returns its live token."""
    if integration_context.control_db.backend.dialect_name != "postgres":
        pytest.skip("Postgres advisory-lock drop test")

    ctx = integration_context
    vendor = _install_vendor(ctx, monkeypatch, expires_in=3600)
    await _seed(ctx, access="at-0", refresh="rt-0")
    stale = await _snapshot(ctx)

    binding = new_browser_binding()
    challenge = await ConnectService(ctx).begin(
        _CRED_ID,
        ConnectRequest(scopes=["read", "write"]),
        identity=_ADMIN,
        redirect_uri="https://app.example.com/cb",
        browser_binding=binding.claim,
    )
    assert isinstance(challenge, AuthCodeChallenge)

    async def _complete_connect(_ctx: Context, *, state: Any, callback: Any) -> ProvisionResult:
        return ProvisionResult(
            access_token="at-reconnect",
            refresh_token="rt-reconnect",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            scope="read write",
            token_url=_OLD_TOKEN_URL,
        )

    monkeypatch.setattr(vendor, "complete_connect", _complete_connect)

    release_response = asyncio.Event()
    vendor.hold_after_rotation = release_response

    refresh_task = asyncio.create_task(
        TokenRefresher(ctx).ensure_fresh(resolved=stale, caller="agent")
    )
    await asyncio.wait_for(vendor.rotated.wait(), timeout=5)

    # Terminate the backend holding the advisory lock so ConnectService can commit
    # while the refresh's vendor call is still paused.
    terminated = await _terminate_postgres_backends(
        integration_config,
        where_clause="pid IN (SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted)",
    )
    assert terminated >= 1

    await ConnectService(ctx).complete(
        challenge.state,
        ConnectCallback(code="auth-code-1432"),
        browser_binding=binding.value,
    )
    release_response.set()

    token = await refresh_task
    assert token == "at-reconnect"
    assert await _stored_token_snapshot(ctx) == ("at-reconnect", "rt-reconnect", "read write")


async def test_refresh_lock_timeout_maps_to_transient_without_starving_pool(
    integration_config: AppConfig,
    integration_context: Context,
    clean_tokens: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins #1432: when another process holds the credential's grant lock past
    the bounded wait, ``TokenRefresher`` raises ``RefreshTransientError`` while
    leaving the session pool free for concurrent reads."""
    if integration_context.control_db.backend.dialect_name != "postgres":
        pytest.skip("Postgres advisory-lock timeout test")

    await _seed(integration_context, access="at-0", refresh="rt-0")
    stale = await _snapshot(integration_context)
    vendor = _RotatingVendor(expires_in=3600, live_refresh_token="rt-0")

    ctx = await _tiny_pool_context(integration_config, pool_max=2, timeout_s=1.5)
    try:
        ctx._providers = ProviderRegistry({_PROVIDER: vendor})
        refresher = TokenRefresher(ctx)
        monkeypatch.setattr(refresher, "_lock_wait_timeout_s", 0.25)

        async with integration_context.control_db.transaction() as holder_session:
            await OAuthTokenRepository.acquire_refresh_lock(holder_session, _CRED_ID)
            with pytest.raises(RefreshTransientError, match="RefreshLockTimeout"):
                await refresher.ensure_fresh(resolved=stale, caller="waiter")
            resolved_read = await CredentialResolver(ctx).resolve(api=_API, caller="reader")
            assert resolved_read.credential_id == _CRED_ID
    finally:
        await ctx.shutdown()

    assert vendor.sent_refresh_tokens == []
