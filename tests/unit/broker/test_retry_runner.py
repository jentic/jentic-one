"""Unit tests for the ``RetryRunner`` decorator (§09 E4.1).

Retry is the capability-gated, idempotency-aware loop wrapping the
breaker-decorated transport. The gate is ``pre_send OR idempotent_method OR
has_idempotency_key``: a connect-phase failure retries for any method, a
post-send failure (read timeout, mirrored 429/503) only for safe methods or with
an ``Idempotency-Key``. Backoff is exponential + full jitter, an upstream
``Retry-After`` is honored, and the whole loop is bounded by the deadline.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable

import pytest

from jentic_one.broker.adapters.runners.base import (
    HTTP_RUNNER_CAPABILITIES,
    RunnerRequest,
    RunnerResult,
    StreamingResult,
)
from jentic_one.broker.adapters.runners.deadline import DeadlineRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner, _is_idempotent
from jentic_one.broker.core.exceptions import (
    BrokerError,
    CircuitOpenError,
    DeadlineExceededError,
    ErrorOrigin,
    RunnerUnavailableError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
    upstream_contact_ruled_out,
)
from jentic_one.broker.services.execution.pipeline import build_runner
from jentic_one.shared.config import RetryConfig


def _ok(status: int = 200, headers: dict[str, str] | None = None) -> RunnerResult:
    return RunnerResult(
        status_code=status,
        body=b"ok",
        headers=headers or {},
        content_type=None,
        duration_ms=1,
    )


class _ScriptedRunner:
    """Inner runner that replays a scripted sequence of results / exceptions."""

    def __init__(self, script: list[RunnerResult | Exception]) -> None:
        self._script = script
        self.calls = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        item = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item

    @contextlib.asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        self.calls += 1
        yield StreamingResult(status_code=200, headers={}, content_type=None, aiter=_empty_aiter())


async def _empty_aiter() -> AsyncIterator[bytes]:
    return
    yield  # pragma: no cover - makes this an async generator


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make backoff sleeps instant so retry timing doesn't slow the suite."""

    async def _instant(_delay: float) -> None:
        return None

    monkeypatch.setattr("jentic_one.broker.adapters.runners.retry.asyncio.sleep", _instant)


def _get(url: str = "https://api.example.com/x") -> RunnerRequest:
    return RunnerRequest(method="GET", url=url)


def _post(*, keyed: bool = False) -> RunnerRequest:
    return RunnerRequest(method="POST", url="https://api.example.com/x", has_idempotency_key=keyed)


# --- idempotency classification -------------------------------------------------


def test_idempotent_methods() -> None:
    for method in ("GET", "head", "PUT", "delete", "OPTIONS", "TRACE"):
        assert _is_idempotent(RunnerRequest(method=method, url="https://x"))


def test_post_without_key_is_not_idempotent() -> None:
    assert not _is_idempotent(_post())


def test_post_with_idempotency_key_flag_is_idempotent() -> None:
    assert _is_idempotent(_post(keyed=True))
    assert _is_idempotent(RunnerRequest(method="patch", url="https://x", has_idempotency_key=True))


def test_idempotency_key_header_alone_does_not_count() -> None:
    # Pins #1711: the gate reads the handler-set flag. ``headers`` are the
    # upstream-bound set, which never carries the broker-consumed key.
    request = RunnerRequest(
        method="POST", url="https://api.example.com/x", headers={"Idempotency-Key": "abc"}
    )
    assert not _is_idempotent(request)


# --- transport-error retry decisions -------------------------------------------


