"""Web request/response models for the toolkits API."""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Request models ---


class PermissionRuleSchema(BaseModel):
    """Permission rule for a toolkit-credential binding.

    Rules are evaluated first-match-wins. If no rule matches, the request is
    denied (default-deny). A binding with zero rules therefore blocks all
    operations — users must explicitly add at least one allow rule.
    """

    model_config = ConfigDict(extra="forbid")

    effect: Literal["allow", "deny"] = Field(
        description="Whether this rule allows or denies the matched request."
    )
    methods: list[str] | None = Field(
        default=None, description="HTTP methods to match (case-insensitive). None matches all."
    )
    path: str | None = Field(
        default=None, description="Regex pattern for the request path. None matches all paths."
    )
    operations: list[str] | None = Field(
        default=None, description="OpenAPI operation IDs to match. None matches all operations."
    )

    @model_validator(mode="after")
    def _reject_condition_less_allow(self) -> PermissionRuleSchema:
        # A condition-less `allow` matches every request under the broker's
        # first-match-wins evaluation — an unrestricted grant. Reject it so a
        # binding can never grant blanket access by accident. A condition-less
        # `deny` stays valid as a legitimate catch-all default-deny.
        if self.effect == "allow" and not (self.methods or self.path or self.operations):
            msg = "An 'allow' rule must constrain at least one of methods, path, or operations"
            raise ValueError(msg)
        return self


class ToolkitCreateRequest(BaseModel):
    """Create a new toolkit."""

    name: str = Field(max_length=255)
    description: str | None = None
    active: bool = True
    credential_ids: list[str] | None = None
    permissions: list[PermissionRuleSchema] | None = None


class ToolkitUpdateRequest(BaseModel):
    """Update a toolkit."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    active: bool | None = None


def _validate_ip_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    for v in values:
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"Invalid IP address or CIDR: {v!r}") from e
    return values


class ToolkitKeyCreateRequest(BaseModel):
    """Create a new key for a toolkit."""

    label: str | None = None
    allowed_ips: list[str] | None = None

    @field_validator("allowed_ips")
    @classmethod
    def validate_ips(cls, v: list[str] | None) -> list[str] | None:
        return _validate_ip_list(v)


class ToolkitKeyUpdateRequest(BaseModel):
    """Update a toolkit key."""

    label: str | None = None
    allowed_ips: list[str] | None = None
    revoked: bool | None = None

    @field_validator("allowed_ips")
    @classmethod
    def validate_ips(cls, v: list[str] | None) -> list[str] | None:
        return _validate_ip_list(v)


class ToolkitCredentialBindRequest(BaseModel):
    """Bind a credential to a toolkit."""

    credential_id: str
    permissions: list[PermissionRuleSchema] | None = None


class PermissionRuleReadSchema(BaseModel):
    """Permission rule response (includes system fields)."""

    effect: Literal["allow", "deny"]
    methods: list[str] | None = None
    path: str | None = None
    operations: list[str] | None = None
    is_system: bool = Field(alias="_system", default=False)
    comment: str | None = Field(alias="_comment", default=None)

    model_config = {"populate_by_name": True}


class PermissionsPatchRequest(BaseModel):
    """Patch permission rules — add and/or remove."""

    add: list[PermissionRuleSchema] | None = None
    remove: list[int] | None = None


# --- Response models ---


class ToolkitResponse(BaseModel):
    """Toolkit response."""

    toolkit_id: str
    name: str
    description: str | None = None
    active: bool
    permissions: list[dict[str, object]]
    key_count: int
    credential_count: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ToolkitCreateResponse(BaseModel):
    """Create response: toolkit + api_key shown once."""

    toolkit: ToolkitResponse
    api_key: str


class ToolkitListResponse(BaseModel):
    """Paginated list of toolkits."""

    data: list[ToolkitResponse]
    has_more: bool
    next_cursor: str | None = None


class ToolkitKeyResponse(BaseModel):
    """Toolkit key response."""

    key_id: str
    toolkit_id: str
    label: str | None = None
    allowed_ips: list[str] | None = None
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None = None
    key_preview: str


class ToolkitKeyCreateResponse(BaseModel):
    """Create response: key + plaintext shown once."""

    key: ToolkitKeyResponse
    api_key: str


class ToolkitKeyListResponse(BaseModel):
    """Paginated list of toolkit keys."""

    data: list[ToolkitKeyResponse]
    has_more: bool
    next_cursor: str | None = None


class ToolkitCredentialBindingResponse(BaseModel):
    """Credential binding response."""

    toolkit_id: str
    credential_id: str
    api_vendor: str | None = None
    api_name: str | None = None
    credential_type: str | None = None
    label: str | None = None
    bound_at: datetime
    permissions: list[PermissionRuleReadSchema] = Field(default_factory=list)


class ToolkitCredentialListResponse(BaseModel):
    """Paginated list of credential bindings."""

    data: list[ToolkitCredentialBindingResponse]
    has_more: bool
    next_cursor: str | None = None


class ToolkitAgentResponse(BaseModel):
    """Agent bound to a toolkit."""

    agent_id: str
    agent_name: str
    status: str
    bound_at: datetime


class ToolkitAgentListResponse(BaseModel):
    """Paginated list of agents bound to a toolkit."""

    data: list[ToolkitAgentResponse]
    has_more: bool
    next_cursor: str | None = None


class PermissionRuleListResponse(BaseModel):
    """List of permission rules."""

    data: list[PermissionRuleReadSchema]
