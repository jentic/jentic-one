"""Connect session ORM model — the flow-agnostic pending-session record.

Owns the state machine that drives the agent-driven integration flow.
Flow-specific transient state lives on auxiliary tables keyed by
`credential_id` (e.g. `device_flow_credentials`), NOT here.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid


class ConnectSession(AuditableMixin, ControlBase):
    """A pending (or completed) connect session, keyed on the target credential."""

    __tablename__ = "connect_sessions"
    __table_args__ = (
        Index("ix_connect_sessions_agent", "agent_id", "state"),
        Index("ix_connect_sessions_credential", "credential_id"),
        Index("ix_connect_sessions_poll_token", "poll_token", unique=True),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cs"),
        server_default=func.generate_ksuid("cs"),
    )
    # Target credential (state=pending until the flow completes).
    credential_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Vendor registry key (e.g. "github"). FK-less; the registry is config-seeded.
    vendor: Mapped[str] = mapped_column(String(255), nullable=False)
    # Target agent to bind on success. FK-less: `agents` lives in the admin DB
    # (cross-DB FKs are forbidden by the architecture).
    agent_id: Mapped[str] = mapped_column(String(30), nullable=False)
    # Who called `:connect`. Actor type is derived from the id prefix
    # (`agt_`/`usr_`/`sa_`) via the existing identity utility — no separate col.
    initiator_actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    # State machine: created | confirmed | polling | connected | expired | failed
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    # As-requested by initiator (may differ from resolved).
    preferred_flow: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Actually used for the connect. Selects which aux table holds flow state.
    resolved_flow: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Identity echo result (e.g. "@octocat"); set on `connected`.
    connected_as: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Terminal failure code (see `error-taxonomy` — machine-readable slug).
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Opaque long-lived token given to the initiator so it can call
    # /connect-sessions/{id}/status without full session-read auth.
    poll_token: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional free-text detail useful for human debugging on the review page.
    # (Deliberately excluded from the plan's `connect_sessions`; kept here as a
    # nullable audit column with no operational meaning — logs remain the
    # authoritative source of failure detail.)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
