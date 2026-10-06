"""Tests for ConnectPollScanner._tick — batching + fault isolation.

The scanner is the sole upstream trigger for RFC 8628 polling — the
``/status`` HTTP surface reads stored state only. Two invariants matter
enough to pin explicitly:

  1. One bad row must NOT abort the batch. A vendor going hostile on
     one credential can't be allowed to strand every other credential
     that's waiting for a poll tick.
  2. Every candidate must dispatch through ``advance_polling_target``,
     which is where session-mode vs credential-mode routing lives.
     Bypassing it (e.g. calling the handler directly) would skip that
     dispatch and leak session-flow state mutations.
"""

from __future__ import annotations

import asyncio
import base64
import os
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

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
from jentic_one.shared.jobs.connect_poll_scanner import ConnectPollScanner

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
    return Context(cfg)


@pytest.mark.asyncio()
async def test_tick_noop_when_no_due_credentials() -> None:
    scanner = ConnectPollScanner(_make_context())
    advance_mock = AsyncMock()
    sweep_mock = AsyncMock(return_value=0)
    with (
        patch.object(scanner, "_due_credentials", new=AsyncMock(return_value=[])),
        patch("jentic_one.shared.jobs.connect_poll_scanner.ConnectSessionService") as service_cls,
    ):
        service_cls.return_value.advance_polling_target = advance_mock
        service_cls.return_value.expire_stale_sessions = sweep_mock
        await scanner._tick()
    # No device-flow candidates ⇒ no advancement, but the flow-agnostic
    # TTL sweep still runs — it is the ONLY expiry driver for ``created``
    # sessions and abandoned auth-code sessions, which by definition have
    # no device-code aux row for ``_due_credentials`` to find.
    sweep_mock.assert_awaited_once()
    advance_mock.assert_not_awaited()


@pytest.mark.asyncio()
async def test_tick_advances_each_candidate() -> None:
    scanner = ConnectPollScanner(_make_context())
    advance_mock = AsyncMock()
    with (
        patch.object(
            scanner,
            "_due_credentials",
            new=AsyncMock(return_value=["cred_1", "cred_2", "cred_3"]),
        ),
        patch("jentic_one.shared.jobs.connect_poll_scanner.ConnectSessionService") as service_cls,
    ):
        service_cls.return_value.advance_polling_target = advance_mock
        service_cls.return_value.expire_stale_sessions = AsyncMock(return_value=0)
        await scanner._tick()
    assert advance_mock.await_count == 3
    # Advances run concurrently under ``asyncio.gather`` — completion
    # order isn't guaranteed (a slow vendor at position 0 must not head-
    # of-line-block positions 1+), so pin the *set* of ids rather than
    # sequence.
    assert {call.args[0] for call in advance_mock.await_args_list} == {
        "cred_1",
        "cred_2",
        "cred_3",
    }


@pytest.mark.asyncio()
async def test_tick_does_not_head_of_line_block_on_slow_vendor() -> None:
    # A slow vendor at the head of the batch is the whole reason to
    # switch from serial iteration to bounded ``asyncio.gather``: a
    # 15s-timeout vendor at position 0 in a serial loop stalls every
    # credential behind it. Pin the concurrency: a long-running advance
    # for ``cred_slow`` cannot block ``cred_fast`` from completing.
    scanner = ConnectPollScanner(_make_context())
    slow_started = asyncio.Event()
    fast_done = asyncio.Event()
    slow_release = asyncio.Event()
    order: list[str] = []

    async def _fake_advance(credential_id: str) -> None:
        if credential_id == "cred_slow":
            slow_started.set()
            await slow_release.wait()
            order.append(credential_id)
            return
        # ``cred_fast`` waits until the slow one has actually started
        # (proving they run in parallel), then completes.
        await slow_started.wait()
        order.append(credential_id)
        fast_done.set()

    with (
        patch.object(
            scanner,
            "_due_credentials",
            new=AsyncMock(return_value=["cred_slow", "cred_fast"]),
        ),
        patch("jentic_one.shared.jobs.connect_poll_scanner.ConnectSessionService") as service_cls,
    ):
        service_cls.return_value.advance_polling_target = AsyncMock(side_effect=_fake_advance)
        service_cls.return_value.expire_stale_sessions = AsyncMock(return_value=0)
        tick_task = asyncio.create_task(scanner._tick())
        # ``cred_fast`` must complete *before* we release ``cred_slow``.
        await asyncio.wait_for(fast_done.wait(), timeout=1.0)
        slow_release.set()
        await asyncio.wait_for(tick_task, timeout=1.0)
    assert order == ["cred_fast", "cred_slow"]


@pytest.mark.asyncio()
async def test_tick_isolates_per_row_failures() -> None:
    # If one credential's advancement blows up (a bad row, a vendor
    # returning malformed JSON, a decrypt failure) every other
    # credential in the same tick MUST still get its poll. A batch that
    # aborts on the first bad row strands the rest until the next tick,
    # and if the row is persistently bad, forever.
    scanner = ConnectPollScanner(_make_context())

    advanced: list[str] = []

    async def _fake_advance(credential_id: str) -> None:
        advanced.append(credential_id)
        if credential_id == "cred_bad":
            raise RuntimeError("kaboom")

    with (
        patch.object(
            scanner,
            "_due_credentials",
            new=AsyncMock(return_value=["cred_1", "cred_bad", "cred_3"]),
        ),
        patch("jentic_one.shared.jobs.connect_poll_scanner.ConnectSessionService") as service_cls,
    ):
        service_cls.return_value.advance_polling_target = AsyncMock(side_effect=_fake_advance)
        service_cls.return_value.expire_stale_sessions = AsyncMock(return_value=0)
        await scanner._tick()
    assert advanced == ["cred_1", "cred_bad", "cred_3"]


def test_stop_clears_running_flag() -> None:
    scanner = ConnectPollScanner(_make_context())
    scanner._running = True
    scanner.stop()
    assert scanner._running is False


@pytest.mark.asyncio()
async def test_tick_threads_catalog_auto_importer_into_session_service() -> None:
    # The scanner runs outside the request scope, so it can't see the
    # request-scoped ``app.state.catalog_auto_importer`` that
    # ``get_connect_session_service`` reads. If it forgets to thread the
    # importer through, every scanner-driven device-flow finalise
    # silently skips ``_maybe_import_catalog`` and the vendor's OpenAPI
    # never lands in the workspace catalog. Pin the threading here.
    importer = object()  # opaque — we only care that it's passed through
    scanner = ConnectPollScanner(_make_context(), catalog_auto_importer=importer)  # type: ignore[arg-type]
    with (
        patch.object(
            scanner,
            "_due_credentials",
            new=AsyncMock(return_value=["cred_1"]),
        ),
        patch("jentic_one.shared.jobs.connect_poll_scanner.ConnectSessionService") as service_cls,
    ):
        service_cls.return_value.advance_polling_target = AsyncMock()
        service_cls.return_value.expire_stale_sessions = AsyncMock(return_value=0)
        await scanner._tick()
    # Constructed with the importer keyword; the same object identity.
    _, kwargs = service_cls.call_args
    assert kwargs.get("catalog_auto_importer") is importer
