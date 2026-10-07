"""ToolkitFlatteningAck ORM model — the Phase-6a acknowledgement sentinel.

Theme-5 Phase 6b's drop migrations destroy the five legacy toolkit tables.
That is only safe once an operator has run the Phase-6a flattening job
against the *production* data, its verification queries passed (every legacy
``(agent, credential)`` pair exists as a direct ``agent_credential_bindings``
row), and the operator explicitly acknowledged the result. This table records
exactly that: one row per acknowledged verification run, written **only** by
``jentic_one flatten-toolkits --verify --acknowledge`` and only when the
verification passed in that same invocation.

Phase 6b's drop migrations must ``SELECT count(*) FROM
toolkit_flattening_acks`` and **raise** — not skip — when the table is empty
(guard-and-raise, mirroring the enterprise FK-ordering guard). The row
carries the counts the verification printed and the tool version that
produced them, so the migration note can cite them.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class ToolkitFlatteningAck(AuditableMixin, ControlBase):
    """One acknowledged Phase-6a verification run (see module docstring)."""

    __tablename__ = "toolkit_flattening_acks"

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("tfa"),
        server_default=func.generate_ksuid("tfa"),
    )
    #: When the operator acknowledged (the --acknowledge invocation's clock).
    acknowledged_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    #: count(distinct (agent_id, credential_id)) over the legacy toolkit join
    #: (agent_toolkit_bindings ⋈ toolkit_credential_bindings, dangling rows
    #: excluded) at verification time.
    legacy_pair_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: count(*) from agent_credential_bindings at verification time.
    direct_binding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Number of report findings the verification emitted (rule mismatches
    #: and the like — informational, not blocking).
    report_finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: jentic-one package version that ran the verification.
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
    #: True only when the verification that wrote this row also confirmed every
    #: resolvable ``execution_records.toolkit_name`` was backfilled (Phase-6b
    #: ``verify`` fails otherwise). Rows written by a pre-6b tool default to
    #: False, so the drop gate refuses them — their verification never checked
    #: the backfill, and the drop would lose those names for good.
    execution_names_backfilled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Digest of the legacy control toolkit rows (``toolkits``,
    #: ``toolkit_credential_bindings``, ``toolkit_permission_rules`` ids) the
    #: verification saw. The drop gate recomputes it and refuses a stale ack
    #: (rows added or removed after acknowledging — e.g. an old replica during
    #: a rolling upgrade). NULL on pre-6b rows.
    control_state_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The same digest over the admin ``agent_toolkit_bindings`` ids; checked by
    #: the admin drop when it can read this table (shared Postgres database).
    admin_state_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
