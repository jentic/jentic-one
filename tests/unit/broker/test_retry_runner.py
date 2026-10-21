"""Unit tests for the ``RetryRunner`` decorator.

Retry is the capability-gated, idempotency-aware loop wrapping the
breaker-decorated transport. The gate is ``pre_send OR idempotent_method``:
a connect-phase failure retries for any method, while a post-send failure
(read timeout, mirrored 429/503) retries only for idempotent HTTP methods
(#1799). Backoff is exponential + full jitter, an upstream ``Retry-After`` is
honored, and the whole loop is bounded by the deadline.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import pytest

from jentic_one.broker.adapters.runners.base import (
    HTTP_RUNNER_CAPABILITIES,
    RunnerRequest,
    RunnerResult,
    StreamingResult,
)
from jentic_one.broker.adapters.runners.deadline import DeadlineRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner, _is_idempotent
from jentic_one.broker.core.exceptions import ErrorOrigin, UpstreamTimeoutError
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


def _post(headers: dict[str, str] | None = None) -> RunnerRequest:
    return RunnerRequest(method="POST", url="https://api.example.com/x", headers=headers or {})


# --- idempotency classification -------------------------------------------------


def test_idempotent_methods() -> None:
    for method in ("GET", "head", "PUT", "delete", "OPTIONS", "TRACE"):
        assert _is_idempotent(RunnerRequest(method=method, url="https://x"))


def test_post_and_patch_are_not_idempotent() -> None:
    assert not _is_idempotent(_post())
    assert not _is_idempotent(RunnerRequest(method="patch", url="https://x"))


def test_idempotency_key_header_does_not_license_post_send_retry() -> None:
    # Pins #1799: the broker consumes Idempotency-Key and strips it from
    # outbound headers, so the upstream cannot deduplicate a post-send retry of
    # POST or PATCH even when a caller supplies the header.
    assert not _is_idempotent(_post(headers={"Idempotency-Key": "abc"}))
    assert not _is_idempotent(
        RunnerRequest(method="PATCH", url="https://x", headers={"idempotency-key": "abc"})
    )


# --- transport-error retry decisions -------------------------------------------


@pytest.mark.asyncio
async def test_connect_phase_error_retries_any_method() -> None:
    # POST + connect-phase (pre_send) error → retryable (no bytes sent).
    err = UpstreamTimeoutError(detail="connect", origin=ErrorOrigin.UPSTREAM, pre_send=True)
    inner = _ScriptedRunner([err, _ok(201)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_post())

    assert result.status_code == 201
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_post_send_error_not_retried_for_post() -> None:
    # Post-send read timeout on POST → double-spend risk, no retry (#1799).
    err = UpstreamTimeoutError(detail="read", origin=ErrorOrigin.UPSTREAM)
    inner = _ScriptedRunner([err, _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    with pytest.raises(UpstreamTimeoutError):
        await runner.run(_post(headers={"Idempotency-Key": "k1"}))
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
async def test_error_propagates_after_exhausting_attempts() -> None:
    err = UpstreamTimeoutError(detail="connect", origin=ErrorOrigin.UPSTREAM, pre_send=True)
    inner = _ScriptedRunner([err])  # always fails
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    with pytest.raises(UpstreamTimeoutError):
        await runner.run(_post())
    assert inner.calls == 3


# --- status-code retry decisions ------------------------------------------------


@pytest.mark.asyncio
async def test_retryable_status_retried_for_idempotent() -> None:
    inner = _ScriptedRunner([_ok(503), _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_get())

    assert result.status_code == 200
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_retryable_status_not_retried_for_post() -> None:
    inner = _ScriptedRunner([_ok(503), _ok(200)])
    runner = RetryRunner(inner, max_attempts=3, base_backoff_s=0.0)

    result = await runner.run(_post(headers={"Idempotency-Key": "k1"}))

    # Returned verbatim, not retried (would double-spend a non-idempotent POST; #1799).
    assert result.status_code == 503
    assert inner.calls == 1


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
