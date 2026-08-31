"""Identity and authentication payload schemas shared across surfaces."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jentic_one.shared.models import ActorType
from jentic_one.shared.models.actors import Origin


class LoginPayload(BaseModel):
    """Credentials for login."""

    email: str
    password: str


class TokenBundle(BaseModel):
    """JWT token response after successful authentication."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool = False


class ChangePasswordPayload(BaseModel):
    """Payload for changing own password."""

    current_password: str
    new_password: str


class Identity(BaseModel):
    """Unified identity object for all surfaces (JWT, opaque token, broker)."""

    model_config = ConfigDict(from_attributes=True)

    sub: str
    email: str = ""

    # ---------------------------------------------------------
    # Internal RBAC Capabilities
    # ---------------------------------------------------------
    permissions: list[str] = []

    # Inherited capabilities from the Agent's owner.
    parent_permissions: list[str] = []

    must_change_password: bool = False
    # Convenience default for trusted constructors (tests, resolvers that
    # already know the type). The *untrusted* boundary must never rely on
    # it: verify_token fails closed on a token without an explicit
    # actor_type claim (#862) — never default when parsing credentials.
    actor_type: ActorType = ActorType.USER
    parent_actor_id: str | None = None

    origin: Origin = Origin.API

    expires_at: datetime | None = None
    active: bool = True
    oauth_client_id: str | None = None
