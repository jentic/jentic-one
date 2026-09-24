"""Tests for DeviceAuthorizationHandler.advance — the RFC 8628 poll state machine.

``advance`` is the sole vendor-touching entrypoint for device flow, and
lives at the heart of server-driven polling: ``ConnectPollScanner`` is
its only caller, and every RFC 8628 outcome — pending, slow_down,
denied, expired, success, and non-8628 upstream faults — is mapped here
into the flow-agnostic ``StatusReport``. Regressions in this mapping
either strand device_code credentials in ``pending`` or terminate them
on transient upstream blips, so every branch gets an explicit test.
"""

from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from jentic_one.control.services.integrations import device_authorization as df
from jentic_one.control.services.integrations.flow_handlers.base import StatusReport
from jentic_one.control.services.integrations.flow_handlers.device_authorization import (
    DeviceAuthorizationHandler,
)
from jentic_one.shared.config import (
    AppConfig,
    ConnectConfig,
    CredentialsConfig,
    DatabaseConfig,
    DatabasesConfig,
    EncryptionConfig,
    EncryptionKey,
)
from jentic_one.shared.context import Context

_KEY_MATERIAL = base64.b64encode(os.urandom(32)).decode()


def _make_context() -> Context:
    cfg = AppConfig(
        databases=DatabasesConfig(
            registry=DatabaseConfig(backend="sqlite", path=":memory:"),
            admin=DatabaseConfig(backend="sqlite", path=":memory:"),
            control=DatabaseConfig(backend="sqlite", path=":memory:"),
        ),
        credentials=CredentialsConfig(
            encryption=EncryptionConfig(
                active_id="v1",
                entries=[EncryptionKey(id="v1", material=SecretStr(_KEY_MATERIAL))],
            ),
            connect=ConnectConfig(
                state_secret=SecretStr("test-state-secret"),
                state_ttl_seconds=600,
            ),
        ),
    )
    ctx = Context(cfg)

    @asynccontextmanager
    async def _session():
        yield MagicMock()

    @asynccontextmanager
    async def _tx():
        yield MagicMock()

    db = MagicMock()
    db.session = _session
    db.transaction = _tx
    ctx._control_db = db
    return ctx


def _make_dfc(
    ctx: Context,
    *,
    poll_interval_seconds: int | None = 5,
    last_polled_at: datetime | None = None,
    device_code: str = "dev-code-xyz",
    device_code_expires_at: datetime | None = None,
    granted_scopes: list[str] | None = None,
    encrypted: bool = True,
) -> MagicMock:
    row = MagicMock()
    row.poll_interval_seconds = poll_interval_seconds
    row.last_polled_at = last_polled_at
    row.encrypted_device_code = ctx.encryption.encrypt(device_code) if encrypted else None
    row.token_url = "https://idp.example.com/token"
    row.client_id = "public-client"
    row.device_code_expires_at = device_code_expires_at or (
        datetime.now(UTC) + timedelta(minutes=30)
    )
    row.granted_scopes = granted_scopes or ["repo"]
    return row


def _patch_repo(dfc: MagicMock | None, **extras: AsyncMock):
    patches = [
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.update_fields",
            new_callable=AsyncMock,
        ),
    ]
    return patches


@pytest.mark.asyncio()
async def test_advance_returns_pending_when_atomic_claim_loses() -> None:
    # RFC 8628 §3.5: the client MUST NOT poll more frequently than the
    # interval. Enforced by an atomic ``UPDATE`` on the aux row
    # (``try_claim_poll_slot``) — the DB is the single source of truth
    # for "is this credential due for a poll right now" so two scanner
    # replicas can't both pass the guard and double-poll the vendor.
    # When the claim fails (interval not elapsed, or a sibling replica
    # got there first) the handler must report pending and skip the
    # vendor call entirely.
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(
        ctx,
        poll_interval_seconds=5,
        last_polled_at=datetime.now(UTC),  # polled just now
    )
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch.object(df, "poll_device_authorization", new_callable=AsyncMock) as poll_mock,
    ):
        report = await handler.advance("cred_1")
    assert report == StatusReport(kind="pending")
    poll_mock.assert_not_awaited()


