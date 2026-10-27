"""Drive ``/events/stream`` fast and observe what it has delivered.

The ASGI transport buffers a streaming body until it ends, so a test cannot
read frames off an open stream. :class:`StreamProbe` counts the items the
stream service hands the router instead. The router pulls the next item only
after it has passed the previous one through its credential recheck and
written it out, so ``wait_for_items(2)`` returns once the first frame has been
delivered on a verified stream. A test that mutates the caller's state only
after that point does not race the stream's first re-verification.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import pytest

from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.events import EventView, Heartbeat
from jentic_one.shared.context import Context


@dataclass
class StreamProbe:
    """Counts the items the stream service hands the router."""

    items: int = 0

    async def wait_for_items(self, count: int, *, timeout: float = 10.0) -> None:
        async def _poll() -> None:
            while self.items < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout=timeout)


def install_fast_stream(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> StreamProbe:
    """Re-verify on every page, poll every 50 ms, and count what the stream yields."""
    monkeypatch.setattr(ctx.config.admin.events, "stream_recheck_seconds", 0.01)
    probe = StreamProbe()
    original = EventStreamService.stream

    async def _stream(
        self: EventStreamService, **kwargs: Any
    ) -> AsyncGenerator[EventView | Heartbeat, None]:
        items = original(self, **{**kwargs, "poll_interval_seconds": 0.05})
        try:
            async for item in items:
                probe.items += 1
                yield item
        finally:
            await items.aclose()

    monkeypatch.setattr(EventStreamService, "stream", _stream)
    return probe
