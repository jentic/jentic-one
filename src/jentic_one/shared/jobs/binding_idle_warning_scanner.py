"""Daily job that warns an agent's owner before a binding ends.

Only bindings whose owner turned the warning on (``idle_warning``) are
considered. Two deadlines are warned about, each once:

- the idle limit, when it is long enough for a warning to mean something (4
  days by default): one ``credential_grant.idle_expiring`` event when the idle
  deadline falls within the window (48 hours by default). ``idle_warned_at``
  marks it; a use or a renewal clears it, so each idle period warns once;
- the end date, when the binding was granted for at least as long: one
  ``credential_grant.expiring`` event when the end date falls within the
  window. ``expiry_warned_at`` marks it; a renewal clears it, so each end date
  warns once (a use does not move the end date, so it does not clear it).

Both events carry ``requires_action`` and are addressed to the agent's owner.
Each marker is set with a conditional UPDATE, so several replicas running the
job warn once between them.

Nothing here enforces expiry: a binding the job misses still ends on time,
because the broker checks the policy whenever it reads the binding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from sqlalchemy import TextClause, text

from jentic_one.shared.auth.grant_policy import (
    GRANT_POLICY_COLUMNS,
    GrantPolicy,
    expiry_warning_due,
    idle_warning_due,
)
from jentic_one.shared.config import GrantExpiryConfig
from jentic_one.shared.events import emit_event, summary_label
from jentic_one.shared.iso_duration import format_iso_duration
from jentic_one.shared.jobs.periodic import PeriodicScanner
from jentic_one.shared.models.events import EventSeverity, EventType

if TYPE_CHECKING:
    from jentic_one.shared.db.session import DatabaseSession

logger = structlog.get_logger(__name__)

_CANDIDATES = text(
    "SELECT b.id, b.agent_id, b.credential_id, a.name AS agent_name, "
    "b.idle_warned_at, b.expiry_warned_at, "
    f"{', '.join('b.' + c.strip() for c in GRANT_POLICY_COLUMNS.split(','))} "
    "FROM agent_credential_bindings b LEFT JOIN agents a ON a.id = b.agent_id "
    "WHERE b.idle_warning = true AND b.suspended = false AND ("
    "(b.idle_warned_at IS NULL AND b.max_idle_period >= :min_idle) "
    "OR (b.expiry_warned_at IS NULL AND b.expires_at IS NOT NULL))"
)

_MARK_IDLE = text(
    "UPDATE agent_credential_bindings SET idle_warned_at = :now "
    "WHERE id = :id AND idle_warned_at IS NULL"
)

_MARK_EXPIRY = text(
    "UPDATE agent_credential_bindings SET expiry_warned_at = :now "
    "WHERE id = :id AND expiry_warned_at IS NULL"
)


class BindingIdleWarningScanner(PeriodicScanner):
    """Warns owners, once per deadline, before an opted-in binding ends."""

    name: ClassVar[str] = "binding_idle_warning_scanner"

    def __init__(
        self, admin_db: DatabaseSession, *, config: GrantExpiryConfig | None = None
    ) -> None:
        self._admin_db = admin_db
        self._config = config or GrantExpiryConfig()
        self._poll_interval = float(self._config.idle_warning_interval)
        self._running = False

    async def _tick(self) -> None:
        await self.sweep()

    async def sweep(self, *, now: datetime | None = None) -> int:
        """Warn about every binding due a warning; return how many events were emitted."""
        now = now or datetime.now(UTC)
        window = timedelta(seconds=self._config.idle_warning_window)
        min_idle = self._config.idle_warning_min_length
        async with self._admin_db.read_session() as session:
            rows = (await session.execute(_CANDIDATES, {"min_idle": min_idle})).all()
        emitted = 0
        for row in rows:
            policy = GrantPolicy.from_row(row)
            if row.idle_warned_at is None:
                deadline = idle_warning_due(
                    policy, now=now, window=window, min_idle_period=min_idle
                )
                if deadline is not None and await self._warn(
                    row,
                    now=now,
                    mark=_MARK_IDLE,
                    type=EventType.CREDENTIAL_GRANT_IDLE_EXPIRING,
                    summary=f"ends if unused before {_when(deadline)}",
                    data={
                        "idle_expires_at": deadline.isoformat(),
                        "max_idle_period": format_iso_duration(row.max_idle_period),
                    },
                ):
                    emitted += 1
            if row.expiry_warned_at is None:
                end = expiry_warning_due(policy, now=now, window=window, min_length=min_idle)
                if end is not None and await self._warn(
                    row,
                    now=now,
                    mark=_MARK_EXPIRY,
                    type=EventType.CREDENTIAL_GRANT_EXPIRING,
                    summary=f"ends on {_when(end)}",
                    data={"expires_at": end.isoformat()},
                ):
                    emitted += 1
        if emitted:
            logger.info("binding_idle_warnings_emitted", count=emitted)
        return emitted

    async def _warn(
        self,
        row: Any,
        *,
        now: datetime,
        mark: TextClause,
        type: str,
        summary: str,
        data: dict[str, Any],
    ) -> bool:
        """Mark the binding and emit its warning in one transaction; whether it was emitted."""
        try:
            async with self._admin_db.transaction() as session:
                marked = await session.execute(mark, {"now": now, "id": row.id})
                if not getattr(marked, "rowcount", 0):
                    return False  # another replica warned first
                await emit_event(
                    session,
                    type=type,
                    severity=EventSeverity.WARNING,
                    summary=(
                        f"Agent {summary_label(row.agent_name, row.agent_id)}'s access to "
                        f"credential {row.credential_id} {summary}"
                    ),
                    # Binding events are the agent's, so its owner sees them.
                    created_by=row.agent_id,
                    requires_action=True,
                    data={"agent_id": row.agent_id, "credential_id": row.credential_id, **data},
                )
            return True
        except Exception:
            # The marker rolled back with the event, so it is retried next run.
            logger.warning(
                "binding_idle_warning_failed",
                agent_id=row.agent_id,
                credential_id=row.credential_id,
                event_type=type,
            )
            return False


def _when(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC")
