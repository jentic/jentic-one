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
    with (
        patch.object(scanner, "_due_credentials", new=AsyncMock(return_value=[])),
        patch("jentic_one.shared.jobs.connect_poll_scanner.ConnectSessionService") as service_cls,
    ):
        service_cls.return_value.advance_polling_target = advance_mock
        await scanner._tick()
    # Empty candidate list must skip the service entirely — instantiating
    # ConnectSessionService for zero work would still open DB txns on
    # the finalise path for other reasons, which is wasteful.
    service_cls.assert_not_called()
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
        await scanner._tick()
    assert advance_mock.await_count == 3
    assert [call.args[0] for call in advance_mock.await_args_list] == [
        "cred_1",
        "cred_2",
        "cred_3",
    ]


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
        await scanner._tick()
    assert advanced == ["cred_1", "cred_bad", "cred_3"]


def test_stop_clears_running_flag() -> None:
    scanner = ConnectPollScanner(_make_context())
    scanner._running = True
    scanner.stop()
    assert scanner._running is False
