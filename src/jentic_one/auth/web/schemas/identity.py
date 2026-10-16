"""Identity response schemas for GET /me."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Field

from jentic_one.shared.schemas import ServedApiRef
from jentic_one.shared.web import openapi_examples as ex

_EX_AGENT_PERMISSIONS = ["apis:read", "capabilities:execute"]


class CredentialBindingEntry(BaseModel):
    """Direct agent↔credential binding summary for the /me response (theme 5 phase 1)."""

    credential_id: str = Field(
        description="ID of the bound credential.", examples=[ex.CREDENTIAL_ID]
    )
    # Human-readable credential name (control DB), same rationale as the
    # toolkit entry's name (issue #686). Null when the credential no longer
    # exists or its name could not be resolved.
    name: str | None = Field(
        default=None,
        description=(
            "Name of the bound credential; null if it no longer exists or cannot be resolved."
        ),
        examples=["Stripe production key"],
    )
    bound_at: datetime = Field(
        description="When the credential was bound (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    # A suspended binding is a reversible cut-off: it keeps its permission
    # rules but is excluded from broker derivation, so the agent should not
    # expect to execute through it until an operator resumes it.
    suspended: bool = Field(
        default=False,
        description=(
            "Whether the binding is suspended; the agent cannot execute through it until resumed."
        ),
        examples=[False],
    )
    # Why the binding is suspended (null = manual; ``api_deleted`` = the API
    # its credential serves was deleted).
    suspended_reason: str | None = Field(
        default=None,
        description=(
            "Why the binding is suspended: null for a manual suspension, `api_deleted`"
            " when the API the credential serves was deleted."
        ),
        examples=["api_deleted"],
    )
    # Shared permission rule set this binding points at (None = inline rules)
    # — tells the agent which policy object governs it (theme 5, Q-04).
    rule_set_id: str | None = Field(
        default=None,
        description="ID of the shared permission rule set attached; null when inline rules apply.",
        examples=[ex.RULE_SET_ID],
    )
    # The API this credential serves. Empty when unresolvable.
    serves: list[ServedApiRef] = Field(
        default=[], description="The APIs the bound credential serves; empty if unresolvable."
    )


class MeUser(BaseModel):
    """Identity response for a user actor."""

    type: Literal["user"] = Field(
        default="user", description="Actor type discriminator; always `user`.", examples=["user"]
    )
    id: str = Field(description="Unique ID of the user.", examples=[ex.USER_ID])
    name: str = Field(description="Display name of the user.", examples=["Ada Lovelace"])
    email: str = Field(description="Email address of the user.", examples=["ada@example.com"])
    admin: bool = Field(
        description="Whether the caller's token holds `org:admin`.", examples=[True]
    )
    status: str = Field(description="User status: `active` or `disabled`.", examples=["active"])
    # For users this is the token's permission set rather than a live grant read:
    # users re-authenticate interactively, so the token view is effectively live
    # (no long-lived programmatic token to drift from grants the way an
    # agent's has — see #673).
    permissions: list[str] = Field(
        description="Permissions carried by the caller's token.",
        examples=[["org:admin", "agents:read"]],
    )
    must_change_password: bool = Field(
        description=(
            "Whether the user must change their password; most endpoints return 403 until they do."
        ),
        examples=[False],
    )


class MeAgent(BaseModel):
    """Identity response for an agent actor."""

    type: Literal["agent"] = Field(
        default="agent", description="Actor type discriminator; always `agent`.", examples=["agent"]
    )
    id: str = Field(description="Unique ID of the agent.", examples=[ex.AGENT_ID])
    name: str = Field(
        description="Human-readable name of the agent.", examples=["invoice-reconciler"]
    )
    status: str = Field(
        description=(
            "Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled`"
            " or `archived`."
        ),
        examples=["active"],
    )
    # Permissions the agent currently holds in `actor_permission_grants` (the
    # source of truth an approver grants against), so whoami reflects an approved
    # grant immediately — independent of when the presented token was minted
    # (#673).
    permissions: list[str] = Field(
        description=(
            "Permissions currently granted to the agent, read live rather than from the token."
        ),
        examples=[_EX_AGENT_PERMISSIONS],
    )
    # Permissions baked into the presented bearer token at mint time. When this is
    # a strict subset of `permissions`, a grant has landed that the current token
    # can't yet exercise; the agent should refresh/re-mint to pick it up.
    token_permissions: list[str] = Field(
        description=(
            "Permissions in the presented bearer token; if narrower than `permissions`,"
            " obtain a new token to pick up newer grants."
        ),
        examples=[_EX_AGENT_PERMISSIONS],
    )
    parent_agent_id: str | None = Field(
        default=None,
        description="ID of the parent agent; null when the agent has no parent.",
        examples=[ex.AGENT_ID],
    )
    approved_by: str | None = Field(
        default=None,
        description="ID of the user who approved the agent; null if not approved.",
        examples=[ex.USER_ID],
    )
    # Direct agent↔credential bindings (theme 5). The toolkit_bindings block
    # was removed with the toolkit surface (Phase 6b).
    credential_bindings: list[CredentialBindingEntry] = Field(
        default=[], description="Credentials bound directly to the agent."
    )


MeResponse = Annotated[MeUser | MeAgent, Discriminator("type")]
