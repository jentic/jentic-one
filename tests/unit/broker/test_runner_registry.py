"""Unit tests for the ``RunnerRegistry`` — scheme selection + lifecycle (§11 RN-0.3)."""

from __future__ import annotations

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.errors import (
    RunnerSchemeUnsupportedError,
    RunnerUnavailableError,
)


class _FakeRunner:
    """A minimal runner with optional startup/aclose lifecycle tracking."""

    def __init__(self, *, startup_error: Exception | None = None) -> None:
        self._startup_error = startup_error
        self.started = 0
        self.closed = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(status_code=200, body=b"", headers={}, content_type=None, duration_ms=1)

    async def startup(self) -> None:
        self.started += 1
        if self._startup_error is not None:
            raise self._startup_error

    async def aclose(self) -> None:
        self.closed += 1


class _LifecycleLessRunner:
    """A runner with no startup()/aclose() — like today's HTTP composed runner."""

    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(status_code=200, body=b"", headers={}, content_type=None, duration_ms=1)


# --- selection ------------------------------------------------------------------


def test_select_by_scheme() -> None:
    registry = RunnerRegistry()
    runner = _FakeRunner()
    registry.register(["http", "https"], runner)

    assert registry.select("http://api.example.com/x") is runner
    assert registry.select("https://api.example.com/x") is runner


def test_select_is_case_insensitive() -> None:
    registry = RunnerRegistry()
    runner = _FakeRunner()
    registry.register("HTTPS", runner)

    assert registry.select("HTTPS://API.EXAMPLE.COM/x") is runner


def test_unknown_scheme_raises_501() -> None:
    registry = RunnerRegistry()
    registry.register(["http", "https"], _FakeRunner())

    with pytest.raises(RunnerSchemeUnsupportedError):
        registry.select("ftp://files.example.com/x")


# --- lifecycle ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_opens_each_runner_once() -> None:
    registry = RunnerRegistry()
    runner = _FakeRunner()
    registry.register(["http", "https"], runner)  # one runner, two schemes

    await registry.startup()

    assert runner.started == 1  # not twice, despite two schemes


@pytest.mark.asyncio
async def test_required_startup_failure_aborts() -> None:
    registry = RunnerRegistry()
    registry.register("http", _FakeRunner(startup_error=RuntimeError("boom")), required=True)

    with pytest.raises(RuntimeError, match="boom"):
        await registry.startup()


@pytest.mark.asyncio
async def test_optional_startup_failure_degrades_not_aborts() -> None:
    registry = RunnerRegistry()
    http = _FakeRunner()
    mqtt = _FakeRunner(startup_error=RuntimeError("no broker"))
    registry.register(["http", "https"], http, required=True)
    registry.register("mqtt", mqtt)

    # Does NOT raise — the optional mqtt runner degrades, HTTP still serves.
    await registry.startup()

    assert registry.select("http://x/y") is http
    with pytest.raises(RunnerUnavailableError):
        registry.select("mqtt://x/y")


@pytest.mark.asyncio
async def test_aclose_drains_all_runners_once() -> None:
    registry = RunnerRegistry()
    http = _FakeRunner()
    mqtt = _FakeRunner()
    registry.register(["http", "https"], http)
    registry.register("mqtt", mqtt)

    await registry.aclose()

    assert http.closed == 1
    assert mqtt.closed == 1


@pytest.mark.asyncio
async def test_aclose_error_never_blocks_drain() -> None:
    class _BadClose(_FakeRunner):
        async def aclose(self) -> None:
            raise RuntimeError("close failed")

    registry = RunnerRegistry()
    bad = _BadClose()
    good = _FakeRunner()
    registry.register("http", bad)
    registry.register("mqtt", good)

    # The bad runner's aclose() error is swallowed; the good one still closes.
    await registry.aclose()

    assert good.closed == 1


@pytest.mark.asyncio
async def test_lifecycle_less_runner_is_noop() -> None:
    registry = RunnerRegistry()
    runner = _LifecycleLessRunner()
    registry.register(["http", "https"], runner)

    # No startup()/aclose() to call — neither should raise.
    await registry.startup()
    await registry.aclose()

    assert registry.select("http://x/y") is runner
