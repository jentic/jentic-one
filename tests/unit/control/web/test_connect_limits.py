"""The control surface's connect limiters run on the process's shared-state backend."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from jentic_one.control.web.connect_limits import (
    MCP_LIMITER_ATTR,
    ROUTE_LIMITER_ATTR,
    connect_limiter,
    install_connect_limiters,
)
from jentic_one.shared.state import RateLimitDecision


class _RecordingBackend:
    """A non-memory backend (a stand-in for redis) that records bucket keys."""

    def __init__(self) -> None:
        self.bucket_keys: list[str] = []

    async def token_bucket(
        self, key: str, *, rate: float, burst: int, cost: int = 1
    ) -> RateLimitDecision:
        self.bucket_keys.append(key)
        return RateLimitDecision(allowed=True, remaining=burst, retry_after_s=0.0, limit=burst)

    async def aclose(self) -> None:
        return None


async def test_both_connect_limiters_use_the_process_backend() -> None:
    backend = _RecordingBackend()
    ctx = MagicMock()
    ctx.state_backend = backend
    app = FastAPI()

    install_connect_limiters(app, ctx)

    await connect_limiter(app, ROUTE_LIMITER_ATTR).acquire("actor-1")
    await connect_limiter(app, MCP_LIMITER_ATTR).acquire("actor-1")
    assert backend.bucket_keys == [
        "rl:integrations_connect:actor-1",
        "rl:mcp_integrations_connect:actor-1",
    ]


def test_a_missing_connect_limiter_is_a_wiring_error_not_a_private_fallback() -> None:
    """An app assembled without ``install_connect_limiters`` fails loudly."""
    app = FastAPI()

    with pytest.raises(RuntimeError, match="install_connect_limiters"):
        connect_limiter(app, ROUTE_LIMITER_ATTR)
    assert getattr(app.state, ROUTE_LIMITER_ATTR, None) is None
