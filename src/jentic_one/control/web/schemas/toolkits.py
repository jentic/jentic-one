"""Web request/response models for the toolkits API."""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jentic_one.control.web.schemas.permission_rules import BasePermissionRuleSchema
from jentic_one.shared.permissions.matching import MatchMode

# --- Request models ---


class PermissionRuleSchema(BasePermissionRuleSchema):
    """Permission rule for a toolkit-credential binding.

    Rules are evaluated first-match-wins. If no rule matches, the request is
    denied (default-deny). A binding with zero rules therefore blocks all
    operations — users must explicitly add at least one allow rule.
    """

    effect: Literal["allow", "deny"] = Field(
        description="Whether this rule allows or denies the matched request."
    )


class ToolkitCreateRequest(BaseModel):
    """Create a new toolkit."""

    name: str = Field(max_length=255)
    description: str | None = None
    active: bool = True
    credential_ids: list[str] | None = None


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

    model_config = ConfigDict(extra="forbid")

    credential_id: str
    permissions: list[PermissionRuleSchema] | None = None
    allow_all: bool = Field(
        default=False,
        description=(
            "Convenience flag: bind with a single `allow` rule that matches every "
            "request for this binding's vendor. Mutually exclusive with `permissions`."
        ),
    )

    @model_validator(mode="after")
    def _allow_all_conflicts_with_permissions(self) -> ToolkitCredentialBindRequest:
        # ``allow_all=True`` sets a specific rule under the hood; combining it
        # with an authored ``permissions`` list is ambiguous — reject at the
        # boundary so the caller has to pick one intent.
        if self.allow_all and self.permissions:
            msg = "allow_all and permissions are mutually exclusive"
            raise ValueError(msg)
        return self


class PermissionRuleReadSchema(BaseModel):
    """Permission rule response (includes system fields)."""

    effect: Literal["allow", "deny"]
    methods: list[str] | None = None
    path: str | None = None
    match_mode: MatchMode = "regex"
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
    key_count: int
    credential_count: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class BindingWarningSchema(BaseModel):
    """A non-fatal signal about a bind (or create-time inline bind)."""

    code: str = Field(description="Stable machine-readable warning code.")
    message: str = Field(description="Human-readable explanation with a recovery pointer.")
    credential_id: str | None = Field(
        default=None,
        description="Credential the warning applies to; null when the whole binding is meant.",
    )


class ToolkitCreateResponse(BaseModel):
    """Create response: toolkit + api_key shown once."""

    toolkit: ToolkitResponse
    api_key: str
    warnings: list[BindingWarningSchema] = Field(
        default_factory=list,
        description=(
            "Non-fatal signals about the create — e.g. inline-bound credentials "
            "that landed with zero permission rules (broker denies by default)."
        ),
    )


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
    warnings: list[BindingWarningSchema] = Field(
        default_factory=list,
        description=(
            "Non-fatal bind-time signals — e.g. a binding that landed with zero "
            "permission rules (broker denies by default until rules are added)."
        ),
    )


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


class PermissionTestRequest(BaseModel):
    """Request body for :test — dry-run a request shape against pooled rules."""

    model_config = ConfigDict(extra="forbid")

    method: str = Field(
        description="HTTP method of the hypothetical request (case-insensitive).",
    )
    path: str = Field(
        description="Path of the hypothetical request as the broker would see it.",
    )
    operation_id: str | None = Field(
        default=None,
        description="Optional OpenAPI operation id resolved from the request URL.",
    )


class PermissionTestResponse(BaseModel):
    """Dry-run result matching :class:`PermissionTestResult`."""

    allowed: bool = Field(
        description="Whether the broker would allow this request under the pooled rules."
    )
    matched: bool = Field(
        description="Whether any rule matched; when false, the outcome is default-deny."
    )
    effect: str | None = Field(
        default=None,
        description="Effect of the matching rule (`allow`/`deny`); null when no match.",
    )
    rule_index: int | None = Field(
        default=None,
        description="Zero-based index in the vendor-pooled rule list; null when no match.",
    )
    credential_id: str | None = Field(
        default=None,
        description=(
            "Which binding contributed the matching rule — vendor pooling means "
            "this may not equal the credential in the request URL."
        ),
    )
    is_system: bool | None = Field(
        default=None,
        description="True when the matching rule was written by the system; null when no match.",
    )
