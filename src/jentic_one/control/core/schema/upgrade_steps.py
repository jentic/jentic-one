"""UpgradeStep ORM model — the ledger of post-migration data steps.

Some upgrades need a data step that spans databases and therefore cannot live
inside a single Alembic tree (e.g. one that reads the control DB and the admin
DB). The migration runner (``python -m jentic_one.migrations.run``) performs
those steps once every tree is at head, and records each completed step here
by name.

A one-shot step runs **at most once per install**. That is a correctness
property, not an optimisation: a step that re-derives data from rows an
operator has since changed would silently undo that change on a later upgrade.
A repeatable step (``UpgradeStepSpec.repeatable``) runs on every full upgrade
and overwrites its row with its latest run. ``name`` is unique so two
concurrent runners collide on the insert instead of both recording (and both
performing) a one-shot step.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant


class UpgradeStep(AuditableMixin, ControlBase):
    """One completed post-migration data step (see module docstring)."""

    __tablename__ = "upgrade_steps"
    __table_args__ = (UniqueConstraint("name", name="uq_upgrade_steps_name"),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("ups"),
        server_default=func.generate_ksuid("ups"),
    )
    #: Stable step identifier, e.g. ``theme5_flatten_toolkits``.
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: jentic-one package version that performed the step.
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
    #: Step outcome counts (no secrets, no rule bodies) for the operator record.
    summary: Mapped[dict[str, Any] | None] = mapped_column(json_variant(), nullable=True)
