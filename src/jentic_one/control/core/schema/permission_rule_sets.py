"""PermissionRuleSet ORM models — shared, reusable ordered rule lists (theme 5, Q-04).

Per-binding rules alone fan out multiplicatively (every ``(agent,
credential)`` pair hand-authors its own list) and delete policy reuse. A
``permission_rule_sets`` row names one ordered rule list; N
``agent_credential_bindings`` rows may point at it via their (cross-DB,
FK-less) ``rule_set_id`` column, NULL there meaning the binding's inline
``agent_permission_rules`` rows apply. ``permissions:test`` and "revoke this
operation everywhere" stay single-place edits.

Every caller with a rule-set read permission can see every set: a set
carries policy, not secrets. Who may attach a set to a binding and who may
edit it is decided in ``CredentialService``; ``curated`` is the stable signal
those gates read. A set is curated when an ``org:admin`` (or a system job)
creates it. A curated set is attachable by any ``credentials:write`` holder
and editable only by ``org:admin``; any other set is attachable and editable
by its creator or ``org:admin``.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant


class PermissionRuleSet(AuditableMixin, ControlBase):
    """A named, shareable ordered permission-rule list."""

    __tablename__ = "permission_rule_sets"
    __table_args__ = (UniqueConstraint("name", name="uq_permission_rule_sets_name"),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("prs"),
        server_default=func.generate_ksuid("prs"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Server default only (no Python-side default), so a Core insert that
    # names its columns works against a pre-curated schema; see
    # ``PermissionRuleSetRepository.insert_for_flattening``.
    curated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class PermissionRuleSetRule(AuditableMixin, ControlBase):
    """One rule in a rule set's ordered list.

    Same rule columns and default-deny/first-match-wins semantics as
    ``agent_permission_rules``; keyed by ``rule_set_id`` instead of the
    binding pair. ``UNIQUE (rule_set_id, sequence)`` keeps evaluation order
    backend-independent, exactly as ``uq_agent_permission_rules_binding_seq``
    does for inline rules.
    """

    __tablename__ = "permission_rule_set_rules"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "sequence", name="uq_permission_rule_set_rules_seq"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("prr"),
        server_default=func.generate_ksuid("prr"),
    )
    rule_set_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("permission_rule_sets.id", ondelete="CASCADE"), nullable=False
    )
    # String(16) holds "require-approval" (16 chars); widened from the original
    # String(10) which only fit "allow" / "deny".
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    methods: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    match_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="regex", server_default=text("'regex'")
    )
    operations: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
