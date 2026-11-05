"""AgentExplanation ORM model — an agent's own account of why it did something.

One row per subject: today a held execution, which its 202 asks the agent to
explain. Keyed by ``(subject_type, subject_id)`` rather than a column on the
subject, so other records an agent may be asked about can carry one too. The
text is the agent's claim, shown to the people who can see the subject; it is
cleaned before it is stored (``shared.agent_text``).
"""

from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.agent_text import MAX_AGENT_TEXT_LEN
from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class AgentExplanation(AuditableMixin, AdminBase):
    """An agent's explanation of one subject, written once.

    ``subject_id`` references the subject by id only (no FK): the subject
    table depends on ``subject_type``. ``agent_id`` is the agent that wrote it,
    which is also the agent the subject belongs to. Like the agent's other
    history, a row outlives the agent it names.
    """

    __tablename__ = "agent_explanations"
    __table_args__ = (
        Index(
            "uq_agent_explanations_subject",
            "subject_type",
            "subject_id",
            unique=True,
        ),
        Index("ix_agent_explanations_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("aex"),
        server_default=func.generate_ksuid("aex"),
    )
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(30), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str] = mapped_column(String(MAX_AGENT_TEXT_LEN), nullable=False)
