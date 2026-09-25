"""Pydantic schemas for the /integrations, /connect-sessions, /vendors endpoints."""

from __future__ import annotations

from datetime import datetime
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
    # Stable per-row UI key. For admin-registered rows this is the
    # ``oar_...`` registration id; for platform-shipped config entries it
    # is the vendor slug. Lets the picker render one card per registration
    # even when multiple registrations share an ``api_vendor``.
    entry_id: str
    # Present when the row came from an ``oauth_app_registrations`` row —
    # threaded onto ``POST /integrations:connect`` as the pin.
    registration_id: str | None = None
    # Vendor slug shared by every registration for the same vendor.
    key: str
    # Config-side fully-qualified vendor id (``<host>/<api-id>``) when
    # available; falls back to ``key`` for DB-only vendors.
    vendor: str
    # Vendor's family display name (e.g. "Gmail"). Two admin registrations
    # for the same vendor share this.
    display_name: str
    # Per-row human label. For DB rows this is the admin-picked
    # registration name; for config rows this equals ``display_name``.
    name: str
    # Where this row came from — ``db`` = admin registration, ``config`` =
    # platform-shipped vendor entry.
    source: Literal["db", "config"]
    flow_kinds: list[str]


class VendorListResponse(BaseModel):
    data: list[VendorSummaryResponse]


# ---------------------------------------------------------------------------
# POST /integrations:connect
# ---------------------------------------------------------------------------


class IntegrationsConnectRequest(BaseModel):
    vendor: str = Field(description="Vendor registry key (e.g. 'github')")
    # Optional user-facing label for the resulting credential. Defaults to
    # the vendor's display name when omitted. Lets a user distinguish
    # multiple credentials minted from the same vendor / shared registration
    # (e.g. ``"Google (personal)"`` vs ``"Google (work)"``).
    name: str | None = Field(default=None, max_length=255)
    # Optional pin to a specific admin-registered OAuth app. When set, the
    # connect session mints tokens through this registration; when omitted,
    # the service picks the most-recently-updated active DB registration
    # (or falls back to the config-shipped vendor entry). Required when the
    # user picked one of multiple registrations for the same vendor from
    # the picker.
    oauth_app_registration_id: str | None = Field(default=None, max_length=30)
    # Required for USER/SA callers, ignored for AGENT callers.
    agent_id: str | None = Field(default=None)
    # Cap mirrors the sister ``/credentials`` endpoints — a scope list
    # deep enough to spam a scope-classification pass in the worker is a
    # cheap DoS surface if left unbounded. 100 is well above any real
    # vendor's scope catalog.
    requested_scopes: list[str] = Field(default_factory=list, max_length=100)
    preferred_flow: str | None = Field(default=None)
    reason: str | None = Field(default=None, max_length=1024)
    # As-requested permission rules — typically the initiating agent's ask,
    # rendered on the review page as pre-filled rows the human owner can
    # accept / edit / drop before ``:confirm`` persists the final set.
    # Captured on the session row; never bound to
    # ``agent_permission_rules`` until ``:confirm``. Bound the list so the
    # session row (and the eventual binding write) cannot be inflated by
    # an unauthenticated ``:connect`` caller.
    requested_permission_rules: list[PermissionRuleSchema] = Field(
        default_factory=list, max_length=100
    )


class IntegrationsConnectResponse(BaseModel):
    session_id: str
    approval_url: str
    poll_token: str
    resolved_flow: str


# ---------------------------------------------------------------------------
# GET /connect-sessions   (console list)
# ---------------------------------------------------------------------------


#: The states a listed row can hold. A session never persists in a terminal
#: failure state: ``_mark_terminal`` deletes its pending credential, and the
#: FK cascade takes the session row with it — so the list only ever sees live
#: (``created``/``polling``) or ``connected`` sessions.
ConnectSessionState = Literal["created", "polling", "connected"]


class ConnectSessionSummaryResponse(BaseModel):
    """Slim list row for the console — deliberately excludes ``poll_token``."""

    session_id: str
    state: ConnectSessionState
    vendor_key: str
    vendor_display_name: str
    agent_id: str | None = None
    requested_by_actor_id: str
    reason: str | None = None
    connected_as: str | None = None
    # Reserved: a failed session is deleted on its terminal transition, so a
    # listed row carries no error_code today.
    error_code: str | None = None
    created_at: datetime


class ConnectSessionListResponse(BaseModel):
    """Cursor-paginated envelope of connect-session summaries."""

    data: list[ConnectSessionSummaryResponse]
    has_more: bool
    next_cursor: str | None = None


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
    # Caps mirror ``IntegrationsConnectRequest`` — the eventual write
    # touches the same broker classification and binding paths, so leaving
    # them unbounded here would defeat the ``:connect`` cap. 100 is far
    # above any real vendor's scope catalog or a sane per-binding rule
    # count.
    confirmed_scopes: list[str] = Field(max_length=100)
    permission_rules: list[PermissionRuleSchema] = Field(default_factory=list, max_length=100)
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
