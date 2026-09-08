"""OAuthClientGrant ORM model — the consent→agent binding.

A row is minted at consent time for ``consent_model='agent'`` clients: it binds
one OAuth client (by its PUBLIC ``client_id`` string — the same join key the
token-lineage columns carry) to exactly ONE of the consenting user's
admin-approved agents, with the D2-intersected scope set frozen at consent.
Tokens exchanged from a grant-bearing authorization code resolve to
actor=AGENT and carry ``oauth_grant_id`` lineage; both resolvers re-check this
row's ``status`` live on every verdict, so revoking the grant is an
independent kill switch beside client-deactivate and agent-disable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus


class OAuthClientGrant(AuditableMixin, AdminBase):
    """A user's consent binding an OAuth client connection to one agent."""

    __tablename__ = "oauth_client_grants"
    __table_args__ = (
        Index("ix_oauth_client_grants_oauth_client_id", "oauth_client_id"),
        Index("ix_oauth_client_grants_agent_id", "agent_id"),
        Index("ix_oauth_client_grants_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("ocg"),
        server_default=func.generate_ksuid("ocg"),
    )
    # The client's public client_id string, NOT the oauth_clients.id ksuid —
    # token rows already join on the public id (access_tokens.oauth_client_id),
    # so the grant uses the same single join key (D3).
    oauth_client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The consenting human. Plain id columns (no FK): grants must survive as
    # audit-relevant history even through actor lifecycle edge cases; liveness
    # is enforced by the resolvers' agent-status re-check, not by referential
    # cascades.
    user_id: Mapped[str] = mapped_column(String(30), nullable=False)
    # The bound actor — exactly one agent per grant, fixed at consent (D2).
    agent_id: Mapped[str] = mapped_column(String(30), nullable=False)
    # The granted set: requested ∩ client allowlist ∩ agent live scopes at
    # consent time, OIDC passthrough scopes stripped (D2/D11).
    scopes: Mapped[list[str]] = mapped_column(json_variant(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=OAuthGrantStatus.ACTIVE.value,
        server_default=OAuthGrantStatus.ACTIVE.value,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
