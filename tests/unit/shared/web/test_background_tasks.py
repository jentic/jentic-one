"""Unit tests for the app-owned background-task registry.

Pins the contract the streaming persistence relies on: a spawned task runs off
the caller, is bounded by its timeout, never raises, and every outcome —
including a task cancelled by the shutdown drain — is counted and logged.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import structlog
from fastapi import FastAPI

from jentic_one.shared.web import background_tasks as bg
from jentic_one.shared.web.background_tasks import (
    BackgroundTaskRegistry,
    DrainResult,
    get_background_tasks,
    install_background_tasks,
)


def _outcomes(counter_add: object) -> list[str]:
    return [call.args[1]["outcome"] for call in counter_add.call_args_list]  # type: ignore[attr-defined]


async def test_spawn_returns_before_the_task_finishes() -> None:
    registry = BackgroundTaskRegistry()
    release = asyncio.Event()
    done: list[bool] = []

    async def _work() -> None:
        await release.wait()
        done.append(True)

    with patch.object(bg._task_outcomes, "add") as add:
        assert registry.spawn(_work(), kind="t", timeout_s=5) is True
        assert registry.pending == 1
        assert done == []
        release.set()
        result = await registry.drain(5)

    assert done == [True]
    assert result == DrainResult(finished=1, cancelled=0)
    assert registry.pending == 0
    assert _outcomes(add) == ["completed"]


async def test_failing_task_is_logged_and_counted_never_raised() -> None:
    registry = BackgroundTaskRegistry()

    async def _boom() -> None:
        raise RuntimeError("db down")

    with patch.object(bg._task_outcomes, "add") as add, structlog.testing.capture_logs() as logs:
        registry.spawn(_boom(), kind="t", timeout_s=5, log_fields={"execution_id": "e1"})
        await registry.drain(5)

    assert _outcomes(add) == ["failed"]
    (failed,) = [e for e in logs if e["event"] == "background_task_failed"]
    assert failed["execution_id"] == "e1"
    assert failed["log_level"] == "error"


async def test_task_past_its_timeout_is_cancelled_and_counted() -> None:
    registry = BackgroundTaskRegistry()
    cancelled: list[bool] = []

    async def _hang() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    with patch.object(bg._task_outcomes, "add") as add, structlog.testing.capture_logs() as logs:
        registry.spawn(_hang(), kind="t", timeout_s=0.05)
        await registry.drain(5)

    assert cancelled == [True]
    assert _outcomes(add) == ["timed_out"]
    assert [e["event"] for e in logs].count("background_task_timed_out") == 1


async def test_a_timeout_raised_inside_the_task_is_a_failure_not_a_timeout() -> None:
    registry = BackgroundTaskRegistry()

    async def _own_timeout() -> None:
        raise TimeoutError("inner")

    with patch.object(bg._task_outcomes, "add") as add:
        registry.spawn(_own_timeout(), kind="t", timeout_s=5)
        await registry.drain(5)

    assert _outcomes(add) == ["failed"]


async def test_drain_cancels_what_is_still_running_and_counts_it() -> None:
    registry = BackgroundTaskRegistry()

    async def _quick() -> None:
        await asyncio.sleep(0)

    async def _slow() -> None:
        await asyncio.sleep(10)

    with patch.object(bg._task_outcomes, "add") as add, structlog.testing.capture_logs() as logs:
        registry.spawn(_quick(), kind="t", timeout_s=30)
        registry.spawn(_slow(), kind="t", timeout_s=30, log_fields={"execution_id": "e2"})
        result = await registry.drain(0.1)

    assert result == DrainResult(finished=1, cancelled=1)
    assert registry.pending == 0
    assert sorted(_outcomes(add)) == ["cancelled", "completed"]
    (cancelled,) = [e for e in logs if e["event"] == "background_task_cancelled"]
    assert cancelled["execution_id"] == "e2"
    (summary,) = [e for e in logs if e["event"] == "background_tasks_drain_timed_out"]
    assert summary["cancelled"] == 1


async def test_spawn_after_drain_is_rejected_and_the_coroutine_never_runs() -> None:
    registry = BackgroundTaskRegistry()
    await registry.drain(0)
    ran: list[bool] = []

    async def _work() -> None:
        ran.append(True)

    with patch.object(bg._task_outcomes, "add") as add:
        assert registry.spawn(_work(), kind="t", timeout_s=5) is False
    await asyncio.sleep(0)

    assert ran == []
    assert registry.closed
    assert _outcomes(add) == ["rejected"]


async def test_tasks_spawned_during_the_drain_are_waited_on() -> None:
    registry = BackgroundTaskRegistry()
    done: list[str] = []

    async def _second() -> None:
        await asyncio.sleep(0.02)
        done.append("second")

    async def _first() -> None:
        await asyncio.sleep(0.02)
        registry.spawn(_second(), kind="t", timeout_s=5)
        done.append("first")

    registry.spawn(_first(), kind="t", timeout_s=5)
    result = await registry.drain(5)

    assert done == ["first", "second"]
    assert result.cancelled == 0


@pytest.mark.parametrize("installed", [True, False])
def test_install_and_get(installed: bool) -> None:
    app = FastAPI()
    registry = install_background_tasks(app) if installed else None
    assert get_background_tasks(app) is registry
