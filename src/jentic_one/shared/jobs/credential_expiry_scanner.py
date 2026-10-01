"""Background sweep that emits ``credential.expiring_soon`` / ``credential.expired``.

OAuth token expiries (``oauth_tokens.expires_at``) live in the **control** DB;
events are written to the **admin** DB. Unlike the in-memory dedup used by the
circuit/auth-failure emitters, a token sits in the "expiring" state for the whole
warning window and across worker restarts, so the dedup is a persistent marker on
the token row (``expiring_soon_event_at`` / ``expired_event_at``).

The sweeper is modelled on ``WorkerLoop.run()``: a tick loop that catches and
logs non-cancellation errors so a transient DB hiccup never kills it. It sweeps
every ``credential_expiry_sweep_interval_ticks`` ticks (a token's expiry shifts on
the scale of hours, so a sweep per couple of minutes is ample).

Like every other emitter the emit is best-effort and carries only **non-secret**
identifiers (``credential_id``, ``expires_at``, ``api_vendor``) — never token
material, mirroring ``emit_credential_access``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos.oauth_token_repo import ExpiryEventKind, OAuthTokenRepository
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events import emit_event
from jentic_one.shared.models.events import EventSeverity, EventType

if TYPE_CHECKING:
    from jentic_one.shared.db.session import DatabaseSession

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECONDS = 2.0
_CANDIDATE_LIMIT = 100


class CredentialExpiryScanner:
    """Periodically emits credential-expiry events from stored OAuth token expiries."""

    def __init__(
        self,
        control_db: DatabaseSession,
        admin_db: DatabaseSession,
        *,
        security_config: SecurityConfig | None = None,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
    ) -> None:
        self._control_db = control_db
        self._admin_db = admin_db
        self._config = security_config or SecurityConfig()
        self._poll_interval = poll_interval
        self._running = False
        self._tick_count = 0

    async def run(self) -> None:
        """Main loop — sweep periodically until cancelled.

        A single tick must never kill the loop: a transient DB error during a
        sweep is caught and logged so the scanner keeps running (mirrors
        ``WorkerLoop.run``).
        """
        self._running = True
        logger.info("credential_expiry_scanner_started")
        try:
            while self._running:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("credential_expiry_scanner_tick_error")
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.info("credential_expiry_scanner_cancelled")
        finally:
            self._running = False
            logger.info("credential_expiry_scanner_stopped")

    async def _tick(self) -> None:
        self._tick_count += 1
        if self._tick_count % self._config.credential_expiry_sweep_interval_ticks != 0:
            return
        await self.sweep()

    async def sweep(self) -> int:
        """Run one sweep; emit events for newly-expiring/expired tokens.

        Returns the number of events emitted (useful for tests). Candidate rows
        are locked with ``SKIP LOCKED`` for the whole control-DB transaction, so
        concurrent replicas never double-emit, and the markers are stamped in
        that same transaction once each event has been written to the admin DB.
        """
        now = datetime.now(UTC)
        window_end = now + timedelta(hours=self._config.credential_expiring_soon_window_h)
        emitted = 0

        async with self._control_db.transaction() as control_session:
            candidates = await OAuthTokenRepository.list_expiry_candidates(
                control_session,
                now=now,
                window_end=window_end,
                limit=_CANDIDATE_LIMIT,
            )
            for token in candidates:
                kind: ExpiryEventKind = (
                    "expired"
                    if token.expires_at is not None and token.expires_at <= now
                    else "expiring_soon"
                )
                # Select only the vendor and owner columns — loading the full
                # Credential ORM object would eager-load its polymorphic credential
                # relationships (and is unnecessary; only non-secret columns go in
                # the event).
                row = (
                    await control_session.execute(
                        select(Credential.api_vendor, Credential.created_by).where(
                            Credential.id == token.credential_id
                        )
                    )
                ).one_or_none()
                api_vendor, owner_id = (row.api_vendor, row.created_by) if row else (None, None)
                try:
                    async with self._admin_db.transaction() as admin_session:
                        await self._emit(
                            admin_session,
                            token,
                            kind=kind,
                            api_vendor=api_vendor,
                            owner_id=owner_id,
                        )
                    await OAuthTokenRepository.mark_expiry_event_emitted(
                        control_session, token, kind=kind, at=now
                    )
                    emitted += 1
                except Exception:
                    # Best-effort: a failed emit leaves the marker null so the
                    # token is retried on the next sweep (never silently lost),
                    # and never breaks the rest of the batch.
                    logger.warning(
                        "emit_event_failed",
                        event_type=(
                            EventType.CREDENTIAL_EXPIRED
                            if kind == "expired"
                            else EventType.CREDENTIAL_EXPIRING_SOON
                        ),
                        credential_id=token.credential_id,
                    )
        if emitted:
            logger.info("credential_expiry_events_emitted", count=emitted)
        return emitted

    async def _emit(
        self,
        admin_session: object,
        token: OAuthToken,
        *,
        kind: ExpiryEventKind,
        api_vendor: str | None,
        owner_id: str | None,
    ) -> None:
        """Emit the expiry event, naming the credential owner in ``created_by``.

        Event reads are owner-scoped, so ``created_by`` is what lets the owner
        (and ``org:admin``) see their credential's expiry.
        """
        expires_at_iso = token.expires_at.isoformat() if token.expires_at is not None else None
        if kind == "expired":
            event_type = EventType.CREDENTIAL_EXPIRED
            severity = EventSeverity.ERROR
            requires_action = True
            summary = f"Credential {token.credential_id} has expired"
        else:
            event_type = EventType.CREDENTIAL_EXPIRING_SOON
            severity = EventSeverity.WARNING
            requires_action = False
            summary = f"Credential {token.credential_id} is expiring soon"
        await emit_event(
            admin_session,  # type: ignore[arg-type]
            type=event_type,
            severity=severity,
            summary=summary,
            requires_action=requires_action,
            created_by=owner_id,
            data={
                "credential_id": token.credential_id,
                "expires_at": expires_at_iso,
                "api_vendor": api_vendor,
            },
        )

    def stop(self) -> None:
        """Signal the scanner to stop after the current tick."""
        self._running = False
