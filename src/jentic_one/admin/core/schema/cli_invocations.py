"""CliInvocation ORM model — one invocation token per vendor CLI command.

``jentic gh``/``cf``/``aws`` mint one per command on the auth surface, renew it
while the command runs and revoke it on exit. The broker's CLI facades resolve
it, then resolve the parent ``at_`` access token it was minted with, so the
agent's live status and permissions apply on every cache miss.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class CliInvocation(AuditableMixin, AdminBase):
    """A ``cit_`` invocation token, stored as a SHA-256 hash.

    ``parent_token_hash`` is the hash of the agent's ``at_`` access token that
    minted the row (no FK: resolvers look it up by hash, the way they resolve
    any access token, so a revoked or expired parent ends the child).
    ``credential_name`` is the agent's choice among its credentials for the
    vendor (by name, as the ``Jentic-Credential-Name`` request header selects
    one); the broker resolves it against the agent's live bindings on every
    call, so it grants nothing by itself.

    ``expires_at`` moves forward on renewal but never past ``hard_expires_at``,
    which is fixed at mint.
    """

    __tablename__ = "cli_invocations"
    __table_args__ = (
        Index("ix_cli_invocations_token_hash", "token_hash", unique=True),
        Index("ix_cli_invocations_actor_id", "actor_id"),
        Index("ix_cli_invocations_hard_expires_at", "hard_expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cli"),
        server_default=func.generate_ksuid("cli"),
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    vendor: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 32 lowercase hex chars (a W3C trace id), chosen by the server at mint.
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # Agent-reported subcommand path (e.g. ``pr create``), never arguments.
    command_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    hard_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
