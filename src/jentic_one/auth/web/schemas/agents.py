"""Agent request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.schemas import ServedApiRef
from jentic_one.shared.web.sensitive import SENSITIVE

PermissionStr = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_:./-]+$")]


class AgentResponse(BaseModel):
    """Agent representation in API responses."""

    id: str = Field(description="Unique ID of the agent.")
    name: str = Field(description="Human-readable name of the agent.")
    description: str | None = Field(
        default=None, description="Free-text description of the agent; null if not set."
    )
    owner_id: str | None = Field(
        default=None,
        description="ID of the user who owns the agent; null for an unclaimed self-registration.",
    )
    registered_by: str = Field(
        description="ID of the actor that registered the agent, or `self` for a self-registration."
    )
    parent_agent_id: str | None = Field(
        default=None, description="ID of the parent agent; null when the agent has no parent."
    )
    approved_by: str | None = Field(
        default=None, description="ID of the user who approved the agent; null if not approved."
    )
    status: str = Field(
        description=(
            "Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled`"
            " or `archived`."
        )
    )
    denial_reason: str | None = Field(
        default=None, description="Reason given when the agent was denied; null if not denied."
    )
    denied_by: str | None = Field(
        default=None, description="ID of the actor that denied the agent; null if not denied."
    )
    created_at: datetime = Field(description="When the agent was created (RFC 3339).")
    approved_at: datetime | None = Field(
        default=None,
        description="When the agent was approved (RFC 3339); null if not approved.",
    )
    has_api_key: bool = Field(
        default=False,
        description=(
            "Whether the agent currently has an active API key; only populated by"
            " `GET /agents/{agent_id}`, and false in other responses."
        ),
    )


class AgentListResponse(BaseModel):
    """List of agents."""

    data: list[AgentResponse] = Field(description="The page of agents.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )


class DenyRequest(BaseModel):
    """Request body for denying an agent."""

    reason: str = Field(min_length=1, max_length=1024, description="Why the agent is being denied.")


class ClaimRequest(BaseModel):
    """Request body for claiming ownership of a self-registered agent."""

    # The single-use claim capability, presented once to take ownership. Marked
    # sensitive so the CLI's Layer-1 redactor masks it in output.
    token: str = Field(
        min_length=1,
        max_length=512,
        json_schema_extra=SENSITIVE,
        description="Single-use claim token returned when the agent self-registered.",
    )


class CredentialBindingResponse(BaseModel):
    """Direct agent↔credential binding representation in API responses."""

    id: str = Field(description="Unique ID of the binding.")
    agent_id: str = Field(description="ID of the agent the credential is bound to.")
    credential_id: str = Field(description="ID of the bound credential.")
    # Human-readable credential name (control DB); None when unresolvable.
    name: str | None = Field(
        default=None, description="Name of the bound credential; null if it cannot be resolved."
    )
    bound_at: datetime = Field(description="When the credential was bound (RFC 3339).")
    suspended: bool = Field(
        description="Whether the binding is suspended, cutting off the agent's access to it."
    )
    # Why the binding is suspended: null for a manual suspension (the default
    # unbind), ``api_deleted`` when the API the credential serves was deleted.
    # Cleared when the binding is resumed.
    suspended_reason: str | None = Field(
        default=None,
        description=(
            "Why the binding is suspended: null for a manual suspension, `api_deleted`"
            " when the API the credential serves was deleted; cleared on resume."
        ),
    )
    # Shared permission rule set the binding points at (None = inline rules).
    rule_set_id: str | None = Field(
        default=None,
        description="ID of the shared permission rule set attached; null when inline rules apply.",
    )
    serves: list[ServedApiRef] = Field(
        default=[], description="The APIs the bound credential serves; empty if unresolvable."
    )


class CredentialBindingListResponse(BaseModel):
    """List of direct credential bindings."""

    data: list[CredentialBindingResponse] = Field(
        description="The agent's direct credential bindings."
    )


class AgentPatchRequest(BaseModel):
    """Request body for partially updating an agent."""

    name: str | None = Field(
        default=None, min_length=1, max_length=255, description="New name for the agent."
    )
    description: str | None = Field(
        default=None, max_length=1024, description="New description for the agent."
    )
    owner_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="ID of the user to transfer ownership to; `org:admin` only.",
    )


class AgentCreateRequest(BaseModel):
    """Request body for creating an agent manually."""

    # Unknown keys are a 422: a body that spells the grant list ``scopes`` would
    # otherwise be ignored, leave ``permissions`` unset, and grant the full
    # default baseline.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255, description="Human-readable name of the agent.")
    description: str | None = Field(
        default=None, max_length=1024, description="Optional free-text description of the agent."
    )
    permissions: list[PermissionStr] | None = Field(
        default=None,
        max_length=100,
        description=(
            "Permissions to grant the agent; each must be grantable by the caller."
            " Omit to grant the default agent permission set."
        ),
    )


class AgentPermissionsRequest(BaseModel):
    """Request body for replacing an agent's permissions."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[PermissionStr] = Field(
        max_length=100, description="The full set of permissions the agent should hold."
    )


class AgentPermissionsResponse(BaseModel):
    """Response containing an agent's current permissions."""

    permissions: list[str] = Field(description="Permissions currently granted to the agent.")


class ApiKeyResponse(BaseModel):
    """Response containing a plaintext API key (shown once)."""

    key: str = Field(description="The plaintext API key; shown only once, store it securely.")


class ApiKeyInfoResponse(BaseModel):
    """API key metadata — retrievable even after revocation."""

    id: str = Field(description="Unique ID of the API key record.")
    status: str = Field(description="Key status: `active`, or `revoked` once revoked.")
    created_at: datetime = Field(description="When the API key was first created (RFC 3339).")
    rotated_at: datetime | None = Field(
        default=None,
        description="When the API key was last rotated (RFC 3339); null if never rotated.",
    )
    created_by: str | None = Field(
        default=None, description="ID of the actor that created the API key."
    )


class ApiKeyHistoryEntryResponse(BaseModel):
    """A single event in the API key audit trail."""

    id: str = Field(description="Unique ID of the audit entry.")
    action: str = Field(description="Audited action, for example `rotate` or `revoke`.")
    reason: str | None = Field(
        default=None, description="Structured reason: `api_key_rotated` or `api_key_revoked`."
    )
    actor_id: str | None = Field(
        default=None, description="ID of the actor that performed the action."
    )
    occurred_at: datetime = Field(description="When the action occurred (RFC 3339).")


class ApiKeyHistoryResponse(BaseModel):
    """Audit trail of API key operations."""

    data: list[ApiKeyHistoryEntryResponse] = Field(description="API key events, newest first.")


class CredentialBindRequest(BaseModel):
    """Request body for directly binding a credential to an agent."""

    credential_id: str = Field(
        min_length=1, max_length=255, description="ID of the credential to bind to the agent."
    )


class JwksUpdateRequest(BaseModel):
    """Request body for updating an agent's JWKS (public keys).

    The JWKS must contain at least one Ed25519 public key and must not
    contain any private key material.
    """

    jwks: dict[str, Any] = Field(description="JWKS containing public keys")
