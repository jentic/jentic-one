"""Agent request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.schemas import ServedApiRef
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.sensitive import SENSITIVE

PermissionStr = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_:./-]+$")]

_EX_AGENT_NAME = "invoice-reconciler"
_EX_AGENT_DESCRIPTION = "Reconciles Stripe invoices against the ledger every night."
_EX_PERMISSIONS = ["apis:read", "capabilities:execute"]
_EX_DENIAL_REASON = "Requested permissions exceed the agent's stated purpose."
_EX_CREDENTIAL_NAME = "Stripe production key"


class AgentResponse(BaseModel):
    """Agent representation in API responses."""

    id: str = Field(description="Unique ID of the agent.", examples=[ex.AGENT_ID])
    name: str = Field(description="Human-readable name of the agent.", examples=[_EX_AGENT_NAME])
    description: str | None = Field(
        default=None,
        description="Free-text description of the agent; null if not set.",
        examples=[_EX_AGENT_DESCRIPTION],
    )
    owner_id: str | None = Field(
        default=None,
        description="ID of the user who owns the agent; null for an unclaimed self-registration.",
        examples=[ex.USER_ID],
    )
    registered_by: str = Field(
        description="ID of the actor that registered the agent, or `self` for a self-registration.",
        examples=[ex.USER_ID],
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
    status: str = Field(
        description=(
            "Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled`"
            " or `archived`."
        ),
        examples=["active"],
    )
    denial_reason: str | None = Field(
        default=None,
        description="Reason given when the agent was denied; null if not denied.",
        examples=[_EX_DENIAL_REASON],
    )
    denied_by: str | None = Field(
        default=None,
        description="ID of the actor that denied the agent; null if not denied.",
        examples=[ex.USER_ID],
    )
    created_at: datetime = Field(
        description="When the agent was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    approved_at: datetime | None = Field(
        default=None,
        description="When the agent was approved (RFC 3339); null if not approved.",
        examples=["2026-01-15T09:45:00Z"],
    )
    has_api_key: bool = Field(
        default=False,
        description=(
            "Whether the agent currently has an active API key; only populated by"
            " `GET /agents/{agent_id}`, and false in other responses."
        ),
        examples=[True],
    )


class AgentListResponse(BaseModel):
    """List of agents."""

    data: list[AgentResponse] = Field(description="The page of agents.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )


class DenyRequest(BaseModel):
    """Request body for denying an agent."""

    reason: str = Field(
        min_length=1,
        max_length=1024,
        description="Why the agent is being denied.",
        examples=[_EX_DENIAL_REASON],
    )


class ClaimRequest(BaseModel):
    """Request body for claiming ownership of a self-registered agent."""

    # The single-use claim capability, presented once to take ownership. Marked
    # sensitive so the CLI's Layer-1 redactor masks it in output.
    token: str = Field(
        min_length=1,
        max_length=512,
        json_schema_extra=SENSITIVE,
        description="Single-use claim token returned when the agent self-registered.",
        examples=[ex.OPAQUE_TOKEN],
    )


class CredentialBindingResponse(BaseModel):
    """Direct agent↔credential binding representation in API responses."""

    id: str = Field(description="Unique ID of the binding.", examples=[ex.CREDENTIAL_BINDING_ID])
    agent_id: str = Field(
        description="ID of the agent the credential is bound to.", examples=[ex.AGENT_ID]
    )
    credential_id: str = Field(
        description="ID of the bound credential.", examples=[ex.CREDENTIAL_ID]
    )
    # Human-readable credential name (control DB); None when unresolvable.
    name: str | None = Field(
        default=None,
        description="Name of the bound credential; null if it cannot be resolved.",
        examples=[_EX_CREDENTIAL_NAME],
    )
    bound_at: datetime = Field(
        description="When the credential was bound (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    suspended: bool = Field(
        description="Whether the binding is suspended, cutting off the agent's access to it.",
        examples=[False],
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
        examples=["api_deleted"],
    )
    # Shared permission rule set the binding points at (None = inline rules).
    rule_set_id: str | None = Field(
        default=None,
        description="ID of the shared permission rule set attached; null when inline rules apply.",
        examples=[ex.RULE_SET_ID],
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
        default=None,
        min_length=1,
        max_length=255,
        description="New name for the agent.",
        examples=[_EX_AGENT_NAME],
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="New description for the agent.",
        examples=[_EX_AGENT_DESCRIPTION],
    )
    owner_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="ID of the user to transfer ownership to; `org:admin` only.",
        examples=[ex.USER_ID],
    )


class AgentCreateRequest(BaseModel):
    """Request body for creating an agent manually."""

    # Unknown keys are a 422: a body that spells the grant list ``scopes`` would
    # otherwise be ignored, leave ``permissions`` unset, and grant the full
    # default baseline.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable name of the agent.",
        examples=[_EX_AGENT_NAME],
    )
    description: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional free-text description of the agent.",
        examples=[_EX_AGENT_DESCRIPTION],
    )
    permissions: list[PermissionStr] | None = Field(
        default=None,
        max_length=100,
        description=(
            "Permissions to grant the agent; each must be grantable by the caller."
            " Omit to grant the default agent permission set."
        ),
        examples=[_EX_PERMISSIONS],
    )


class AgentPermissionsRequest(BaseModel):
    """Request body for replacing an agent's permissions."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[PermissionStr] = Field(
        max_length=100,
        description="The full set of permissions the agent should hold.",
        examples=[_EX_PERMISSIONS],
    )


