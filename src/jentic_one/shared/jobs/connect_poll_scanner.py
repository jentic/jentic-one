"""Background scanner that drives device-flow polling.

Owns the "poll the vendor" side of the device-flow state machine so it is
server-driven rather than client-driven — an agent that calls ``:confirm``
(or a user that clicks Connect on a manually-created device-flow
credential) and walks away sees progress land without another request
coming in. The ``/status`` HTTP surface is a pure stored-state read; this
scanner is the sole upstream trigger. Mirrors ``CredentialExpiryScanner``
/ ``CatalogUpdateScanner`` in shape.

Targets ``device_authorization_credentials`` — the aux row both entrypoints
(connect-session flow and raw-credential connect) write to. That row's
``encrypted_device_code IS NOT NULL`` is the natural "flow in flight"
signal: cleared by ``DeviceAuthorizationHandler.on_finalise`` on success, and by
``_mark_terminal`` on failure. When a candidate is found, the service
dispatches on whether a live ``ConnectSession`` wraps the credential —
session-driven flows advance the session's state machine, standalone
credentials advance ``credentials.state`` directly.

Callback flows (authorization_code) are advanced by the OAuth callback
route, not this scanner — they have no aux row here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
)

if TYPE_CHECKING:
    from jentic_one.shared.catalog import CatalogAutoImportProtocol
    from jentic_one.shared.context import Context

_logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECONDS = 2.0
_CANDIDATE_LIMIT = 100
# Per-tick concurrency cap on vendor advancement. Without a cap a slow
# vendor at the head of the batch stalls every credential behind it —
# each ``advance`` fires a 15s-timeout HTTP call, so serial iteration
# meant a 100-candidate tick could take ~25 minutes in the worst case
# even though 99 of them were healthy. Bounded ``asyncio.gather`` lets
# healthy vendors advance while a single slow one occupies exactly one
# concurrency slot. Cap is deliberately conservative so a scanner tick
# never dominates the shared upstream HTTP pool.
_ADVANCE_CONCURRENCY = 20


class ConnectPollScanner:
    """Periodically advances device-flow sessions in state=polling."""

    def __init__(
        self,
        ctx: Context,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        catalog_auto_importer: CatalogAutoImportProtocol | None = None,
    ) -> None:
        self._ctx = ctx
        self._poll_interval = poll_interval
        self._running = False
        # Threaded into every ``ConnectSessionService`` the scanner
        # builds so the scanner-driven device-flow finalise can
        # enqueue the vendor's catalog import — the request-scoped
        # ``get_connect_session_service`` reads the same value off
        # ``app.state``, but the scanner runs outside the request
        # scope, so we have to plumb it in explicitly.
        self._catalog_auto_importer = catalog_auto_importer

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
        picks up the remainder. Advances run concurrently under a bounded
        semaphore so one slow vendor doesn't head-of-line-block the rest
        of the batch. Each advancement is guarded so one bad row doesn't
        abort the batch.
        """
        service = ConnectSessionService(
            self._ctx, catalog_auto_importer=self._catalog_auto_importer
        )
        # Flow-agnostic TTL sweep first: sessions stuck in ``created``
        # (confirm never called) or ``polling`` with no device-code aux row
        # (abandoned auth-code popup) are invisible to the device-flow
        # query below — this is their only expiry driver, and it also
        # cleans up the upfront ``pending`` credential rows they minted.
        await service.expire_stale_sessions()
        credential_ids = await self._due_credentials()
        if not credential_ids:
            return
        semaphore = asyncio.Semaphore(_ADVANCE_CONCURRENCY)

        async def _guarded_advance(credential_id: str) -> None:
            async with semaphore:
                try:
                    await service.advance_polling_target(credential_id)
                except Exception:
                    _logger.exception(
                        "connect_poll_scanner_advance_failed",
                        credential_id=credential_id,
                    )

        await asyncio.gather(*(_guarded_advance(cid) for cid in credential_ids))

    async def _due_credentials(self) -> list[str]:
        """Return credential IDs due for a poll tick this cycle.

        Filters to device-flow aux rows with an active ``encrypted_device_code``.
        Both flow entrypoints (connect-session flow, raw credential connect)
        write to this table during ``DeviceAuthorizationHandler.begin``, and
        both clear it via ``on_finalise`` on success / ``_mark_terminal`` on
        failure — so "row present with non-NULL device_code" is a clean,
        flow-agnostic "is this in flight?" signal. Rows past their
        vendor-supplied ``device_code_expires_at`` are deliberately still
        selected: ``DeviceAuthorizationHandler.advance`` is where the
        ``expired`` terminal report comes from, and the terminal transition
        is what clears the aux row — filtering them out here would strand
        the session in ``polling`` forever (the vendor TTL is typically
        *shorter* than the session TTL).

        Multi-pod safety: the definitive per-credential poll-interval
        lease lives in ``DeviceAuthorizationCredentialRepository.try_claim_poll_slot``
        (atomic ``UPDATE`` guard in ``DeviceAuthorizationHandler.advance``).
        The ``with_for_update(skip_locked=True)`` here is the coarser
        candidate-selection lease: two scanner replicas that tick at the
        same moment don't both pull the same 100 IDs into memory, which
        would otherwise burn a wave of contending atomic-claim UPDATEs
        on the same rows. Postgres holds these locks only for the
        duration of this transaction (released immediately below) so
        we don't stall the vendor call on an idle-in-transaction lock;
        the true poll ownership is the atomic-claim CAS in ``advance``.
        A no-op on SQLite.
        """
        async with self._ctx.control_db.transaction() as session:
            stmt = (
                select(DeviceAuthorizationCredential.id)
                .where(
                    DeviceAuthorizationCredential.encrypted_device_code.is_not(None),
                    DeviceAuthorizationCredential.device_code_expires_at.is_not(None),
                )
                .order_by(DeviceAuthorizationCredential.created_at.asc())
                .limit(_CANDIDATE_LIMIT)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            return [str(row_id) for row_id in result.scalars().all()]
