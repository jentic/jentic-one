"""ConnectSessionRevision ORM model — one version of what a connect session asks for.

Every session starts with revision 1, and each amend appends the next. Rows
are append-only and never updated: a confirm names the revision its reviewer
saw, and the session's ``current_revision_id`` says which one is live, so an
approval can only apply to the exact ask it was shown.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant
from jentic_one.shared.db.utils import utcnow


class ConnectSessionRevision(ControlBase):
    """One immutable version of a connect session's ask.

    Does not use :class:`AuditableMixin`: rows are append-only and never carry
    an ``updated_at``. ``created_by`` is whoever began or amended the session.
    """

    __tablename__ = "connect_session_revisions"
    __table_args__ = (
        Index(
            "ix_connect_session_revisions_session_id_number",
            "session_id",
            "number",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("csr"),
        server_default=func.generate_ksuid("csr"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now(), index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("connect_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 1 for the ask the session began with, then one more per amend.
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    # The ask as the initiator sent it: ``permission_rules``, ``scopes`` and
    # ``reason`` for a create session; ``rules``, ``scopes``, ``resume`` and
    # ``reason`` for an edit.
    proposal: Mapped[dict[str, object]] = mapped_column(json_variant(), nullable=False)
    # The ask resolved into what confirming it would leave in place:
    # ``{rules, scopes}`` for a create, plus ``suspended`` for an edit.
    result: Mapped[dict[str, object]] = mapped_column(json_variant(), nullable=False)
    # SHA-256 over the session's baseline, ``result`` and ``reason``.
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
