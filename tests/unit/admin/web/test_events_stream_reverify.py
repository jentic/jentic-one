"""``GET /events/stream`` re-verifies its caller while open (#1836).

A fake clock drives the interval, a scripted verifier stands in for the token
chain, and a fake stream service calls ``refresh_identity`` before each poll
the way :class:`EventStreamService` does. This pins:

* no re-verification before the interval elapses, one per interval after;
* the refreshed identity is what scopes the next poll;
* a refused credential, a revoked permission, or a verifier that cannot answer
  each ends the stream with a ``stream_closed`` frame naming the reason;
* an event store that cannot be read ends it with ``stream_closed: unavailable``;
* a client disconnect ends it without a ``stream_closed`` frame.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jentic.problem_details import Unauthorized

from jentic_one.admin.services.event_stream_service import IdentityRefresh
from jentic_one.admin.services.schemas.events import Heartbeat
from jentic_one.admin.web.deps import (
    get_event_stream_service,
    get_stream_clock,
    get_stream_recheck_seconds,
)
from jentic_one.admin.web.routers.events import router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AdminConfig, AdminEventsConfig
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.web.deps import (
    CredentialRecheckOutcome,
    CredentialRecheckResult,
    get_credential_recheck,
    get_ctx,
    resolve_identity,
)

_OPENING = Identity(sub="usr_1", permissions=["events:read", "org:admin"])
_POLL_SECONDS = 5.0


@dataclass
class _Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _FakeStream:
    """Polls ``polls`` times, advancing the clock one poll interval before each.

    With ``store_down_after`` set, the poll after that many raises
    ``DatabaseUnavailableError`` the way an unreachable event store does.
    """

    clock: _Clock
    polls: int
    store_down_after: int | None = None
    scoped_by: list[Identity] = field(default_factory=list)
    closed: bool = False

    def stream(self, *, refresh_identity: IdentityRefresh, **_: Any) -> AsyncIterator[Heartbeat]:
        async def _gen() -> AsyncIterator[Heartbeat]:
            try:
                for poll in range(self.polls):
                    if poll == self.store_down_after:
                        raise DatabaseUnavailableError("admin db down")
                    self.clock.now += _POLL_SECONDS
                    identity = await refresh_identity()
                    if identity is None:
                        return
                    self.scoped_by.append(identity)
                    yield Heartbeat(sent_at=datetime.now(UTC))
            finally:
                self.closed = True

        return _gen()


Verdict = Identity | Exception


def _app(
    verdicts: list[Verdict],
    *,
    polls: int,
    recheck_seconds: float | None,
    store_down_after: int | None = None,
) -> tuple[TestClient, _FakeStream, list[str]]:
    """Build the app; ``recheck_seconds=None`` leaves the interval to config."""
    clock = _Clock()
    stream = _FakeStream(clock=clock, polls=polls, store_down_after=store_down_after)
    calls: list[str] = []

    async def _verify(token: str, request: Request) -> Identity:
        calls.append(token)
        verdict = verdicts[min(len(calls), len(verdicts)) - 1]
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    app = FastAPI()
    app.include_router(router)
    app.state.verify_token = _verify
    app.dependency_overrides[resolve_identity] = lambda: _OPENING
    app.dependency_overrides[get_event_stream_service] = lambda: stream
    app.dependency_overrides[get_stream_clock] = lambda: clock
    if recheck_seconds is not None:
        app.dependency_overrides[get_stream_recheck_seconds] = lambda: recheck_seconds
    return TestClient(app), stream, calls


def _frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    frames = []
    for raw in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in raw.split("\n"))
        frames.append((fields["event"], json.loads(fields["data"])))
    return frames


def _get(client: TestClient) -> list[tuple[str, dict[str, Any]]]:
    resp = client.get("/events/stream", headers={"Authorization": "Bearer tok_live"})
    assert resp.status_code == 200, resp.text
    return _frames(resp.text)


def test_reverifies_once_per_interval_not_per_poll() -> None:
    client, _, calls = _app([_OPENING], polls=4, recheck_seconds=10.0)

    frames = _get(client)

    # Polls land at +5, +10, +15, +20: only +10 and +20 have a full interval behind them.
    assert calls == ["tok_live", "tok_live"]
    assert [name for name, _ in frames] == ["heartbeat"] * 4


def test_no_reverification_inside_the_first_interval() -> None:
    client, _, calls = _app([Unauthorized(detail="revoked")], polls=1, recheck_seconds=30.0)

    assert [name for name, _ in _get(client)] == ["heartbeat"]
    assert calls == []


def test_refreshed_identity_scopes_the_next_poll() -> None:
    narrowed = Identity(sub="usr_1", permissions=["events:read"])
    client, stream, _ = _app([narrowed], polls=2, recheck_seconds=_POLL_SECONDS)

    _get(client)

    assert [i.permissions for i in stream.scoped_by] == [["events:read"], ["events:read"]]


@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        (Unauthorized(detail="expired"), "refused"),
        (Identity(sub="usr_1", permissions=["apis:read"]), "forbidden"),
        (
            Identity(sub="usr_1", permissions=["events:read"], must_change_password=True),
            "forbidden",
        ),
        (DatabaseUnavailableError("admin db down"), "unavailable"),
    ],
    ids=["refused", "permission-revoked", "password-rotation-required", "unavailable"],
)
def test_failed_reverification_ends_with_stream_closed(verdict: Verdict, reason: str) -> None:
    # The first recheck passes, the second fails: the stream ends at that poll.
    client, stream, calls = _app([_OPENING, verdict], polls=5, recheck_seconds=_POLL_SECONDS)

    frames = _get(client)

    assert len(calls) == 2
    assert frames[-1] == ("stream_closed", {"type": "stream_closed", "reason": reason})
    assert [name for name, _ in frames[:-1]] == ["heartbeat"]
    assert len(stream.scoped_by) == 1


def test_stream_that_ends_on_its_own_sends_no_stream_closed() -> None:
    client, stream, _ = _app([_OPENING], polls=2, recheck_seconds=_POLL_SECONDS)

    assert [name for name, _ in _get(client)] == ["heartbeat", "heartbeat"]
    assert stream.closed


def test_unreadable_event_store_ends_with_stream_closed_unavailable() -> None:
    client, stream, _ = _app([_OPENING], polls=5, recheck_seconds=_POLL_SECONDS, store_down_after=1)

    frames = _get(client)

    assert [name for name, _ in frames] == ["heartbeat", "stream_closed"]
    assert frames[-1][1] == {"type": "stream_closed", "reason": "unavailable"}
    assert stream.closed


async def test_client_disconnect_ends_the_stream_without_stream_closed() -> None:
    client, stream, _ = _app([_OPENING], polls=1000, recheck_seconds=_POLL_SECONDS)
    received = 0

    async def _receive() -> dict[str, Any]:
        # The first message carries the request; every later one is the
        # disconnect, seen by the stream's per-item disconnect check.
        nonlocal received
        received += 1
        if received == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    body = bytearray()

    async def _send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/events/stream",
        "raw_path": b"/events/stream",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer tok_live"), (b"host", b"testserver")],
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(client.app(scope, _receive, _send), timeout=5)

    assert b"stream_closed" not in body
    assert len(stream.scoped_by) < 1000, "the disconnect ends the stream early"
    assert stream.closed, "the service generator is closed on disconnect"


def test_valid_outcome_without_identity_fails_closed_as_refused() -> None:
    client, _, _ = _app([_OPENING], polls=3, recheck_seconds=_POLL_SECONDS)

    async def _recheck(**_: Any) -> CredentialRecheckOutcome:
        return CredentialRecheckOutcome(CredentialRecheckResult.VALID, None)

    client.app.dependency_overrides[get_credential_recheck] = lambda: _recheck  # type: ignore[attr-defined]

    frames = _get(client)

    assert frames == [("stream_closed", {"type": "stream_closed", "reason": "refused"})]


def test_interval_defaults_to_configured_value() -> None:
    # No override of the interval dependency: it reads ``admin.events`` off the context.
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            admin=AdminConfig(events=AdminEventsConfig(stream_recheck_seconds=10.0))
        )
    )
    client, _, calls = _app([_OPENING], polls=4, recheck_seconds=None)
    client.app.dependency_overrides[get_ctx] = lambda: ctx  # type: ignore[attr-defined]

    _get(client)

    # Polls land at +5, +10, +15, +20: a 10 s configured interval re-verifies twice.
    assert calls == ["tok_live", "tok_live"]
