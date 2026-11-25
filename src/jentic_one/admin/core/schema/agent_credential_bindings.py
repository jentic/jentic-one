"""AgentCredentialBinding ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class AgentCredentialBinding(AuditableMixin, AdminBase):
    """Direct binding between a broker-executing actor and a credential.

    ``agent_id`` holds the id of the agent (``agnt_…``) the binding
    authorizes. Before theme 8 it could also hold a service-account id
    (``sva_…``); the theme-8 migration re-keyed those onto successor agents
    and its sweep deleted the originals, and the Phase-4 drop refuses to run
    while any remain. The column carries no FK (it predates that cleanup);
    lifecycle cleanup is application-level (``AgentService.archive`` and
    ``AgentService.purge`` remove an agent's bindings). Every consumer (broker
    derivation SQL, repos) keys on it.

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
        UTCDateTime(), nullable=False, server_default=func.now()
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
    # When the binding stops authorizing calls (fixed expiry). NULL never
    # expires. Enforced at read time by the broker resolver, never by a sweep,
    # and independent of ``suspended``: resuming never extends it and renewing
    # never resumes. A renewal (PATCH) sets, moves or clears it.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Idle limit, in whole seconds: the binding ends once it has gone this
    # long without a use. None = no idle limit. Enforced at read time like
    # ``expires_at``; the idle clock runs from the later of ``last_used_at``
    # and ``renewed_at`` (falling back to ``bound_at``).
    max_idle_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When the agent last made a call through the binding, written by the
    # broker at most about once an hour. None = never used.
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # When the binding was granted or last renewed (PATCH): restarts the idle
    # clock without counting as a use.
    renewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Opt-in warning to the agent's owner before the binding ends — its idle
    # limit or its end date (always off by default). ``idle_warned_at`` marks
    # the idle warning (cleared by a use or a renewal, so each idle period
    # warns at most once); ``expiry_warned_at`` marks the end-date warning
    # (cleared by a renewal, so each end date warns once).
    idle_warning: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    idle_warned_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expiry_warned_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # What a call through the binding gets once it has ended: ``hold`` keeps
    # the first such call for the owner to renew and run; ``refuse`` answers a
    # plain 403 telling the agent to ask its owner (``AfterExpiry``).
    after_expiry: Mapped[str] = mapped_column(
        String(16), nullable=False, default="hold", server_default=text("'hold'")
    )
    # Use count (NULL = unlimited): the binding ends once ``quota_remaining``
    # reaches zero. The broker takes one use per call through it (failed or
    # refused calls included) with an
    # atomic decrement; a renewal restores it to ``max_quota``.
    max_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
