"""AgentCredentialBinding ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class AgentCredentialBinding(AuditableMixin, AdminBase):
    """Direct binding between a broker-executing actor and a credential.

    ``agent_id`` holds the id of the agent (``agnt_…``) the binding
    authorizes. Before theme 8 it could also hold a service-account id
    (``sva_…``); the theme-8 migration re-keyed those onto successor agents
    and its sweep deleted the originals, and the Phase-4 drop refuses to run
    while any remain. The column carries no FK (it predates that cleanup);
    lifecycle cleanup is application-level (``AgentService.delete`` removes
    an agent's bindings). Every consumer (broker derivation SQL, repos) keys
    on it.

    ``credential_id`` references a row in the control database, so it is a
    plain string column with no FK (cross-DB reference — same pattern as
    ``execution_records.credential_id``); the reference is resolved at the
    application layer. An agent may hold bindings to several credentials of
    the same API (multi-account); per-request disambiguation uses the
    ``Jentic-Credential-Name`` header.

    ``rule_set_id`` optionally points at a control-DB ``permission_rule_sets``
    row (cross-DB, FK-less, same as ``credential_id``). NULL means the
    binding's policy is its inline ``agent_permission_rules`` rows; non-NULL
    means the shared rule set's list applies instead (theme 5 rule grouping,
    Q-04 — N bindings can share one ordered list, so policy reuse survives
    the toolkit removal).
    """

    __tablename__ = "agent_credential_bindings"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "credential_id", name="uq_agent_credential_bindings_agent_credential"
        ),
        Index("ix_agent_credential_bindings_agent_id", "agent_id"),
        Index("ix_agent_credential_bindings_credential_id", "credential_id"),
        Index("ix_agent_credential_bindings_rule_set_id", "rule_set_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("acb"),
        server_default=func.generate_ksuid("acb"),
    )
    agent_id: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    credential_id: Mapped[str] = mapped_column(String(30), nullable=False)
    rule_set_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Reversible per-consumer cut-off (PR #35 review): a suspended binding is
    # excluded from broker derivation but keeps its permission rules, so
    # restoring access is a flag flip rather than a destructive re-bind that
    # loses authored policy. Unbind soft-suspends by default; row deletion is
    # an explicit purge.
    suspended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Why the binding is suspended. NULL is a manual suspension (the default
    # unbind); ``api_deleted`` is set by the registry when the API the
    # credential serves is deleted, so a later re-import under the same
    # identity does not silently re-adopt the binding. Cleared on resume.
    suspended_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
