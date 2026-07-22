"""Identity response schemas for GET /me."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator


class ApiRef(BaseModel):
    """An API a toolkit serves — the (vendor, name, version) identity tuple.

    Populated from the credentials bound to the toolkit, so an agent reading its
    own `whoami` can tell which APIs it can already call — and skip filing a
    provisioning plan for an API it is already bound to, instead of executing
    just to discover it's denied.
    """

    api_vendor: str
    api_name: str | None = None
    api_version: str | None = None


class ToolkitBindingEntry(BaseModel):
    """Toolkit binding summary for the /me response."""

    toolkit_id: str
    bound_at: datetime
    # The APIs this toolkit serves (derived from its bound credentials). Empty
    # when the toolkit has no credential bound yet.
    serves: list[ApiRef] = []


class MeUser(BaseModel):
    """Identity response for a user actor."""

    type: Literal["user"] = "user"
    id: str
    name: str
    email: str
    admin: bool
    status: str
    # For users `scopes` stays the token's permissions: users re-authenticate
    # interactively, so the token view is effectively live (no long-lived
    # programmatic token to drift from grants the way agents/service accounts
    # have — see #673).
    scopes: list[str]
    must_change_password: bool


class MeAgent(BaseModel):
    """Identity response for an agent actor."""

    type: Literal["agent"] = "agent"
    id: str
    name: str
    status: str
    # Scopes the agent currently holds in `actor_scope_grants` (the source of
    # truth an approver grants against), so whoami reflects an approved grant
    # immediately — independent of when the presented token was minted (#673).
    scopes: list[str]
    # Scopes baked into the presented bearer token at mint time. When this is a
    # strict subset of `scopes`, a grant has landed that the current token can't
    # yet exercise; the agent should refresh/re-mint to pick it up.
    token_scopes: list[str]
    parent_agent_id: str | None = None
    approved_by: str | None = None
    toolkit_bindings: list[ToolkitBindingEntry]


class MeServiceAccount(BaseModel):
    """Identity response for a service-account actor."""

    type: Literal["service_account"] = "service_account"
    id: str
    name: str
    status: str
    # Live grants from `actor_scope_grants` (same source of truth as agents), so
    # whoami reflects an approved grant immediately regardless of when the token
    # was minted (#673).
    scopes: list[str]
    # Scopes baked into the presented bearer token at mint time. A strict subset
    # of `scopes` means a grant has landed that the current token can't yet
    # exercise; the service account should re-mint to pick it up.
    token_scopes: list[str]
    registered_by: str
    approved_by: str | None = None


MeResponse = Annotated[MeUser | MeAgent | MeServiceAccount, Discriminator("type")]
