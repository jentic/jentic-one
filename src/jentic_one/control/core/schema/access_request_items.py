"""AccessRequestItem ORM model — individual line items within an access request."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant

if TYPE_CHECKING:
    from jentic_one.control.core.schema.access_requests import AccessRequest


# The only (resource_type, action) combinations whose permission `rules` are
# actually enforced. Broker rules are keyed per (agent_id, credential_id)
# binding (see broker/repos/agent_rule_evaluator.py), so only a credential:bind
# has a key to apply them to. Rules on any other item type would silently
# produce an unrestricted binding — granted scope ≠ enforced scope. Both the
# default-rule substitution (repo) and the file/amend rejection (service) gate
# on this set, so future rule-bearing item types are opt-in in exactly one place.
RULE_BEARING_COMBINATIONS: frozenset[tuple[str, str]] = frozenset({("credential", "bind")})


class AccessRequestItem(AuditableMixin, ControlBase):
    """A single resource-action line item within an access request."""

    __tablename__ = "access_request_items"
    __table_args__ = (
        Index("ix_access_request_items_request_id", "access_request_id"),
        # NULLs in to_id/resource_id are treated as distinct by Postgres — broad
        # requests ("GET *") with NULL to_id or resource_id can be filed multiple
        # times. This is intentional: dedup only activates for fully-specified items.
        Index(
            "uq_access_request_items_pending_dedup",
            "actor_id",
            "resource_type",
            "action",
            "to_id",
            "resource_id",
            unique=True,
            postgresql_where="status = 'pending'",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("arqi"),
        server_default=func.generate_ksuid("arqi"),
    )
    access_request_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("access_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_reference: Mapped[dict[str, Any] | None] = mapped_column(json_variant(), nullable=True)
    to_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rules: Mapped[list[dict[str, Any]] | None] = mapped_column(json_variant(), nullable=True)
    # Optional pointer at a shared ``permission_rule_sets`` row (control DB,
    # FK-less by convention with the admin binding's pointer) — the alternative
    # policy carrier for a ``credential:bind`` item (theme-5 Phase 3, hard
    # problem 6: every bind must carry ``rules`` or a ``rule_set_id``).
    rule_set_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    applied_effects: Mapped[dict[str, Any] | None] = mapped_column(json_variant(), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    access_request: Mapped[AccessRequest] = relationship(back_populates="items")
