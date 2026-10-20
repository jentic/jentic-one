"""``GET /events/stream`` re-verifies its caller while open (#1836).

A fake clock drives the interval, a scripted verifier stands in for the token
chain, and a fake stream service calls ``refresh_identity`` before each poll
the way :class:`EventStreamService` does. This pins:

* no re-verification before the interval elapses, one per interval after;
* the refreshed identity is what scopes the next poll;
* a refused credential, a revoked permission, or a verifier that cannot answer
  each ends the stream with a ``stream_closed`` frame naming the reason.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from jentic_one.shared.config import AdminConfig
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.web.deps import resolve_identity

_OPENING = Identity(sub="usr_1", permissions=["events:read", "org:admin"])
_POLL_SECONDS = 5.0


@dataclass
class _Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _FakeStream:
    """Polls ``polls`` times, advancing the clock one poll interval before each."""

    clock: _Clock
    polls: int
    scoped_by: list[Identity] = field(default_factory=list)

    def stream(self, *, refresh_identity: IdentityRefresh, **_: Any) -> AsyncIterator[Heartbeat]:
        async def _gen() -> AsyncIterator[Heartbeat]:
            for _ in range(self.polls):
                self.clock.now += _POLL_SECONDS
                identity = await refresh_identity()
                if identity is None:
                    return
                self.scoped_by.append(identity)
                yield Heartbeat(sent_at=datetime.now(UTC))

        return _gen()


Verdict = Identity | Exception


def _app(
    verdicts: list[Verdict], *, polls: int, recheck_seconds: float
) -> tuple[TestClient, _FakeStream, list[str]]:
    clock = _Clock()
    stream = _FakeStream(clock=clock, polls=polls)
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
        (DatabaseUnavailableError("admin db down"), "unavailable"),
    ],
    ids=["refused", "permission-revoked", "unavailable"],
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
    client, _, _ = _app([_OPENING], polls=2, recheck_seconds=_POLL_SECONDS)

    assert [name for name, _ in _get(client)] == ["heartbeat", "heartbeat"]


def test_default_interval_comes_from_config() -> None:
    assert AdminConfig().events.stream_recheck_seconds == 5.0
