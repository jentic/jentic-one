"""Unit tests for the ``Idempotency-Key`` store (§07).

Exercises the full claim/replay state machine over the in-process memory backend
(the same code path the Redis backend runs cross-instance):

- first claim is ``FRESH``; a replay after ``complete`` re-emits the stored response,
- a different fingerprint under the same key is ``CONFLICT``,
- a retry while the original is still pending is ``IN_PROGRESS`` with a ``Retry-After``,
- callers are isolated (one caller's key never replays another's),
- a short pending TTL frees a crashed claim before the long replay window,
- sensitive response headers are scrubbed and oversized bodies are dropped,
- the memory backend's byte budget tracks the real memory it allocates.
"""

from __future__ import annotations

import gc
import json
import tracemalloc

import pytest
import structlog

from jentic_one.broker.core.exceptions import IdempotencyUnavailableError
from jentic_one.broker.core.problem import status_for_broker_error
from jentic_one.broker.services.idempotency import (
    MAX_RECORD_HEADER_BYTES,
    IdempotencyState,
    SharedStateIdempotencyStore,
)
from jentic_one.shared.config import IdempotencyConfig
from jentic_one.shared.state import BackendKind, MemoryBackendConfig
from jentic_one.shared.state.backend import MemoryStateBackend, NamespaceBudget


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


async def _claim_bytes(caller: str, key: str, fp: str) -> int:
    """Budgeted bytes of one PENDING claim, measured on a scratch backend."""
    scratch = MemoryStateBackend(clock=_Clock())
    await _store(scratch).begin(caller, key, fp)
    return scratch._usage["idem"].bytes


@pytest.mark.asyncio
async def test_a_retry_after_the_claim_ttl_does_not_rerun_when_the_record_does_not_fit() -> None:
    """Once the upstream call ran, some record always replaces the claim.

    The ``idem`` budget holds exactly the claim, so neither the full record
    nor a body-less one fits; the status-only tombstone, never larger than the
    claim, does. A retry after ``pending_ttl_s`` must replay, not run again.
    """
    clock = _Clock()
    budget = await _claim_bytes("agent-1", "k", "fp")
    backend = MemoryStateBackend(
        clock=clock, namespace_budgets={"idem": NamespaceBudget(max_entries=10, max_bytes=budget)}
    )
    store = _store(backend, pending_ttl_s=35.0, max_response_bytes=100_000)
    assert (await store.begin("agent-1", "k", "fp")).state is IdempotencyState.FRESH

    with structlog.testing.capture_logs() as logs:
        await store.complete(
            "agent-1", "k", "fp", status_code=201, headers={"h": "v" * 200}, body=b"x" * 50_000
        )

    assert "idempotency_record_stored_as_tombstone" in {entry["event"] for entry in logs}
    clock.advance(36.0)
    retry = await store.begin("agent-1", "k", "fp")
    assert retry.state is IdempotencyState.REPLAY
    assert retry.stored is not None
    assert retry.stored.status_code == 201
    assert retry.stored.body_omitted is True


@pytest.mark.asyncio
async def test_a_full_body_budget_drops_bodies_but_never_refuses_a_claim() -> None:
    backend = MemoryStateBackend(
        clock=_Clock(),
        namespace_budgets={"idem-body": NamespaceBudget(max_entries=1_000, max_bytes=2_000)},
    )
    store = _store(backend, max_response_bytes=10_000)
    for i in range(5):
        assert (await store.begin("agent-1", f"k{i}", "fp")).state is IdempotencyState.FRESH
        await store.complete(
            "agent-1", f"k{i}", "fp", status_code=200, headers={}, body=b"b" * 1_000
        )

    first = await store.begin("agent-1", "k0", "fp")
    last = await store.begin("agent-1", "k4", "fp")
    assert first.stored is not None and first.stored.body == b"b" * 1_000
    assert last.stored is not None and last.stored.body_omitted is True
    assert (await store.begin("agent-1", "next", "fp")).state is IdempotencyState.FRESH


@pytest.mark.asyncio
async def test_one_caller_over_its_share_does_not_lock_out_another() -> None:
    backend = MemoryStateBackend(
        clock=_Clock(),
        namespace_budgets={
            "idem": NamespaceBudget(max_entries=20, max_bytes=10**9, max_owner_share=0.1)
        },
    )
    store = _store(backend)
    for i in range(2):
        assert (await store.begin("agent-1", f"k{i}", "fp")).state is IdempotencyState.FRESH
    with pytest.raises(IdempotencyUnavailableError):
        await store.begin("agent-1", "k2", "fp")

    assert (await store.begin("agent-2", "k0", "fp")).state is IdempotencyState.FRESH


@pytest.mark.asyncio
async def test_a_colon_in_the_caller_does_not_merge_owner_shares() -> None:
    backend = MemoryStateBackend(
        clock=_Clock(),
        namespace_budgets={
            "idem": NamespaceBudget(max_entries=10, max_bytes=10**9, max_owner_share=0.1)
        },
    )
    store = _store(backend)
    assert (await store.begin("urn:a", "k", "fp")).state is IdempotencyState.FRESH
    assert (await store.begin("urn:b", "k", "fp")).state is IdempotencyState.FRESH