@pytest.mark.asyncio
async def test_connect_phase_error_retries_any_method() -> None:
    # Key-less POST + connect-phase (pre_send) error → retryable (no bytes sent).
    err = UpstreamTimeoutError(detail="connect", origin=ErrorOrigin.UPSTREAM, pre_send=True)
    inner = _ScriptedRunner([err, _ok(201)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_post())

    assert result.status_code == 201
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_post_send_error_not_retried_for_keyless_post() -> None:
    # Post-send read timeout on a key-less POST → double-spend risk, no retry.
    err = UpstreamTimeoutError(detail="read", origin=ErrorOrigin.UPSTREAM)
    inner = _ScriptedRunner([err, _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    with pytest.raises(UpstreamTimeoutError):
        await runner.run(_post())
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_post_send_error_retried_for_idempotent_method() -> None:
    err = UpstreamTimeoutError(detail="read", origin=ErrorOrigin.UPSTREAM)
    inner = _ScriptedRunner([err, _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_get())

    assert result.status_code == 200
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_post_send_error_retried_with_idempotency_key() -> None:
    err = UpstreamTimeoutError(detail="read", origin=ErrorOrigin.UPSTREAM)
    inner = _ScriptedRunner([err, _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_post(keyed=True))

    assert result.status_code == 200
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_error_propagates_after_exhausting_attempts() -> None:
    err = UpstreamTimeoutError(detail="connect", origin=ErrorOrigin.UPSTREAM, pre_send=True)
    inner = _ScriptedRunner([err])  # always fails
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    with pytest.raises(UpstreamTimeoutError):
        await runner.run(_post())
    assert inner.calls == 3


# --- prior-attempt tracking (#1710) ----------------------------------------------


def _circuit_open() -> CircuitOpenError:
    return CircuitOpenError(detail="open", type="circuit_open")


def _connect_failed() -> UpstreamTimeoutError:
    return UpstreamTimeoutError(detail="connect", type="upstream_connect_failed", pre_send=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first",
    [
        UpstreamTimeoutError(detail="read", type="upstream_timeout"),
        _ok(503),
    ],
    ids=["read_timeout", "upstream_503"],
)
@pytest.mark.parametrize("last", [_circuit_open, _connect_failed], ids=["circuit", "connect"])
async def test_final_pre_send_error_after_a_sent_attempt_is_flagged(
    first: RunnerResult | Exception, last: Callable[[], BrokerError]
) -> None:
    """A keyed POST whose first attempt reached the upstream never reads as "nothing sent"."""
    final = last()
    inner = _ScriptedRunner([first, final])
    runner = RetryRunner(inner, max_attempts=2, base_backoff_s=0.0)

    with pytest.raises(BrokerError) as caught:
        await runner.run(_post(keyed=True))

    assert caught.value is final
    assert caught.value.prior_attempt_sent is True
    assert not upstream_contact_ruled_out(caught.value)


@pytest.mark.asyncio
async def test_all_attempts_pre_send_stays_ruled_out() -> None:
    """Every attempt failed before sending: the error still proves no upstream contact."""
    inner = _ScriptedRunner([_connect_failed(), _circuit_open()])
    runner = RetryRunner(inner, max_attempts=2, base_backoff_s=0.0)

    with pytest.raises(CircuitOpenError) as caught:
        await runner.run(_post(keyed=True))

    assert caught.value.prior_attempt_sent is False
    assert upstream_contact_ruled_out(caught.value)


def test_upstream_contact_ruled_out_classification() -> None:
    """Only pre-dispatch / connect-phase broker errors rule out upstream contact."""
    assert upstream_contact_ruled_out(_connect_failed())
    assert upstream_contact_ruled_out(_circuit_open())
    assert upstream_contact_ruled_out(RunnerUnavailableError(detail="down"))
    assert not upstream_contact_ruled_out(UpstreamTimeoutError(detail="read"))
    assert not upstream_contact_ruled_out(BrokerError(detail="x", type="upstream_transport_error"))
    assert not upstream_contact_ruled_out(DeadlineExceededError(detail="late"))
    assert not upstream_contact_ruled_out(UpstreamResponseTooLargeError(detail="big"))
    assert not upstream_contact_ruled_out(RuntimeError("bug"))


# --- status-code retry decisions ------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_status_retried_for_idempotent() -> None:
    inner = _ScriptedRunner([_ok(503), _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_get())

    assert result.status_code == 200
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_retryable_status_not_retried_for_keyless_post() -> None:
    inner = _ScriptedRunner([_ok(503), _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_post())

    # Returned verbatim, not retried (would double-spend a non-idempotent POST).
    assert result.status_code == 503
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_retryable_status_retried_for_keyed_post() -> None:
    inner = _ScriptedRunner([_ok(503), _ok(503), _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_post(keyed=True))

    assert result.status_code == 200
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_non_retryable_status_returned_immediately() -> None:
    inner = _ScriptedRunner([_ok(404)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_get())

    assert result.status_code == 404
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_exhausted_status_retry_returns_last_response() -> None:
    inner = _ScriptedRunner([_ok(503)])  # always 503
    runner = RetryRunner(inner, max_attempts=2, base_backoff_s=0.0)

    result = await runner.run(_get())

    # Budget spent → return the upstream's own 503 verbatim (no synthetic error).
    assert result.status_code == 503
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_retry_after_header_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[float] = []

    async def _capture(delay: float) -> None:
        captured.append(delay)

    monkeypatch.setattr("jentic_one.broker.adapters.runners.retry.asyncio.sleep", _capture)
    inner = _ScriptedRunner([_ok(429, headers={"Retry-After": "2"}), _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0, max_backoff_s=0.0)

    result = await runner.run(_get())

    assert result.status_code == 200
    # The upstream's explicit 2s Retry-After overrides the (zeroed) backoff.
    assert captured == [pytest.approx(2.0)]


# --- streaming -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_passes_through_without_retry() -> None:
    inner = _ScriptedRunner([_ok(200)])
    runner = RetryRunner(inner, max_attempts=3)

    async with runner.stream(_get()) as result:
        assert result.status_code == 200
    assert inner.calls == 1


# --- composition ---------------------------------------------------------------


def test_build_runner_inserts_retry_when_enabled() -> None:
    inner = _ScriptedRunner([_ok(200)])
    composed = build_runner(
        inner, deadline_s=5.0, retry=RetryConfig(enabled=True), caps=HTTP_RUNNER_CAPABILITIES
    )

    assert isinstance(composed, DeadlineRunner)
    assert isinstance(composed._inner, RetryRunner)


def test_build_runner_skips_retry_when_disabled() -> None:
    inner = _ScriptedRunner([_ok(200)])
    composed = build_runner(
        inner, deadline_s=5.0, retry=RetryConfig(enabled=False), caps=HTTP_RUNNER_CAPABILITIES
    )

    assert isinstance(composed, DeadlineRunner)
    assert composed._inner is inner


def test_build_runner_skips_retry_when_none() -> None:
    inner = _ScriptedRunner([_ok(200)])
    composed = build_runner(inner, deadline_s=5.0, caps=HTTP_RUNNER_CAPABILITIES)

    assert isinstance(composed, DeadlineRunner)
    assert composed._inner is inner
