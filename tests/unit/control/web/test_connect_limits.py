"""The control surface's connect limiters run on the process's shared-state backend."""

from __future__ import annotations

from unittest.mock import MagicMock

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

    route_lim = connect_limiter(app, ROUTE_LIMITER_ATTR)
    mcp_lim = connect_limiter(app, MCP_LIMITER_ATTR)
    assert route_lim is mcp_lim
    await route_lim.acquire("actor-1")
    await mcp_lim.acquire("actor-1")
    assert backend.bucket_keys == [
        "rl:integrations_connect:actor-1",
        "rl:integrations_connect:actor-1",
    ]


async def test_fallback_connect_limiter_shares_budget_across_http_and_mcp() -> None:
    app = FastAPI()
    route_lim = connect_limiter(app, ROUTE_LIMITER_ATTR)
    mcp_lim = connect_limiter(app, MCP_LIMITER_ATTR)
    assert route_lim is mcp_lim
    assert route_lim._key_prefix == "rl:integrations_connect:"
