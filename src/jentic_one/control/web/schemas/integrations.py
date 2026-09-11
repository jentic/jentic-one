"""Pydantic schemas for the /integrations, /connect-sessions, /vendors endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Vendor discovery
# ---------------------------------------------------------------------------


class VendorScopeResponse(BaseModel):
    name: str
    classification: Literal["read", "write", "admin"]
    default: bool
    description: str


class VendorFlowResponse(BaseModel):
    kind: str


class VendorAuthCapabilitiesResponse(BaseModel):
    vendor: str
    display_name: str
    flows: list[VendorFlowResponse]
    scopes: list[VendorScopeResponse]


class VendorSummaryResponse(BaseModel):
    key: str
    vendor: str
    display_name: str
    flow_kinds: list[str]


class VendorListResponse(BaseModel):
    data: list[VendorSummaryResponse]


# ---------------------------------------------------------------------------
# POST /integrations:connect
# ---------------------------------------------------------------------------


class PermissionRuleModel(BaseModel):
    """One agent permission rule (allow/deny)."""

    method: str = Field(examples=["GET", "POST"])
    path: str = Field(examples=["/repos/**", "/repos/*/issues"])
    effect: Literal["allow", "deny"] = "allow"


class IntegrationsConnectRequest(BaseModel):
    vendor: str = Field(description="Vendor registry key (e.g. 'github')")
    # Required for USER/SA callers, ignored for AGENT callers.
    agent_id: str | None = Field(default=None)
    requested_scopes: list[str] = Field(default_factory=list)
    preferred_flow: str | None = Field(default=None)
    reason: str | None = Field(default=None, max_length=1024)


class IntegrationsConnectResponse(BaseModel):
    session_id: str
    approval_url: str
    poll_token: str
    resolved_flow: str


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}   (review page data)
# ---------------------------------------------------------------------------


class ReviewScopeResponse(BaseModel):
    name: str
    classification: Literal["read", "write", "admin"]
    default: bool
    requested: bool
    description: str


class ReviewSessionResponse(BaseModel):
    session_id: str
    state: str
    vendor_key: str
    vendor_display_name: str
    resolved_flow: str
    reason: str | None
    requested_by_actor_id: str
    scopes: list[ReviewScopeResponse]


# ---------------------------------------------------------------------------
# POST /connect-sessions/{id}:confirm
# ---------------------------------------------------------------------------


class ConfirmSessionRequest(BaseModel):
    confirmed_scopes: list[str]
    permission_rules: list[PermissionRuleModel] = Field(default_factory=list)


class ConfirmSessionResponse(BaseModel):
    """Discriminated on ``kind`` — clients branch on that, not field presence.

    * ``kind == "device_flow"`` — RFC 8628 device-code result: the user
      types ``user_code`` at ``verification_uri``, and the client polls
      ``/status`` until connected.
    * ``kind == "authorization_code"`` — browser redirect target
      (``authorize_url``); completion lands server-side at
      ``/credentials/oauth/callback`` and the client observes it via
      ``/status``.
    """

    kind: Literal["device_flow", "authorization_code"]
    # Device-flow branch.
    user_code: str | None = None
    verification_uri: str | None = None
    verification_uri_complete: str | None = None
    poll_interval_seconds: int | None = None
    # Auth-code branch.
    authorize_url: str | None = None


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}/status
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    status: Literal["pending", "polling", "connected", "failed", "expired"]
    connected_as: str | None = None
    credential_id: str | None = None
    bound_scopes: list[str] | None = None
    error_code: str | None = None