class AgentPermissionsResponse(BaseModel):
    """Response containing an agent's current permissions."""

    permissions: list[str] = Field(
        description="Permissions currently granted to the agent.", examples=[_EX_PERMISSIONS]
    )


class ApiKeyResponse(BaseModel):
    """Response containing a plaintext API key (shown once)."""

    key: str = Field(
        description="The plaintext API key; shown only once, store it securely.",
        examples=["jak_example-secret"],  # pragma: allowlist secret
    )


class ApiKeyInfoResponse(BaseModel):
    """API key metadata — retrievable even after revocation."""

    id: str = Field(description="Unique ID of the API key record.", examples=[ex.AGENT_API_KEY_ID])
    status: str = Field(
        description="Key status: `active`, or `revoked` once revoked.", examples=["active"]
    )
    created_at: datetime = Field(
        description="When the API key was first created (RFC 3339).",
        examples=["2026-01-15T09:30:00Z"],
    )
    rotated_at: datetime | None = Field(
        default=None,
        description="When the API key was last rotated (RFC 3339); null if never rotated.",
        examples=["2026-01-20T14:00:00Z"],
    )
    created_by: str | None = Field(
        default=None,
        description="ID of the actor that created the API key.",
        examples=[ex.USER_ID],
    )


class ApiKeyHistoryEntryResponse(BaseModel):
    """A single event in the API key audit trail."""

    id: str = Field(description="Unique ID of the audit entry.", examples=[ex.AUDIT_ID])
    action: str = Field(
        description="Audited action, for example `rotate` or `revoke`.", examples=["rotate"]
    )
    reason: str | None = Field(
        default=None,
        description="Structured reason: `api_key_rotated` or `api_key_revoked`.",
        examples=["api_key_rotated"],
    )
    actor_id: str | None = Field(
        default=None,
        description="ID of the actor that performed the action.",
        examples=[ex.USER_ID],
    )
    occurred_at: datetime = Field(
        description="When the action occurred (RFC 3339).", examples=["2026-01-20T14:00:00Z"]
    )


class ApiKeyHistoryResponse(BaseModel):
    """Audit trail of API key operations."""

    data: list[ApiKeyHistoryEntryResponse] = Field(description="API key events, newest first.")


class CredentialBindRequest(BaseModel):
    """Request body for directly binding a credential to an agent."""

    credential_id: str = Field(
        min_length=1,
        max_length=255,
        description="ID of the credential to bind to the agent.",
        examples=[ex.CREDENTIAL_ID],
    )


class JwksUpdateRequest(BaseModel):
    """Request body for updating an agent's JWKS (public keys).

    The JWKS must contain at least one Ed25519 public key and must not
    contain any private key material.
    """

    jwks: dict[str, Any] = Field(
        description="JWKS containing public keys",
        examples=[
            {
                "keys": [
                    {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        # RFC 8037 appendix A.2 public-key test vector (not a
                        # secret).
                        # pragma: allowlist nextline secret
                        "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
                        "kid": "agent-key-1",
                    }
                ]
            }
        ],
    )
