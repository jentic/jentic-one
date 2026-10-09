"""ConnectSessionOutcome ORM model — how a connect session ended.

A terminal session deletes its pending credential and the session row goes
with it (FK cascade), so the outcome is kept here, FK-less, for a bounded
retention window. Append-only: a row is written once, in the same transaction
as the session's terminal transition, and only the retention sweep removes it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow

#: The outcomes a session can end with.
OUTCOME_CONNECTED = "connected"
OUTCOME_REJECTED = "rejected"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_EXPIRED = "expired"
OUTCOME_FAILED = "failed"


class ConnectSessionOutcome(ControlBase):
    """The terminal outcome of one connect session.

    Does not use :class:`AuditableMixin`: rows are append-only and never carry
    an ``updated_at``. ``created_by`` is the session's initiator.
    """

    __tablename__ = "connect_session_outcomes"
    __table_args__ = (
        Index("ix_connect_session_outcomes_session_id", "session_id", unique=True),
        Index("ix_connect_session_outcomes_ended_at", "ended_at"),
        # Serves the rejection-cooldown lookup (an agent's recent outcomes).
        Index("ix_connect_session_outcomes_agent_ended_at", "agent_id", "ended_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cso"),
        server_default=func.generate_ksuid("cso"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now(), index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # The ended session's id. FK-less: the session row is usually gone.
    session_id: Mapped[str] = mapped_column(String(30), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Target, copied from the session (see ``ConnectSession.target_kind``).
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    vendor: Mapped[str] = mapped_column(String(255), nullable=False)
    api_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_flow: Mapped[str] = mapped_column(String(50), nullable=False)
    # connected | rejected | cancelled | expired | failed
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    # Machine-readable terminal code for ``failed`` (e.g. ``oauth_app_changed``).
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The credential a ``connected`` session left the agent bound to. FK-less:
    # the outcome outlives it. NULL for every other outcome.
    credential_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # The session's poll-token digest, so the token holder can still learn
    # the outcome once the session row is gone.
    poll_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
