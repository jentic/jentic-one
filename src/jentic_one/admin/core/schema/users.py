"""User ORM model."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, column
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class User(AuditableMixin, AdminBase):
    """Platform user account."""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_email_lower", sa_func.lower(column("email")), unique=True),
        Index("ix_users_invite_state", "invite_state"),
        Index("ix_users_active", "active"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("usr"),
        server_default=func.generate_ksuid("usr"),
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    auth_provider: Mapped[str] = mapped_column(String(16), nullable=False, server_default="local")
    external_subject_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    invite_state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    # Login-session generation, carried by every login JWT as ``sgen``: the
    # shared verifier refuses a user token whose claim differs (#1139). Bumped
    # on disable, delete and password change/reset, so re-enabling a user does
    # not revive tokens minted before the disable.
    session_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