@pytest.mark.asyncio
async def test_long_headers_are_capped_so_records_stay_bounded() -> None:
    store = _store(MemoryStateBackend(clock=_Clock()))
    await store.begin("agent-1", "k", "fp")
    headers = {f"x-h{i}": "v" * 100 for i in range(20)} | {"content-type": "text/plain"}
    await store.complete("agent-1", "k", "fp", status_code=200, headers=headers, body=b"ok")

    replay = await store.begin("agent-1", "k", "fp")
    assert replay.stored is not None
    assert replay.stored.headers["content-type"] == "text/plain"
    assert len(json.dumps(replay.stored.headers, separators=(",", ":"))) <= MAX_RECORD_HEADER_BYTES
    assert replay.stored.body == b"ok"


_TYPICAL_HEADERS = {
    "content-type": "application/json",
    "content-length": "1024",
    "date": "Sat, 17 Oct 2026 00:00:00 GMT",
    "server": "nginx",
    "x-request-id": "0f8fad5b-d9cb-469f-a165-70867728950e",
    "jentic-execution-id": "exec_" + "e" * 27,
    "jentic-upstream-status": "200",
}
_FILL_BUDGET_BYTES = 12 * 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(("filled", "body_len"), [("idem", 0), ("idem-body", 1024)])
async def test_the_byte_budget_tracks_real_memory(filled: str, body_len: int) -> None:
    """Filled to its budget, a namespace holds about the memory the budget says.

    Runs typical requests (20 callers, seven response headers, a claim then
    a record each) until ``filled`` refuses, then compares the bytes
    ``tracemalloc`` traces for the whole backend with its budgeted bytes. An
    understated ``ENTRY_OVERHEAD_BYTES`` would let a memory limit sized from
    the budgets be OOM-killed before the store ever refuses; an overstated
    one refuses requests the memory could hold.
    """
    clock = _Clock()
    unbounded = NamespaceBudget(max_entries=10**8, max_bytes=10**12, max_owner_share=0.1)
    full = NamespaceBudget(max_entries=10**8, max_bytes=_FILL_BUDGET_BYTES, max_owner_share=0.1)
    budgets = {"idem": unbounded, "idem-body": unbounded, filled: full}
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        backend = MemoryStateBackend(clock=clock, namespace_budgets=budgets, max_total_bytes=10**12)
        store = _store(backend, done_ttl_s=3_600.0, max_response_bytes=1_000_000)
        usage = backend._usage
        for i in range(10**6):
            caller, key, fp = f"agnt_{i % 20:027d}", f"{i:036d}", f"{i:064x}"
            try:
                await store.begin(caller, key, fp)
            except IdempotencyUnavailableError:
                break
            body_bytes = usage["idem-body"].bytes if "idem-body" in usage else 0
            await store.complete(
                caller,
                key,
                fp,
                status_code=200,
                headers=dict(_TYPICAL_HEADERS),
                body=bytes(bytearray(body_len)),
            )
            if body_len and usage["idem-body"].bytes == body_bytes:
                break
            clock.advance(0.001)
        gc.collect()
        real = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()

    assert usage[filled].bytes >= 0.99 * _FILL_BUDGET_BYTES
    assert 0.85 <= real / backend._total_bytes <= 1.15


@pytest.mark.asyncio
async def test_default_budgets_hold_the_documented_sustained_rate() -> None:
    """The built-in budgets hold about 30 req/s (3 per caller) for the 1 h memory TTL.

    Uses the budgeted size of a typical record and 1 KiB body, which
    ``test_the_byte_budget_tracks_real_memory`` keeps within 15% of real
    memory, and checks the built-in budgets fit the backend-wide cap.
    """
    backend = MemoryStateBackend(clock=_Clock())
    store = _store(backend, max_response_bytes=256 * 1024)
    caller, key = "agnt_" + "a" * 27, "0f8fad5b-d9cb-469f-a165-70867728950e"
    fp = "f" * 64
    await store.begin(caller, key, fp)
    await store.complete(
        caller, key, fp, status_code=200, headers=_TYPICAL_HEADERS, body=b"x" * 1024
    )
    record_bytes = backend._usage["idem"].bytes
    body_bytes = backend._usage["idem-body"].bytes

    memory = MemoryBackendConfig()
    budgets = memory.budgets()
    ttl_s = IdempotencyConfig().replay_ttl_s(BackendKind.MEMORY)
    assert ttl_s == 3_600
    idem, body = budgets["idem"], budgets["idem-body"]
    records_per_s = min(idem.max_entries, idem.max_bytes // record_bytes) / ttl_s
    bodies_per_s = min(body.max_entries, body.max_bytes // body_bytes) / ttl_s
    assert 28 <= records_per_s <= 32
    assert 4.5 <= bodies_per_s <= 5.5
    assert idem.max_owner_share is not None
    assert records_per_s * idem.max_owner_share >= 2.8
    # idem, idem-body and one default-sized namespace (the breaker's) fit the cap.
    assert idem.max_bytes + body.max_bytes + memory.max_bytes_per_namespace <= (
        memory.max_total_bytes
    )
