"""ASGI app factories for the multi-worker supervisor tests (``test_uvicorn_workers``).

Kept in their own lightweight module: spawned worker processes import it by
name, so it must not pull in the application.
"""

from __future__ import annotations

import os
import signal
from typing import Any


def failing_factory() -> Any:
    """App factory that raises, like ``create_app`` on a config that no longer validates."""
    raise RuntimeError("boom: app factory failed")


def sigkilled_factory() -> Any:
    """App factory whose process is SIGKILLed mid-boot, like the OOM killer during import."""
    os.kill(os.getpid(), signal.SIGKILL)
    raise AssertionError("unreachable: SIGKILL is not catchable")


def failing_lifespan_factory() -> Any:
    """App whose lifespan startup fails, like an unreachable database at boot."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "lifespan":
            return
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.failed", "message": "boom: lifespan failed"})

    return app


def healthy_factory() -> Any:
    """App that starts and answers every HTTP request with 200."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    return app
