"""Web request/response models for the access-requests API."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from jentic_one.control.web.schemas.permission_rules import BasePermissionRuleSchema

# --- Request models ---


class PermissionRuleSchema(BasePermissionRuleSchema):
    """Permission rule for an access request item."""

    effect: Literal["allow", "deny", "require-approval"]


class CredentialSpecSchema(BaseModel):
    """Credential specification reference."""

    api_reference: dict[str, str]
    security_scheme_type: str | None = None


class AccessRequestItemRequest(BaseModel):
    """A single line-item in a file request.

    **Permission rules:** Rules control which upstream API operations the broker
    allows through a credential binding. They are enforced per (toolkit_id,
    credential_id) pair, so they can only be attached to credential:bind items —
    not toolkit:bind or scope:grant. You do not need toolkits:write scope to set
    rules; include them directly on the credential:bind item when filing the
    access request, and the approver's decision persists them on the binding.
    """

    resource_type: Literal["credential", "toolkit", "scope"]
    action: Literal["bind", "grant", "create", "provision"]
    resource_id: str | None = None
    resource_reference: dict[str, Any] | None = None
    to_type: str | None = None
    to_id: str | None = None
    rules: list[PermissionRuleSchema] | None = Field(
        default=None,
        description=(
            "Permission rules for the binding (credential:bind only). "
            "Rules are evaluated first-match-wins by the broker; if no rule matches, "
            'the request is denied. Example: [{"effect": "allow", "path": ".*"}].'
        ),
    )

    @model_validator(mode="after")
    def _check_resource_target(self) -> AccessRequestItemRequest:
        has_id = self.resource_id is not None
        has_ref = self.resource_reference is not None
        if has_id and has_ref:
            msg = "Provide exactly one of resource_id or resource_reference, not both"
            raise ValueError(msg)
        # Only the (resource_type, action) pairs the system understands are
        # meaningful. Two families:
        #   * enforced effects — the applicator dispatches on them at approval
        #     (credential:bind, toolkit:bind, scope:grant).
        #   * fulfilment intents — placeholders in a provisioning plan that a
        #     human fulfils via the existing create endpoints; the applicator
        #     never executes them (toolkit:create, credential:provision).
        # Reject anything else (e.g. ("scope", "bind")) so a filer gets immediate
        # feedback instead of a silent no-op.
        valid = {
            ("credential", "bind"),
            ("toolkit", "bind"),
            ("scope", "grant"),
            ("toolkit", "create"),
            ("credential", "provision"),
        }
        if (self.resource_type, self.action) not in valid:
            msg = (
                f"Unsupported resource_type/action combination: {self.resource_type}/{self.action}"
            )
            raise ValueError(msg)
        return self


class AccessRequestFileRequest(BaseModel):
    """Request body for filing an access request."""

    reason: str | None = None
    items: list[AccessRequestItemRequest] = Field(min_length=1)


class DecideItemSchema(BaseModel):
    """A single item decision."""

    item_id: str
    decision: Literal["approved", "denied"]
    decision_reason: str | None = None


class DecideRequest(BaseModel):
    """Request body for the :decide verb."""

    items: list[DecideItemSchema] = Field(min_length=1)


class AmendItemSchema(BaseModel):
    """A single item amendment."""

    item_id: str
    rules: list[PermissionRuleSchema] | None = None
    resource_id: str | None = None
    to_id: str | None = None


class AmendRequest(BaseModel):
    """Request body for the :amend verb."""

    items: list[AmendItemSchema] = Field(min_length=1)


# --- Response models ---


class AccessRequestItemResponse(BaseModel):
    """Response model for a single access-request line item."""

    id: str
    resource_type: str
    action: str
    resource_id: str | None = None
    resource_reference: dict[str, Any] | None = None
    to_type: str | None = None
    to_id: str | None = None
    toolkit_name: str | None = None
    credential_name: str | None = None
    rules: list[dict[str, Any]] | None = None
    status: str
    applied_effects: dict[str, Any] | None = None
    decided_by: str | None = None
    decided_at: dt.datetime | None = None
    decision_reason: str | None = None
    already_satisfied: bool | None = Field(
        default=None,
        description=(
            "Whether this item's outcome is already in effect (the binding or "
            "grant it asks for already exists), letting a reviewer approve "
            "manually-fulfilled work instead of re-doing it in the wizard. "
            "Populated on single-request GETs for pending credential:bind, "
            "toolkit:bind, and scope:grant items; null when not computed "
            "(list endpoints, decided items, fulfilment-only intents, or an "
            "item whose target cannot be determined). Toolkit references are "
            "resolved under the caller's visibility, mirroring decide-time "
            "resolution."
        ),
    )


class EvaluationCheckResponse(BaseModel):
    """A single evaluation check result."""

    check: str
    passed: bool
    blocker: str | None = None


class EvaluationResponse(BaseModel):
    """Computed evaluation of whether the caller can fulfill a request."""

    can_fulfill: bool
    checks: list[EvaluationCheckResponse]


class AccessRequestOwnerResponse(BaseModel):
    """Display info for the filer's human owner (labelling only, not authorization).

    Server-resolved from ``filer_owner_id`` (falling back to ``created_by``
    when the former is null, mirroring what consumers render) so they don't
    need ``users:read`` (or a roster fetch) just to label a row. Absent when
    the id doesn't resolve to a user (service-account filers, purged rows) or
    on mutation responses, which skip the enrichment.
    """

    id: str = Field(
        description="The resolved owner's user id (filer_owner_id, or created_by when null)."
    )
    email: str = Field(description="The owner's email address.")
    display_name: str | None = Field(
        default=None, description="The owner's full name, when set on the profile."
    )


class AccessRequestResponse(BaseModel):
    """Response model for an access request envelope."""

    id: str
    actor_id: str
    reason: str | None = None
    requested_by: str
    status: str
    approve_url: str
    filed_at: dt.datetime
    expires_at: dt.datetime
    created_by: str
    filer_owner_id: str | None = None
    filer_owner: AccessRequestOwnerResponse | None = None
    items: list[AccessRequestItemResponse]
    evaluation: EvaluationResponse | None = None


class AccessRequestListResponse(BaseModel):
    """Paginated list of access requests."""

    data: list[AccessRequestResponse]
    has_more: bool
    next_cursor: str | None = None
