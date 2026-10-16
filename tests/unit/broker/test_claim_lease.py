"""Unit tests for ``ClaimLease`` — the claim settlement of a failed keyed request (#1710).

Runs over the real ``SharedStateIdempotencyStore`` and in-process memory backend
(no DB, no app wiring). The router-level policy is pinned end to end in
``tests/integration/broker/test_idempotency_claim_release.py``; this module pins
the lease's own invariants: a cancelled request still finishes its store work, a
failing store never raises over the request, and the phase decides the action.
"""

from __future__ import annotations

import asyncio

import pytest

from jentic_one.broker.core.exceptions import CircuitOpenError, UpstreamTimeoutError
from jentic_one.broker.services.idempotency import (
    IdempotencyState,
    SharedStateIdempotencyStore,
)
from jentic_one.broker.web import claim_lease
from jentic_one.broker.web.claim_lease import ClaimLease
from jentic_one.shared.state.backend import MemoryStateBackend

_CALLER = "agnt_lease"
_KEY = "key-1"
_FP = "fp-1"


class _GatedBackend(MemoryStateBackend):
    """A memory backend whose compare-and-delete waits on a gate (a slow store)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def delete_if_equals(self, key: str, expected: bytes) -> bool:
        self.entered.set()
        await self.gate.wait()
        return await super().delete_if_equals(key, expected)


class _FailingBackend(MemoryStateBackend):
    async def delete_if_equals(self, key: str, expected: bytes) -> bool:
        raise ConnectionError("redis://user:secret@host unreachable")  # pragma: allowlist secret


def _store(backend: MemoryStateBackend) -> SharedStateIdempotencyStore:
    return SharedStateIdempotencyStore(
        backend, pending_ttl_s=35.0, done_ttl_s=86_400.0, max_response_bytes=1024
    )


async def _held_lease(store: SharedStateIdempotencyStore) -> ClaimLease:
    outcome = await store.begin(_CALLER, _KEY, _FP)
    assert outcome.claim is not None
    lease = ClaimLease()
    lease.hold(store, outcome.claim, _FP)
    return lease


async def _state(store: SharedStateIdempotencyStore) -> IdempotencyState:
    return (await store.begin(_CALLER, _KEY, _FP)).state


@pytest.mark.asyncio
async def test_cancelled_request_still_finishes_the_release() -> None:
    """A second cancellation while releasing detaches the release, which still completes."""
    backend = _GatedBackend()
    store = _store(backend)
    lease = await _held_lease(store)

    abandoning = asyncio.create_task(lease.abandon(asyncio.CancelledError()))
    await backend.entered.wait()
    abandoning.cancel()
    with pytest.raises(asyncio.CancelledError):
        await abandoning
    assert len(claim_lease._IN_FLIGHT) == 1

    backend.gate.set()
    while claim_lease._IN_FLIGHT:
        await asyncio.sleep(0)
    assert await _state(store) is IdempotencyState.FRESH


@pytest.mark.asyncio
async def test_store_failure_never_raises_over_the_request() -> None:
    lease = await _held_lease(_store(_FailingBackend()))

    await lease.abandon(RuntimeError("original"))

    assert not claim_lease._IN_FLIGHT


@pytest.mark.asyncio
async def test_pre_send_upstream_failure_releases() -> None:
    store = _store(MemoryStateBackend())
    lease = await _held_lease(store)
    lease.dispatching(lambda _outcome: pytest.fail("no upstream answer to render"))
    error = CircuitOpenError(detail="open", type="circuit_open")
    lease.upstream_failed(error)

    await lease.abandon(error)

    assert await _state(store) is IdempotencyState.FRESH


@pytest.mark.asyncio
async def test_post_send_upstream_failure_stores_its_problem_for_replay() -> None:
    store = _store(MemoryStateBackend())
    lease = await _held_lease(store)
    lease.dispatching(lambda _outcome: pytest.fail("no upstream answer to render"))
    error = UpstreamTimeoutError(detail="read", type="upstream_timeout")
    lease.upstream_failed(error)

    await lease.abandon(error)

    replay = await store.begin(_CALLER, _KEY, _FP)
    assert replay.state is IdempotencyState.REPLAY
    assert replay.stored is not None
    assert replay.stored.status_code == 504
    assert b"upstream_timeout" in replay.stored.body


@pytest.mark.asyncio
async def test_failure_during_the_upstream_call_holds() -> None:
    store = _store(MemoryStateBackend())
    lease = await _held_lease(store)
    lease.dispatching(lambda _outcome: pytest.fail("no upstream answer to render"))

    await lease.abandon(asyncio.CancelledError())

    assert await _state(store) is IdempotencyState.IN_PROGRESS


@pytest.mark.asyncio
async def test_settled_lease_does_nothing() -> None:
    store = _store(MemoryStateBackend())
    lease = await _held_lease(store)
    lease.settle()

    await lease.abandon(RuntimeError("after settle"))

    assert await _state(store) is IdempotencyState.IN_PROGRESS
