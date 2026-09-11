"""Background scanner that drives device-flow polling.

Owns the "poll the vendor" side of the device-flow state machine so it is
server-driven rather than client-driven — an agent that calls ``:confirm``
(or a user that clicks Connect on a manually-created device-flow
credential) and walks away sees progress land without another request
coming in. The ``/status`` HTTP surface is a pure stored-state read; this
scanner is the sole upstream trigger. Mirrors ``CredentialExpiryScanner``
/ ``CatalogUpdateScanner`` in shape.

Targets ``device_flow_credentials`` — the aux row both entrypoints
(connect-session flow and raw-credential connect) write to. That row's
``encrypted_device_code IS NOT NULL`` is the natural "flow in flight"
signal: cleared by ``DeviceFlowHandler.on_finalise`` on success, and by
``_mark_terminal`` on failure. When a candidate is found, the service
dispatches on whether a live ``ConnectSession`` wraps the credential —
session-driven flows advance the session's state machine, standalone
credentials advance ``credentials.state`` directly.

Callback flows (authorization_code) are advanced by the OAuth callback
route, not this scanner — they have no aux row here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from jentic_one.control.core.schema.device_flow_credentials import DeviceFlowCredential
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
)

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
        """Advance every in-flight device-flow credential up to a per-tick cap.

        The cap keeps a single scanner tick from becoming a hostage to a
        slow vendor when there are many flows in flight — the next tick
        picks up the remainder. Each advancement is guarded so one bad
        row doesn't abort the batch.
        """
        credential_ids = await self._due_credentials()
        if not credential_ids:
            return
        service = ConnectSessionService(self._ctx)
        for credential_id in credential_ids:
            try:
                await service.advance_polling_target(credential_id)
            except Exception:
                _logger.exception(
                    "connect_poll_scanner_advance_failed",
                    credential_id=credential_id,
                )

    async def _due_credentials(self) -> list[str]:
        """Return credential IDs due for a poll tick this cycle.

        Filters to device-flow aux rows with an active ``encrypted_device_code``
        that hasn't hit the vendor-supplied TTL yet. Both flow entrypoints
        (connect-session flow, raw credential connect) write to this table
        during ``DeviceFlowHandler.begin``, and both clear it via
        ``on_finalise`` on success / ``_mark_terminal`` on failure — so
        "row present with non-NULL device_code" is a clean, flow-agnostic
        "is this in flight?" signal. RFC 8628 interval throttling happens
        per-credential inside ``DeviceFlowHandler.advance`` (via
        ``last_polled_at``), so we don't try to be clever with the query.
        """
        now = datetime.now(UTC)
        async with self._ctx.control_db.session() as session:
            stmt = (
                select(DeviceFlowCredential.id)
                .where(
                    DeviceFlowCredential.encrypted_device_code.is_not(None),
                    DeviceFlowCredential.device_code_expires_at.is_not(None),
                    DeviceFlowCredential.device_code_expires_at > now,
                )
                .order_by(DeviceFlowCredential.created_at.asc())
                .limit(_CANDIDATE_LIMIT)
            )
            result = await session.execute(stmt)
            return [str(row_id) for row_id in result.scalars().all()]
