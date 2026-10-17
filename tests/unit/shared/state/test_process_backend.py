"""One shared-state backend per process: built once on the Context, closed once."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI

from jentic_one.auth.web.app import install_on_app as install_auth
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
