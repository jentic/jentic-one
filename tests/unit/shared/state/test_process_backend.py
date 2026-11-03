"""One shared-state backend per process: built once on the Context, closed once."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
import structlog.testing
from fastapi import FastAPI

from jentic_one.auth.web.app import install_on_app as install_auth
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.control.web.connect_limits import install_connect_limiters
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.state import (
    MemoryStateBackend,
    SharedStateBackend,
    StateBackendBuildContext,
    register_state_backend,
)
from jentic_one.shared.state.factory import unregister_state_backend
from jentic_one.shared.web import app_factory
from jentic_one.shared.web.app_factory import create_combined_app, create_surface_app


class _CountingBackend(MemoryStateBackend):
    def __init__(self) -> None:
        super().__init__()
        self.closes = 0

    async def aclose(self) -> None:
        self.closes += 1


@pytest.fixture()
def counting() -> Iterator[list[_CountingBackend]]:
    built: list[_CountingBackend] = []

    def _build(ctx: StateBackendBuildContext) -> SharedStateBackend:
        backend = _CountingBackend()
        built.append(backend)
        return backend

    register_state_backend("counting", _build)
    try:
        yield built
    finally:
        unregister_state_backend("counting")


def _ctx(sample_config_dict: dict[str, Any], **state: Any) -> Context:
    return Context(AppConfig.model_validate({**sample_config_dict, "state": state}))


def test_the_default_process_backend_is_memory(sample_config_dict: dict[str, Any]) -> None:
    ctx = _ctx(sample_config_dict)
    assert isinstance(ctx.state_backend, MemoryStateBackend)
    assert ctx.state_backend is ctx.state_backend


async def test_every_surface_shares_one_backend_closed_once(
    sample_config_dict: dict[str, Any], counting: list[_CountingBackend]
) -> None:
    ctx = _ctx(sample_config_dict, backend="counting")
    app = FastAPI()
    install_auth(app, ctx)
    install_connect_limiters(app, ctx)

    (backend,) = counting
    assert app.state.auth_state_backend is backend
    assert app.state.control_state_backend is backend

    await ctx.shutdown()
    assert backend.closes == 0, "closing the databases leaves the backend to the lifespan"
    await ctx.close_state_backend()
    await ctx.close_state_backend()
    assert backend.closes == 1


async def test_close_without_a_built_backend_is_a_no_op(
    sample_config_dict: dict[str, Any], counting: list[_CountingBackend]
) -> None:
    ctx = _ctx(sample_config_dict, backend="counting")
    await ctx.close_state_backend()
    assert counting == []


def test_an_unknown_backend_fails_when_the_app_is_assembled(
    sample_config_dict: dict[str, Any],
) -> None:
    ctx = _ctx(sample_config_dict, backend="not-registered")
    with pytest.raises(RuntimeError, match=r"registered backends: .*memory"):
        install_connect_limiters(FastAPI(), ctx)


@pytest.mark.parametrize("shape", ["surface", "combined"])
async def test_the_app_lifespan_closes_the_backend_even_when_startup_fails(
    sample_config_dict: dict[str, Any],
    counting: list[_CountingBackend],
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    ctx = _ctx(sample_config_dict, backend="counting")
    app = (
        create_control_app(ctx)
        if shape == "surface"
        else create_combined_app(ctx, ["control", "auth"])
    )
    (backend,) = counting

    async def _fail() -> None:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(ctx, "startup", _fail)
    with pytest.raises(RuntimeError, match="database unreachable"):
        async with app.router.lifespan_context(app):
            pass
    assert backend.closes == 1


async def test_the_surface_lifespan_closes_the_backend_after_its_extra_lifespan(
    sample_config_dict: dict[str, Any],
    counting: list[_CountingBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker's runners (its extra_lifespan) drain on a still-open backend."""
    ctx = _ctx(sample_config_dict, backend="counting")
    closes_seen_by_extra: list[int] = []

    @asynccontextmanager
    async def _extra(app: FastAPI) -> AsyncIterator[None]:
        backend = ctx.state_backend
        try:
            yield
        finally:
            closes_seen_by_extra.append(cast("_CountingBackend", backend).closes)

    async def _noop() -> None:
        return None

    app = create_surface_app(
        ctx, title="t", routers=[], enabled_apps={"probe"}, extra_lifespan=_extra
    )
    monkeypatch.setattr(ctx, "startup", _noop)
    monkeypatch.setattr(ctx, "shutdown", _noop)
    # The stubbed startup connects no database, so skip instrumenting the
    # engines (a no-op only once an earlier test has instrumented the process).
    monkeypatch.setattr(app_factory, "instrument_databases", lambda _ctx: None)
    async with app.router.lifespan_context(app):
        pass
    (backend,) = counting
    assert closes_seen_by_extra == [0], "the backend was closed before extra_lifespan exited"
    assert backend.closes == 1


async def test_a_failing_backend_close_is_logged_and_does_not_mask_a_startup_error(
    sample_config_dict: dict[str, Any],
    counting: list[_CountingBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(sample_config_dict, backend="counting")
    app = create_control_app(ctx)
    (backend,) = counting

    async def _fail_close() -> None:
        raise ConnectionError("backend gone")

    async def _fail_startup() -> None:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(backend, "aclose", _fail_close)
    monkeypatch.setattr(ctx, "startup", _fail_startup)
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(RuntimeError, match="database unreachable"),
    ):
        async with app.router.lifespan_context(app):
            pass
    failures = [e for e in logs if e["event"] == "state_backend_close_failed"]
    assert len(failures) == 1
    assert failures[0]["log_level"] == "warning"
    assert failures[0]["backend"] == "counting"
