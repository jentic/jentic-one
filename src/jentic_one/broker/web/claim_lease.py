"""The Idempotency-Key claim a buffered request holds until it has an outcome.

A keyed buffered request claims its key (``SharedStateIdempotencyStore.begin``)
before it runs. When the request ends without the normal ``complete`` of the
upstream response, :class:`ClaimLease` decides what happens to the claim, keyed
on whether the upstream may have acted:

- **Release** (the same-key retry runs again at once) when nothing reached the
  upstream: the request failed before the upstream call started (credential
  resolution or injection errors, post-injection URL validation, cancellation),
  or the envelope proved the call never left the broker
  (:func:`~jentic_one.broker.core.exceptions.upstream_contact_ruled_out`:
  connect-phase failures, an open circuit, an unavailable runner, a deadline
  that ran out before any send, with no earlier retry attempt that may have
  been sent).
- **Store and replay** when the upstream may have acted and there is a response
  to record: a ``BrokerError`` after send (read timeout, dropped connection,
  oversized response, a deadline that cut off a send) stores its problem+json,
  and an upstream answer whose persistence or commit failed stores that answer.
  The same-key retry replays it (``Idempotent-Replayed: true``) instead of
  re-running the call; a new key is the explicit way to run it again.
- **Hold** to ``pending_ttl_s`` when the upstream may have acted but there is
  nothing to record: cancellation or a non-``BrokerError`` once the upstream
  call started. The retry gets ``409 idempotency_in_progress`` until the claim
  lapses.

Once the caller has the upstream response and calls :meth:`ClaimLease.settle`,
storing it is the caller's job: a later failure (including of that store
write) leaves the claim to its pending TTL and the lease records nothing.

Every store operation here is shielded so a cancelled request still finishes it,
runs as a task the module keeps a strong reference to until it is done, and
never raises: a store failure is logged by exception class only (a store error
message can carry its connection URL) and the claim falls back to its pending
TTL, while the request's own error keeps propagating.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from enum import StrEnum
from typing import Any

import structlog
from fastapi import Response

from jentic_one.broker.core.exceptions import BrokerError, upstream_contact_ruled_out
from jentic_one.broker.services.execution.pipeline import ExecutionOutcome
from jentic_one.broker.services.idempotency import IdempotencyClaim, SharedStateIdempotencyStore
from jentic_one.broker.web.errors import broker_error_response
from jentic_one.shared.metrics import get_meter

logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
_failed_claims_total = _meter.create_counter(
    "broker.idempotency.failed_request_claims_total",
    description=(
        "Idempotency-Key claims of requests that ended without storing their upstream "
        "response, by what happened to the claim."
    ),
)

# Store work still running after its request was cancelled; held so the task is
# not garbage-collected mid-flight, discarded when it finishes.
_IN_FLIGHT: set[asyncio.Task[None]] = set()


class ClaimOutcome(StrEnum):
    """What happened to the claim of a request that ended without ``complete``."""

    RELEASED = "released"
    """Freed: the same-key retry runs again."""
    STALE = "stale"
    """Already gone or re-claimed by another request (expired); nothing freed."""
    REPLAY_STORED = "replay_stored"
    """The failure (or the unpersisted upstream answer) was stored for replay."""
    HELD = "held"
    """Kept until ``pending_ttl_s``: the upstream may have acted, nothing to store."""
    STORE_ERROR = "store_error"
    """The store failed; the claim falls back to ``pending_ttl_s``."""


class _Phase(StrEnum):
    IDLE = "idle"
    HELD = "held"
    DISPATCHED = "dispatched"
    FAILED_BEFORE_SEND = "failed_before_send"
    FAILED_AFTER_SEND = "failed_after_send"
    ANSWERED = "answered"
    SETTLED = "settled"


class ClaimLease:
    """Tracks one request's claim from ``FRESH`` to its outcome (see module docstring).

    Doubles as the ``UpstreamCallObserver`` handed to ``run_execution``, which
    reports how the upstream leg ended before the record is persisted.
    """

    __slots__ = ("_claim", "_error", "_fingerprint", "_outcome", "_phase", "_render", "_store")

    def __init__(self) -> None:
        self._phase = _Phase.IDLE
        self._store: SharedStateIdempotencyStore | None = None
        self._claim: IdempotencyClaim | None = None
        self._fingerprint = ""
        self._render: Callable[[ExecutionOutcome], Response] | None = None
        self._outcome: ExecutionOutcome | None = None
        self._error: BrokerError | None = None

    def hold(
        self, store: SharedStateIdempotencyStore, claim: IdempotencyClaim, fingerprint: str
    ) -> None:
        """Own a ``FRESH`` claim; a failure before :meth:`dispatching` releases it."""
        self._store, self._claim, self._fingerprint = store, claim, fingerprint
        self._phase = _Phase.HELD

    def dispatching(self, render: Callable[[ExecutionOutcome], Response]) -> None:
        """The upstream call is about to start; ``render`` assembles its response."""
        if self._phase is _Phase.HELD:
            self._render = render
            self._phase = _Phase.DISPATCHED

    def upstream_returned(self, outcome: ExecutionOutcome) -> None:
        if self._phase is _Phase.DISPATCHED:
            self._outcome = outcome
            self._phase = _Phase.ANSWERED

    def upstream_failed(self, error: BrokerError) -> None:
        if self._phase is _Phase.DISPATCHED:
            self._error = error
            self._phase = (
                _Phase.FAILED_BEFORE_SEND
                if upstream_contact_ruled_out(error)
                else _Phase.FAILED_AFTER_SEND
            )

    def settle(self) -> None:
        """The caller stores the response itself; a later :meth:`abandon` is a no-op."""
        self._phase = _Phase.SETTLED

    async def abandon(self, exc: BaseException) -> None:
        """Settle the claim of a request that is ending with ``exc``; never raises over it."""
        phase, store, claim = self._phase, self._store, self._claim
        self._phase = _Phase.SETTLED
        if store is None or claim is None or phase is _Phase.SETTLED:
            return
        error_type = type(exc).__name__
        if phase in (_Phase.HELD, _Phase.FAILED_BEFORE_SEND):
            await _shielded(_release(store, claim, error_type))
            return
        replay = self._replay_for(phase)
        if replay is not None:
            response, body = replay
            await _shielded(
                _store_replay(store, claim, self._fingerprint, response, body, error_type)
            )
        else:
            _record(ClaimOutcome.HELD, error_type)

    def _replay_for(self, phase: _Phase) -> tuple[Response, bytes] | None:
        """The response (and body to store) a same-key retry replays, if there is one."""
        try:
            if phase is _Phase.FAILED_AFTER_SEND and self._error is not None:
                problem = broker_error_response(self._error)
                return problem, bytes(problem.body)
            if phase is _Phase.ANSWERED and self._outcome is not None and self._render is not None:
                return self._render(self._outcome), self._outcome.result.body
        except Exception as exc:
            # Rendering is what just failed the request; hold the claim instead.
            logger.warning("idempotency_replay_render_failed", error_type=type(exc).__name__)
        return None


async def _shielded(work: Coroutine[Any, Any, None]) -> None:
    """Run ``work`` to completion even if the awaiting request is cancelled."""
    task = asyncio.ensure_future(work)
    _IN_FLIGHT.add(task)
    task.add_done_callback(_IN_FLIGHT.discard)
    await asyncio.shield(task)


async def _release(
    store: SharedStateIdempotencyStore, claim: IdempotencyClaim, error_type: str
) -> None:
    try:
        released = await store.release(claim)
    except Exception as exc:
        _store_failed("release", exc)
        return
    _record(ClaimOutcome.RELEASED if released else ClaimOutcome.STALE, error_type)


async def _store_replay(
    store: SharedStateIdempotencyStore,
    claim: IdempotencyClaim,
    fingerprint: str,
    response: Response,
    body: bytes,
    error_type: str,
) -> None:
    try:
        await store.complete(
            claim.caller,
            claim.key,
            fingerprint,
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body,
        )
    except Exception as exc:
        _store_failed("store_replay", exc)
        return
    _record(ClaimOutcome.REPLAY_STORED, error_type)


def _record(outcome: ClaimOutcome, error_type: str) -> None:
    _failed_claims_total.add(1, {"outcome": outcome.value})
    logger.info(
        "idempotency_claim_settled_on_failure", outcome=outcome.value, error_type=error_type
    )


def _store_failed(action: str, exc: Exception) -> None:
    _failed_claims_total.add(1, {"outcome": ClaimOutcome.STORE_ERROR.value})
    # Class name only: a store error message can carry its connection URL.
    logger.warning("idempotency_claim_settle_failed", action=action, error_type=type(exc).__name__)


__all__ = ["ClaimLease", "ClaimOutcome"]
