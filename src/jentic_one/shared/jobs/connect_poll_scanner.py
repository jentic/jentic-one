"""Background scanner that drives device-flow connect-session polling.

Owns the "poll the vendor" side of the device-flow state machine so it is
server-driven rather than client-driven — an agent that calls ``:confirm``
and walks away sees progress land without another request coming in. The
``/status`` HTTP surface is a pure stored-state read; this scanner is the
sole upstream trigger. Mirrors ``CredentialExpiryScanner`` /
``CatalogUpdateScanner`` in shape (tick loop with per-tick error
containment) and layering (shared job that reads control-DB state).

Callback flows (authorization_code) are advanced by the OAuth callback
route, not this scanner — the query filter excludes them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
)
from jentic_one.control.services.integrations.flow_handlers import DeviceFlowHandler

if TYPE_CHECKING:
    from jentic_one.shared.context import Context

_logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECONDS = 2.0
_CANDIDATE_LIMIT = 100


class ConnectPollScanner:
    """Periodically advances device-flow sessions in state=polling."""

    def __init__(
        self,
        ctx: Context,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
    ) -> None:
        self._ctx = ctx
        self._poll_interval = poll_interval
        self._running = False

    async def run(self) -> None:
        """Main loop — sweep every ``poll_interval`` seconds until cancelled.

        Mirrors ``WorkerLoop.run`` / ``CredentialExpiryScanner.run``: a
        single tick's error is caught and logged so a transient DB hiccup
        never kills the scanner permanently.
        """
        self._running = True
        _logger.info("connect_poll_scanner_started")
        try:
            while self._running:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _logger.exception("connect_poll_scanner_tick_error")
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            _logger.info("connect_poll_scanner_cancelled")
        finally:
            self._running = False
            _logger.info("connect_poll_scanner_stopped")

    def stop(self) -> None:
        """Signal the scanner to stop after the current tick."""
        self._running = False

    async def _tick(self) -> None:
        """Advance every polling-device-flow session up to a per-tick cap.

        The cap keeps a single scanner tick from becoming a hostage to a
        slow vendor when there are many sessions in flight — the next tick
        picks up the remainder. Each session's advancement is guarded so
        one bad row doesn't abort the batch.
        """
        session_ids = await self._due_sessions()
        if not session_ids:
            return
        service = ConnectSessionService(self._ctx)
        for session_id in session_ids:
            try:
                await service.advance_polling_session(session_id)
            except Exception:
                _logger.exception(
                    "connect_poll_scanner_advance_failed",
                    session_id=session_id,
                )

    async def _due_sessions(self) -> list[str]:
        """Return session IDs due for a poll tick this cycle.

        Filters to ``state == 'polling'`` + ``resolved_flow == 'device_flow'``.
        Callback flows are excluded — they're advanced by the OAuth
        callback route, not by this scanner. RFC 8628 interval throttling
        happens per-session inside ``DeviceFlowHandler.advance`` (via
        ``last_polled_at``), so we don't try to be clever with the query.
        """
        async with self._ctx.control_db.session() as session:
            stmt = (
                select(ConnectSession.id)
                .where(
                    ConnectSession.state == "polling",
                    ConnectSession.resolved_flow == DeviceFlowHandler.kind,
                )
                .order_by(ConnectSession.created_at.asc())
                .limit(_CANDIDATE_LIMIT)
            )
            result = await session.execute(stmt)
            return [str(row_id) for row_id in result.scalars().all()]
