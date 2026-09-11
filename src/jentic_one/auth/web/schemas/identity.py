"""Identity response schemas for GET /me."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator

from jentic_one.shared.schemas import ServedApiRef


class CredentialBindingEntry(BaseModel):
    """Direct agent↔credential binding summary for the /me response (theme 5 phase 1)."""

    credential_id: str
    # Human-readable credential name (control DB), same rationale as the
    # toolkit entry's name (issue #686). Null when the credential no longer
    # exists or its name could not be resolved.
    name: str | None = None
    bound_at: datetime
    # A suspended binding is a reversible cut-off: it keeps its permission
    # rules but is excluded from broker derivation, so the agent should not
    # expect to execute through it until an operator resumes it.
    suspended: bool = False
    # Shared permission rule set this binding points at (None = inline rules)
    # — tells the agent which policy object governs it (theme 5, Q-04).
    rule_set_id: str | None = None
    # The API this credential serves. Empty when unresolvable.
    serves: list[ServedApiRef] = []


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
    # Direct agent↔credential bindings (theme 5). The toolkit_bindings block
    # was removed with the toolkit surface (Phase 6b).
    credential_bindings: list[CredentialBindingEntry] = []


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
