"""Agent request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from jentic_one.shared.web.sensitive import SENSITIVE

ScopeStr = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_:./-]+$")]


class AgentResponse(BaseModel):
    """Agent representation in API responses."""

    id: str
    name: str
    description: str | None = None
    owner_id: str | None = None
    registered_by: str
    parent_agent_id: str | None = None
    approved_by: str | None = None
    status: str
    denial_reason: str | None = None
    denied_by: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    has_api_key: bool = False


class AgentListResponse(BaseModel):
    """List of agents."""

    data: list[AgentResponse]
    has_more: bool
    next_cursor: str | None = None


class DenyRequest(BaseModel):
    """Request body for denying an agent."""

    reason: str = Field(min_length=1, max_length=1024)


class ClaimRequest(BaseModel):
    """Request body for claiming ownership of a self-registered agent."""

    # The single-use claim capability, presented once to take ownership. Marked
    # sensitive so the CLI's Layer-1 redactor masks it in output.
    token: str = Field(min_length=1, max_length=512, json_schema_extra=SENSITIVE)


class ToolkitBindingResponse(BaseModel):
    """Toolkit binding representation in API responses."""

    id: str
    agent_id: str
    toolkit_id: str
    bound_at: datetime


class ToolkitBindingListResponse(BaseModel):
    """List of toolkit bindings."""

    data: list[ToolkitBindingResponse]


class AgentPatchRequest(BaseModel):
    """Request body for partially updating an agent."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    owner_id: str | None = Field(default=None, min_length=1, max_length=255)


class AgentCreateRequest(BaseModel):
    """Request body for creating an agent manually."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    scopes: list[ScopeStr] | None = Field(default=None, max_length=100)


class AgentScopesRequest(BaseModel):
    """Request body for replacing an agent's scopes."""

    scopes: list[ScopeStr] = Field(max_length=100)


class AgentScopesResponse(BaseModel):
    """Response containing an agent's current scopes."""

    scopes: list[str]


class ApiKeyResponse(BaseModel):
    """Response containing a plaintext API key (shown once)."""

    key: str


class ApiKeyInfoResponse(BaseModel):
    """API key metadata — retrievable even after revocation."""

    id: str
    status: str
    created_at: datetime
    rotated_at: datetime | None = None
    created_by: str | None = None


class ApiKeyHistoryEntryResponse(BaseModel):
    """A single event in the API key audit trail."""

    id: str
    action: str
    reason: str | None = None
    actor_id: str | None = None
    occurred_at: datetime


class ApiKeyHistoryResponse(BaseModel):
    """Audit trail of API key operations."""

    data: list[ApiKeyHistoryEntryResponse]


class ToolkitBindRequest(BaseModel):
    """Request body for binding a toolkit."""

    toolkit_id: str = Field(min_length=1, max_length=255)


class ClientCredentialsResponse(BaseModel):
    """Response containing OAuth client credentials (shown once).

    The client_id is the agent's ID. The client_secret is shown only at
    generation time and cannot be retrieved later.
    """

    client_id: str
    client_secret: str
