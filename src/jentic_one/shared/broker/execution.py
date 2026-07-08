"""Transport-neutral broker execution value objects.

These are the request/result/context/outcome value objects that make up the
public :class:`~jentic_one.shared.broker.broker.Broker` contract. They live in
``shared/broker`` — not ``broker/`` — so both the broker surface and any
downstream implementation depend on the *same* value types without ``shared``
importing ``broker`` (forbidden by ``tests/arch/test_module_boundaries.py``).

The concrete broker pipeline (``broker/services/execution/pipeline.py``) and the
runner adapters (``broker/adapters/runners/base.py``) re-export these names, so
existing broker-internal call sites keep importing them from their old modules
unchanged; this module is the single definition.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum

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
