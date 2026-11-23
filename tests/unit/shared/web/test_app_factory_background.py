"""Unit tests for app_factory background service lifecycle helpers."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web import app_factory
from jentic_one.shared.web.app_factory import (
    _background_services,
    _BackgroundHandle,
    _stop_background,
    create_surface_app,
)

_AF = "jentic_one.shared.web.app_factory"


class _FakeService:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._name = name
        self._events = events
        self._stop_event = stop_event

    async def drain(self) -> bool:
        self._events.append(f"drain:{self._name}")
        return True

    def stop(self) -> None:
        self._events.append(f"stop:{self._name}")
        if self._stop_event is not None:
            self._stop_event.set()


@pytest.mark.asyncio
async def test_stop_background_does_not_swallow_outer_cancel() -> None:
    """Cancelling the task awaiting _stop_background must propagate CancelledError."""
    stop_called = asyncio.Event()
    release_inner = asyncio.Event()
    events: list[str] = []
    service = _FakeService("scanner", events, stop_event=stop_called)

    async def _stubborn_runner() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_inner.wait()
            raise

    inner_task = asyncio.create_task(_stubborn_runner())
    await asyncio.sleep(0)
    handle = _BackgroundHandle(service=service, task=inner_task)

    stopper_task = asyncio.create_task(_stop_background(handle))
    await asyncio.wait_for(stop_called.wait(), timeout=1.0)
    stopper_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopper_task

    assert stopper_task.cancelled() is True
    release_inner.set()
    with contextlib.suppress(asyncio.CancelledError):
        await inner_task


@pytest.mark.asyncio
async def test_stop_order() -> None:
    """_background_services starts and stops services in the canonical order."""
    events: list[str] = []
    tasks: list[asyncio.Task[None]] = []

    def _make_handle(name: str) -> _BackgroundHandle:
        events.append(f"start:{name}")
        service = _FakeService(name, events)
        task = asyncio.create_task(asyncio.sleep(60))
        tasks.append(task)
        return _BackgroundHandle(service=service, task=task)

    app = FastAPI()
    ctx = MagicMock()
    enabled_apps = {"registry", "admin", "control"}

    with (
        patch(
            f"{_AF}._start_worker",
            side_effect=lambda *_a, **_kw: _make_handle("worker"),
        ) as start_worker,
        patch(
            f"{_AF}._start_expiry_scanner",
            side_effect=lambda *_a, **_kw: _make_handle("expiry"),
        ),
        patch(
            f"{_AF}._start_catalog_update_scanner",
            side_effect=lambda *_a, **_kw: _make_handle("catalog"),
        ),
        patch(
            f"{_AF}._start_connect_poll_scanner",
            side_effect=lambda *_a, **_kw: _make_handle("connect_poll"),
        ),
    ):
        async with _background_services(
            app,
            ctx,
            enabled_apps,
            worker_kwargs={"upstream_executor": "exec_sentinel"},
        ):
            assert events == [
                "start:worker",
                "start:expiry",
                "start:catalog",
                "start:connect_poll",
            ]
            start_worker.assert_called_once_with(
                ctx, enabled_apps, upstream_executor="exec_sentinel"
            )

    assert events == [
        "start:worker",
        "start:expiry",
        "start:catalog",
        "start:connect_poll",
        "stop:connect_poll",
        "stop:catalog",
        "stop:expiry",
        "drain:worker",
        "stop:worker",
    ]
    assert all(t.done() for t in tasks)
    assert app_factory is not None


@pytest.mark.asyncio
async def test_surface_lifespan_starts_draining_admission_gate_before_stopping_services(
    sample_config_dict: dict[str, object],
) -> None:
    """Surface lifespan marks admission gate draining before stopping background services."""
    events: list[str] = []
    tasks: list[asyncio.Task[None]] = []

    def _make_handle(name: str) -> _BackgroundHandle:
        service = _FakeService(name, events)
        task = asyncio.create_task(asyncio.sleep(60))
        tasks.append(task)
        return _BackgroundHandle(service=service, task=task)

    ctx = Context(AppConfig.model_validate(sample_config_dict))
    app = create_surface_app(ctx, title="broker", routers=[], enabled_apps={"broker"})
    gate = MagicMock()
    gate.start_draining.side_effect = lambda: events.append("gate:start_draining")
    app.state.broker_admission_gate = gate

    with (
        patch.object(ctx, "startup", AsyncMock()),
        patch.object(ctx, "shutdown", AsyncMock()),
        patch(f"{_AF}.instrument_databases"),
        patch(
            f"{_AF}._start_worker",
            side_effect=lambda *_a, **_kw: _make_handle("worker"),
        ),
        patch(
            f"{_AF}._start_expiry_scanner",
            side_effect=lambda *_a, **_kw: _make_handle("expiry"),
        ),
        patch(
            f"{_AF}._start_catalog_update_scanner",
            side_effect=lambda *_a, **_kw: _make_handle("catalog"),
        ),
        patch(
            f"{_AF}._start_connect_poll_scanner",
            side_effect=lambda *_a, **_kw: _make_handle("connect_poll"),
        ),
    ):
        async with app.router.lifespan_context(app):
            assert events == []

    assert events == [
        "gate:start_draining",
        "stop:connect_poll",
        "stop:catalog",
        "stop:expiry",
        "drain:worker",
        "stop:worker",
    ]
