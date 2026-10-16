"""Unit tests for the ``Idempotency-Key`` store (§07).

Exercises the full claim/replay state machine over the in-process memory backend
(the same code path the Redis backend runs cross-instance):

- first claim is ``FRESH``; a replay after ``complete`` re-emits the stored response,
- a different fingerprint under the same key is ``CONFLICT``,
- a retry while the original is still pending is ``IN_PROGRESS`` with a ``Retry-After``,
- callers are isolated (one caller's key never replays another's),
- a short pending TTL frees a crashed claim before the long replay window,
- sensitive response headers are scrubbed and oversized bodies are dropped.
"""

from __future__ import annotations

import pytest
import structlog

from jentic_one.broker.core.exceptions import IdempotencyUnavailableError
from jentic_one.broker.core.problem import status_for_broker_error
from jentic_one.broker.services.idempotency import (
    IdempotencyState,
    SharedStateIdempotencyStore,
)
from jentic_one.shared.state.backend import MemoryStateBackend


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(
    backend: MemoryStateBackend,
    *,
    pending_ttl_s: float = 35.0,
    done_ttl_s: float = 86_400.0,
    max_response_bytes: int = 1024,
) -> SharedStateIdempotencyStore:
    return SharedStateIdempotencyStore(
        backend,
        pending_ttl_s=pending_ttl_s,
        done_ttl_s=done_ttl_s,
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.asyncio
async def test_fresh_then_replay() -> None:
    store = _store(MemoryStateBackend(clock=_Clock()))

    first = await store.begin("agent-1", "key-1", "fp-a")
    assert first.state is IdempotencyState.FRESH

    await store.complete(
        "agent-1",
        "key-1",
        "fp-a",
        status_code=201,
        headers={"content-type": "application/json"},
        body=b'{"id":42}',
    )

    replay = await store.begin("agent-1", "key-1", "fp-a")
    assert replay.state is IdempotencyState.REPLAY
    assert replay.stored is not None
    assert replay.stored.status_code == 201
    assert replay.stored.body == b'{"id":42}'
    assert replay.stored.headers["content-type"] == "application/json"
    assert replay.stored.body_omitted is False


@pytest.mark.asyncio
async def test_same_key_different_fingerprint_conflicts() -> None:
    store = _store(MemoryStateBackend(clock=_Clock()))

    assert (await store.begin("agent-1", "key-1", "fp-a")).state is IdempotencyState.FRESH
    conflict = await store.begin("agent-1", "key-1", "fp-DIFFERENT")
    assert conflict.state is IdempotencyState.CONFLICT


@pytest.mark.asyncio
async def test_retry_while_pending_is_in_progress() -> None:
    store = _store(MemoryStateBackend(clock=_Clock()), pending_ttl_s=30.0)

    assert (await store.begin("agent-1", "key-1", "fp-a")).state is IdempotencyState.FRESH
    in_progress = await store.begin("agent-1", "key-1", "fp-a")
    assert in_progress.state is IdempotencyState.IN_PROGRESS
    assert in_progress.retry_after_s == 30


@pytest.mark.asyncio
async def test_callers_are_isolated() -> None:
    store = _store(MemoryStateBackend(clock=_Clock()))

    await store.begin("agent-1", "shared-key", "fp-a")
    await store.complete("agent-1", "shared-key", "fp-a", status_code=200, headers={}, body=b"one")

    # A different caller using the same key string sees a fresh slot.
    other = await store.begin("agent-2", "shared-key", "fp-b")
    assert other.state is IdempotencyState.FRESH


@pytest.mark.asyncio
async def test_pending_claim_expires_before_replay_window() -> None:
    clock = _Clock()
    store = _store(MemoryStateBackend(clock=clock), pending_ttl_s=10.0)

    # Claim then "crash" before complete; after the short pending TTL the slot
    # frees and the retry re-claims it FRESH (not stuck for the 24h window).
    assert (await store.begin("agent-1", "key-1", "fp-a")).state is IdempotencyState.FRESH
    clock.advance(11.0)
    assert (await store.begin("agent-1", "key-1", "fp-a")).state is IdempotencyState.FRESH


@pytest.mark.asyncio
async def test_sensitive_headers_scrubbed_on_store() -> None:
    store = _store(MemoryStateBackend(clock=_Clock()))

    await store.begin("agent-1", "key-1", "fp-a")
    await store.complete(
        "agent-1",
        "key-1",
        "fp-a",
        status_code=200,
        headers={
            "content-type": "application/json",
            "Set-Cookie": "session=abc",
            "Authorization": "Bearer secret",
        },
        body=b"{}",
    )

    replay = await store.begin("agent-1", "key-1", "fp-a")
    assert replay.stored is not None
    assert "content-type" in replay.stored.headers
    assert "Set-Cookie" not in replay.stored.headers
    assert "Authorization" not in replay.stored.headers


@pytest.mark.asyncio
async def test_oversized_body_omitted_but_still_replays() -> None:
    store = _store(MemoryStateBackend(clock=_Clock()), max_response_bytes=8)

    await store.begin("agent-1", "key-1", "fp-a")
    await store.complete(
        "agent-1",
        "key-1",
        "fp-a",
        status_code=200,
        headers={"content-type": "application/json"},
        body=b"this body is definitely longer than eight bytes",
    )

    replay = await store.begin("agent-1", "key-1", "fp-a")
    assert replay.state is IdempotencyState.REPLAY
    assert replay.stored is not None
    assert replay.stored.body_omitted is True
    assert replay.stored.body == b""
    # Status/headers still replay so the side-effect isn't repeated.
    assert replay.stored.status_code == 200


@pytest.mark.asyncio
async def test_in_flight_retry_is_never_fresh_when_the_store_is_full() -> None:
    """A full store refuses new claims; it never drops a live one.

    Evicting the in-flight claim to make room would let the client's retry
    claim the key ``FRESH`` and run the upstream call a second time.
    """
    backend = MemoryStateBackend(clock=_Clock(), max_entries=10)
    store = _store(backend)
    assert (await store.begin("agent-1", "in-flight", "fp")).state is IdempotencyState.FRESH
    for i in range(9):
        assert (await store.begin("agent-1", f"other-{i}", "fp")).state is IdempotencyState.FRESH

    with pytest.raises(IdempotencyUnavailableError) as refused:
        await store.begin("agent-1", "overflow", "fp")
    assert status_for_broker_error(refused.value) == 503
    assert int(refused.value.headers["Retry-After"]) >= 1

    retry = await store.begin("agent-1", "in-flight", "fp")
    assert retry.state is IdempotencyState.IN_PROGRESS


@pytest.mark.asyncio
async def test_complete_stores_the_record_without_its_body_when_the_body_does_not_fit() -> None:
    backend = MemoryStateBackend(clock=_Clock(), max_bytes=1_500)
    store = _store(backend, max_response_bytes=10_000)
    assert (await store.begin("agent-1", "key-1", "fp")).state is IdempotencyState.FRESH

    await store.complete(
        "agent-1", "key-1", "fp", status_code=201, headers={"x": "1"}, body=b"b" * 2_000
    )

    replay = await store.begin("agent-1", "key-1", "fp")
    assert replay.state is IdempotencyState.REPLAY
    assert replay.stored is not None
    assert replay.stored.status_code == 201
    assert replay.stored.body_omitted is True


@pytest.mark.asyncio
async def test_complete_keeps_the_pending_claim_when_nothing_fits() -> None:
    backend = MemoryStateBackend(clock=_Clock(), max_bytes=300)
    store = _store(backend)
    assert (await store.begin("agent-1", "k", "fp")).state is IdempotencyState.FRESH

    with structlog.testing.capture_logs() as logs:
        await store.complete(
            "agent-1", "k", "fp", status_code=200, headers={"h": "v" * 200}, body=b"x"
        )

    assert "idempotency_record_not_stored" in {entry["event"] for entry in logs}
    assert (await store.begin("agent-1", "k", "fp")).state is IdempotencyState.IN_PROGRESS