@pytest.mark.asyncio()
async def test_advance_returns_expired_when_device_code_ttl_passed() -> None:
    # Vendor-supplied device_code TTL — surfaces as terminal-expired
    # before we make a vendor call, so we don't burn a poll on a code
    # we already know is dead.
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(
        ctx,
        device_code_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with patch(
        "jentic_one.control.services.integrations.flow_handlers.device_authorization."
        "DeviceAuthorizationCredentialRepository.get_by_credential",
        new_callable=AsyncMock,
        return_value=dfc,
    ):
        report = await handler.advance("cred_1")
    assert report.kind == "expired"
    assert report.error_code == "device_code_expired"


@pytest.mark.asyncio()
async def test_advance_returns_pending_on_authorization_pending() -> None:
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(ctx, last_polled_at=None)
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            df,
            "poll_device_authorization",
            new_callable=AsyncMock,
            return_value=df.PollResult(status="pending"),
        ),
    ):
        report = await handler.advance("cred_1")
    assert report == StatusReport(kind="pending")


@pytest.mark.asyncio()
async def test_advance_widens_interval_on_slow_down() -> None:
    # RFC 8628 §3.5: on ``slow_down`` the client MUST widen its poll
    # interval by at least 5s. Pin the mutation so a future rewrite
    # can't silently drop it — hitting the vendor faster than requested
    # gets the whole app throttled.
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(ctx, poll_interval_seconds=5)
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.update_fields",
            new_callable=AsyncMock,
        ) as update_fields,
        patch.object(
            df,
            "poll_device_authorization",
            new_callable=AsyncMock,
            return_value=df.PollResult(status="slow_down"),
        ),
    ):
        report = await handler.advance("cred_1")
    assert report == StatusReport(kind="pending")
    update_fields.assert_awaited_once()
    assert update_fields.await_args is not None
    assert update_fields.await_args.kwargs["poll_interval_seconds"] == 10


@pytest.mark.asyncio()
async def test_advance_returns_failed_on_denied() -> None:
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(ctx)
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            df,
            "poll_device_authorization",
            new_callable=AsyncMock,
            return_value=df.PollResult(status="denied"),
        ),
    ):
        report = await handler.advance("cred_1")
    assert report.kind == "failed"
    assert report.error_code == "access_denied"


@pytest.mark.asyncio()
async def test_advance_returns_expired_from_vendor() -> None:
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(ctx)
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            df,
            "poll_device_authorization",
            new_callable=AsyncMock,
            return_value=df.PollResult(status="expired"),
        ),
    ):
        report = await handler.advance("cred_1")
    assert report.kind == "expired"
    assert report.error_code == "expired_token"


@pytest.mark.asyncio()
async def test_advance_returns_success_with_granted_scopes_from_aux_row() -> None:
    # The vendor may return ``scope=""`` on success (GitHub does).
    # granted_scopes MUST come from the confirmed set on the aux row,
    # otherwise permission-rule readback from ``oauth_token.scope``
    # would echo an empty list back to the UI as "no permissions".
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(ctx, granted_scopes=["repo", "read:user"])
    poll_result = df.PollResult(
        status="success",
        access_token="at_ok",
        refresh_token="rt_ok",
        expires_in=3600,
        scope="",  # deliberately unreliable
    )
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            df, "poll_device_authorization", new_callable=AsyncMock, return_value=poll_result
        ),
    ):
        report = await handler.advance("cred_1")
    assert report.kind == "success"
    assert report.tokens is not None
    assert report.tokens.access_token == "at_ok"
    assert report.tokens.granted_scopes == ["repo", "read:user"]


@pytest.mark.asyncio()
async def test_advance_maps_403_to_vendor_forbidden_terminal() -> None:
    # Non-retryable upstream faults (403 revoked app, malformed body,
    # unrecognised 4xx/5xx) fail loud on the first tick — no waiting
    # for the session TTL. 403 gets its own error code so ops can tell
    # "vendor blocked us" apart from generic upstream noise.
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(ctx)
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            df,
            "poll_device_authorization",
            new_callable=AsyncMock,
            side_effect=df.DeviceAuthorizationUpstreamError(403, "forbidden"),
        ),
    ):
        report = await handler.advance("cred_1")
    assert report.kind == "failed"
    assert report.error_code == "vendor_forbidden"


@pytest.mark.asyncio()
async def test_advance_maps_other_upstream_error_to_vendor_error_terminal() -> None:
    ctx = _make_context()
    handler = DeviceAuthorizationHandler(ctx)
    dfc = _make_dfc(ctx)
    with (
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.get_by_credential",
            new_callable=AsyncMock,
            return_value=dfc,
        ),
        patch(
            "jentic_one.control.services.integrations.flow_handlers.device_authorization."
            "DeviceAuthorizationCredentialRepository.try_claim_poll_slot",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch.object(
            df,
            "poll_device_authorization",
            new_callable=AsyncMock,
            side_effect=df.DeviceAuthorizationUpstreamError(500, "boom"),
        ),
    ):
        report = await handler.advance("cred_1")
    assert report.kind == "failed"
    assert report.error_code == "vendor_error"
