"""A ``credential.accessed`` audit event held back for the caller's own transaction.

A broker call that injects a stored credential writes two admin-DB records: the
``credential.accessed`` audit event and the execution record (+ its lifecycle
event). Writing the audit event in the execution record's transaction makes a
successful call cost one admin write session instead of two (#1702).

The pending event is therefore owned by the call-site, which must write it on
every exit path:

* the persistence step calls :meth:`PendingCredentialAccess.emit` inside the
  same transaction as the execution record, and
* every call-site wraps the injected call in a guard that calls
  :meth:`PendingCredentialAccess.record_standalone` when it exits, which writes
  the event in its own transaction **unless** an earlier ``emit`` was committed.
  A failure on the way to persistence (an exception between injection and the
  upstream call, an upstream failure on the streaming path, a rolled-back
  persistence transaction, a client disconnect) still leaves the audit row.

The event keeps the moment of access as its ``created_at``, so it sorts before
the execution's lifecycle event exactly as an immediately-written one would.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.events import emit_credential_access

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class PendingCredentialAccess:
    """The non-secret fields of one ``credential.accessed`` event, not yet written.

    ``recorded`` turns true only once the transaction that carries the event
    has **committed** (session ``after_commit`` hook) — never on flush. A
    rollback of that session first detaches the event, so a rolled-back
    persistence transaction leaves it pending and :meth:`record_standalone`
    still writes it.
    """

    actor_id: str
    actor_type: str
    credential_id: str
    provider: str
    wire_type: str
    api_vendor: str
    api_name: str
    api_version: str
    credential_owner: str | None = None
    credential_name: str | None = None
    actor_name: str | None = None
    trace_id: str | None = None
    accessed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _recorded: bool = field(default=False, init=False, repr=False)
    # The session whose open transaction holds the flushed (uncommitted) event.
    _pending_in: Session | None = field(default=None, init=False, repr=False)

    @property
    def recorded(self) -> bool:
        """Whether a committed transaction carries the event."""
        return self._recorded

    async def write(self, session: AsyncSession) -> str:
        """Flush the event into ``session`` and return its id (no commit tracking).

        For a caller that commits the event itself, right away; deferred
        call-sites use :meth:`emit`. Raises like :func:`emit_credential_access`.
        """
        return await emit_credential_access(
            session,
            actor_id=self.actor_id,
            actor_type=self.actor_type,
            credential_id=self.credential_id,
            provider=self.provider,
            wire_type=self.wire_type,
            api_vendor=self.api_vendor,
            api_name=self.api_name,
            api_version=self.api_version,
            credential_owner=self.credential_owner,
            credential_name=self.credential_name,
            actor_name=self.actor_name,
            trace_id=self.trace_id,
            created_at=self.accessed_at,
        )

    async def emit(self, session: AsyncSession) -> None:
        """Write the deferred event in the caller's transaction (flush-only).

        A no-op once the event is recorded or already flushed into ``session``,
        so overlapping persistence paths cannot double-write it. Raises like
        :func:`emit_credential_access` — a failed audit write aborts the
        caller's transaction rather than passing silently.
        """
        sync = session.sync_session
        if self._recorded or self._pending_in is sync:
            return
        await self.write(session)
        self._pending_in = sync

        # Listeners stay registered for the (short-lived) session's lifetime;
        # once the event is settled they are no-ops. Removing them from inside
        # a dispatch would mutate the listener collection mid-iteration.
        def _after_commit(committed: Session) -> None:
            if self._pending_in is committed:
                self._pending_in = None
                self._recorded = True

        def _after_rollback(rolled_back: Session) -> None:
            if self._pending_in is rolled_back:
                self._pending_in = None

        sa_event.listen(sync, "after_commit", _after_commit)
        sa_event.listen(sync, "after_rollback", _after_rollback)

    async def record_standalone(self, db: DatabaseSession) -> None:
        """Write the event in its own transaction unless it is already recorded.

        The exit guard for every path that did not commit the event alongside
        an execution record. Shielded so a cancellation (client disconnect)
        cannot interrupt the write. A failure is logged with the non-secret
        identifiers instead of raised: the guard runs while another exception
        may be propagating, and masking it would hide the real failure.
        """
        if self._recorded:
            return
        try:
            await asyncio.shield(self._write_standalone(db))
        except Exception:
            logger.error(
                "credential_access_audit_write_failed",
                credential_id=self.credential_id,
                actor_id=self.actor_id,
                trace_id=self.trace_id,
                exc_info=True,
            )

    async def _write_standalone(self, db: DatabaseSession) -> None:
        async with db.transaction() as session:
            await self.emit(session)
