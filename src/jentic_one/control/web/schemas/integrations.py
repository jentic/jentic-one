"""Pydantic schemas for the /integrations, /connect-sessions, /vendors endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from jentic_one.control.web.schemas.permission_rules import PermissionRuleSchema

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
    # As-requested permission rules — typically the initiating agent's ask,
    # rendered on the review page as pre-filled rows the human owner can
    # accept / edit / drop before ``:confirm`` persists the final set.
    # Captured on the session row; never bound to
    # ``agent_permission_rules`` until ``:confirm``.
    requested_permission_rules: list[PermissionRuleSchema] = Field(default_factory=list)


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


class ApiReferenceResponse(BaseModel):
    """Where the vendor's OpenAPI lives in the registry.

    ``version`` is nullable because the catalog auto-importer runs
    asynchronously — the SPA's rules-page preview polls this endpoint
    and shows an "operations still importing…" skeleton until it
    becomes non-null and the operations endpoint returns 200.
    """

    vendor: str
    name: str | None
    version: str | None


class ReviewSessionResponse(BaseModel):
    session_id: str
    state: str
    vendor_key: str
    vendor_display_name: str
    resolved_flow: str
    reason: str | None
    requested_by_actor_id: str
    scopes: list[ReviewScopeResponse]
    # Captured verbatim from ``IntegrationsConnectRequest.requested_permission_rules``
    # so the approve page can render them as pre-filled rows.
    requested_permission_rules: list[PermissionRuleSchema] = Field(default_factory=list)
    # Where the vendor's OpenAPI lives once imported — SPA needs this
    # to hit ``/apis/{vendor}/{name}/{version}/operations`` on the
    # rules-page preview.
    api_reference: ApiReferenceResponse


# ---------------------------------------------------------------------------
# POST /connect-sessions/{id}:confirm
# ---------------------------------------------------------------------------


class ConfirmSessionRequest(BaseModel):
    confirmed_scopes: list[str]
    permission_rules: list[PermissionRuleSchema] = Field(default_factory=list)
    # Selected at the rules-page Continue-click when the session was
    # opened without a target agent (user starts a session by clicking a
    # vendor tile, then picks the agent to bind on the way through).
    # Ignored when the session already carries an ``agent_id`` — the
    # service refuses to switch the target once a binding has been made
    # visible to the user.
    agent_id: str | None = Field(default=None)


class DeviceAuthorizationConfirmSessionResponse(BaseModel):
    """RFC 8628 device-code result — user types ``user_code`` at
    ``verification_uri`` and the client polls ``/status`` until connected.

    Discriminator matches ``ConnectChallengeResponse`` (the direct
    credential-connect endpoint) — one wire value for the same concept
    across both entry points.
    """

    kind: Literal["device_authorization"] = "device_authorization"
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    poll_interval_seconds: int | None = None


class AuthCodeConfirmSessionResponse(BaseModel):
    """Authorization-code result — browser redirect target; completion
    lands server-side at ``/credentials/oauth/callback`` and the client
    observes it via ``/status``."""

    kind: Literal["authorization_code"] = "authorization_code"
    authorize_url: str


ConfirmSessionResponse = Annotated[
    DeviceAuthorizationConfirmSessionResponse | AuthCodeConfirmSessionResponse,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}/status
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    status: Literal["pending", "polling", "connected", "failed", "expired"]
    connected_as: str | None = None
    credential_id: str | None = None
    bound_scopes: list[str] | None = None
    error_code: str | None = None
