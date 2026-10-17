"""Shutdown handlers registered by surface installers run when the app stops.

FastAPI runs an app's own ``add_event_handler("shutdown", …)`` handlers only
from its default lifespan. Every app the factory builds has its own
``lifespan``, so the factory's lifespan runs them; otherwise the auth and
control surfaces' shared-state backends (a Redis connection pool on the redis
backend) would never be closed.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app, create_surface_app


def _ctx(config: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(config), allowed_dbs=set())


@pytest.mark.asyncio
@pytest.mark.parametrize("combined", [False, True])
async def test_lifespan_runs_registered_shutdown_handlers(
    combined: bool, sample_config_dict: dict[str, Any]
) -> None:
    ctx = _ctx(sample_config_dict)
    app = (
        create_combined_app(ctx, [])
        if combined
        else create_surface_app(ctx, title="t", routers=[], enabled_apps=set())
    )
    calls: list[str] = []

    async def _async_handler() -> None:
        calls.append("async")

    def _failing_handler() -> None:
        raise RuntimeError("boom")

    def _sync_handler() -> None:
        calls.append("sync")

    for handler in (_async_handler, _failing_handler, _sync_handler):
        app.router.add_event_handler("shutdown", handler)

    with structlog.testing.capture_logs() as logs:
        async with app.router.lifespan_context(app):
            assert calls == []

    # A failing handler is logged and does not stop the others.
    assert calls == ["async", "sync"]
    assert "shutdown_handler_failed" in {entry["event"] for entry in logs}
