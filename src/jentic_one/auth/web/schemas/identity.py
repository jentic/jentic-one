"""Identity response schemas for GET /me."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Field

from jentic_one.shared.schemas import ServedApiRef


class CredentialBindingEntry(BaseModel):
    """Direct agent↔credential binding summary for the /me response (theme 5 phase 1)."""

    credential_id: str = Field(description="ID of the bound credential.")
    # Human-readable credential name (control DB), same rationale as the
    # toolkit entry's name (issue #686). Null when the credential no longer
    # exists or its name could not be resolved.
    name: str | None = Field(
        default=None,
        description=(
            "Name of the bound credential; null if it no longer exists or cannot be resolved."
        ),
    )
    bound_at: datetime = Field(description="When the credential was bound (RFC 3339).")
    # A suspended binding is a reversible cut-off: it keeps its permission
    # rules but is excluded from broker derivation, so the agent should not
    # expect to execute through it until an operator resumes it.
    suspended: bool = Field(
        default=False,
        description=(
            "Whether the binding is suspended; the agent cannot execute through it until resumed."
        ),
    )
    # Why the binding is suspended (null = manual; ``api_deleted`` = the API
    # its credential serves was deleted).
    suspended_reason: str | None = Field(
        default=None,
        description=(
            "Why the binding is suspended: null for a manual suspension, `api_deleted`"
            " when the API the credential serves was deleted."
        ),
    )
    # Shared permission rule set this binding points at (None = inline rules)
    # — tells the agent which policy object governs it (theme 5, Q-04).
    rule_set_id: str | None = Field(
        default=None,
        description="ID of the shared permission rule set attached; null when inline rules apply.",
    )
    # The API this credential serves. Empty when unresolvable.
    serves: list[ServedApiRef] = Field(
        default=[], description="The APIs the bound credential serves; empty if unresolvable."
    )


class MeUser(BaseModel):
    """Identity response for a user actor."""

    type: Literal["user"] = Field(
        default="user", description="Actor type discriminator; always `user`."
    )
    id: str = Field(description="Unique ID of the user.")
    name: str = Field(description="Display name of the user.")
    email: str = Field(description="Email address of the user.")
    admin: bool = Field(description="Whether the caller's token holds `org:admin`.")
    status: str = Field(description="User status: `active` or `disabled`.")
    # For users this is the token's permission set rather than a live grant read:
    # users re-authenticate interactively, so the token view is effectively live
    # (no long-lived programmatic token to drift from grants the way an
    # agent's has — see #673).
    permissions: list[str] = Field(description="Permissions carried by the caller's token.")
    must_change_password: bool = Field(
        description=(
            "Whether the user must change their password; most endpoints return 403 until they do."
        )
    )


class MeAgent(BaseModel):
    """Identity response for an agent actor."""

    type: Literal["agent"] = Field(
        default="agent", description="Actor type discriminator; always `agent`."
    )
    id: str = Field(description="Unique ID of the agent.")
    name: str = Field(description="Human-readable name of the agent.")
    status: str = Field(
        description=(
            "Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled`"
            " or `archived`."
        )
    )
    # Permissions the agent currently holds in `actor_permission_grants` (the
    # source of truth an approver grants against), so whoami reflects an approved
    # grant immediately — independent of when the presented token was minted
    # (#673).
    permissions: list[str] = Field(
        description=(
            "Permissions currently granted to the agent, read live rather than from the token."
        )
    )
    # Permissions baked into the presented bearer token at mint time. When this is
    # a strict subset of `permissions`, a grant has landed that the current token
    # can't yet exercise; the agent should refresh/re-mint to pick it up.
    token_permissions: list[str] = Field(
        description=(
            "Permissions in the presented bearer token; if narrower than `permissions`,"
            " obtain a new token to pick up newer grants."
        )
    )
    parent_agent_id: str | None = Field(
        default=None, description="ID of the parent agent; null when the agent has no parent."
    )
    approved_by: str | None = Field(
        default=None, description="ID of the user who approved the agent; null if not approved."
    )
    # Direct agent↔credential bindings (theme 5). The toolkit_bindings block
    # was removed with the toolkit surface (Phase 6b).
    credential_bindings: list[CredentialBindingEntry] = Field(
        default=[], description="Credentials bound directly to the agent."
    )


MeResponse = Annotated[MeUser | MeAgent, Discriminator("type")]
