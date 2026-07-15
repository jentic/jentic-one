"""Transport-neutral broker execution value objects.

These are the request/result/context/outcome value objects and runner protocols
that make up the public :class:`~jentic_one.shared.broker.broker.Broker` contract.
They live in ``shared/broker`` — not ``broker/`` — so both the broker surface and
any downstream implementation depend on the *same* value types without ``shared``
importing ``broker`` (forbidden by ``tests/arch/test_module_boundaries.py``).

The concrete broker pipeline (``broker/services/execution/pipeline.py``), the
runner adapters (``broker/adapters/runners/base.py``), and the streaming web edge
(``broker/web/streaming.py``) re-export these names, so existing broker-internal
call sites keep importing them from their old modules unchanged; this module is
the single definition.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from jentic_one.shared.schemas import APIReference


class ErrorOrigin(StrEnum):
    """Disambiguates *who* failed on an otherwise-identical 5xx/503."""

    BROKER = "broker"
    """The broker/system failed (overloaded, misconfigured, bad request shape)."""

    UPSTREAM = "upstream"
    """The external vendor failed (down, rate-limited, returned an error)."""


@dataclass(frozen=True, slots=True)
class RunnerRequest:
    """A transport-agnostic upstream request handed to a runner."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout_s: float = 30.0


@dataclass(frozen=True, slots=True)
class RunnerResult:
    """The runner's response — the real upstream status/headers/body, verbatim."""

    status_code: int
    body: bytes
    headers: dict[str, str]
    content_type: str | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class StreamingResult:
    """A streamed upstream response: status/headers known up front, body lazy.

    Unlike :class:`RunnerResult` the body is **not** materialised — ``aiter`` is
    the raw (still-compressed) upstream byte stream, consumed by the web edge to
    feed a ``StreamingResponse``. It is only valid for the lifetime of the
    ``stream()`` context manager that produced it; the upstream connection is
    torn down when that context exits.
    """

    status_code: int
    headers: dict[str, str]
    content_type: str | None
    aiter: AsyncIterator[bytes]


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Identity + discovery metadata threaded through the pipeline for one call."""

    execution_id: str
    toolkit_id: str | None
    operation_id: str | None
    api: APIReference | None
    trace_id: str


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """The immutable result a post-response stage may enrich (never the 2xx body)."""

    result: RunnerResult
    context: ExecutionContext
    error_origin: ErrorOrigin | None = None


@dataclass
class StreamingOutcome:
    """Captures the final state of a streaming execution for persistence."""

    execution_id: str
    http_status: int
    started_at_perf: float = field(default_factory=time.perf_counter)
    duration_ms: int = 0
    error: str | None = None
    bytes_transferred: int = 0


@runtime_checkable
class UpstreamRunner(Protocol):
    """Executes a single upstream request and returns its verbatim result.

    Decorators (retry/circuit/deadline/idempotency) implement this same protocol
    and wrap a base runner, so the pipeline composes them without the handler
    knowing which capabilities are active.
    """

    async def run(self, request: RunnerRequest) -> RunnerResult: ...


@runtime_checkable
class StreamingUpstreamRunner(UpstreamRunner, Protocol):
    """An ``UpstreamRunner`` that can also stream the body without buffering (§08 E2.4).

    ``stream`` is an **async context manager**: it dispatches the request, yields
    a :class:`StreamingResult` once the status/headers are in, and — critically —
    holds the upstream ``httpx`` response open only for the duration of the
    ``async with``. When the context exits (normal completion, size-cap/deadline
    abort, or a client-disconnect ``CancelledError`` propagating out of the
    body generator) the upstream stream is ``aclose()``d, releasing the pool slot
    rather than leaking a zombie background drain.
    """

    def stream(self, request: RunnerRequest) -> AbstractAsyncContextManager[StreamingResult]: ...
