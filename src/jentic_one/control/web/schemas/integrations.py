"""Pydantic schemas for the /integrations, /connect-sessions, /vendors endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    SecretStr,
    Tag,
    model_validator,
)

from jentic_one.control.web.schemas.permission_rules import PermissionRuleSchema
from jentic_one.shared.web.sensitive import SENSITIVE

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
    # For admin-registered rows, the catalog API the shared app signs in to
    # (``<domain>/<sub>``) — lets a flow already on one API offer only its
    # apps. ``None`` for platform-shipped config rows.
    catalog_api_id: str | None = None


class VendorListResponse(BaseModel):
    data: list[VendorSummaryResponse]


# ---------------------------------------------------------------------------
# POST /integrations:connect
# ---------------------------------------------------------------------------


class ApiTargetRequest(BaseModel):
    """A registry API identity to connect a credential for."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(min_length=1, max_length=100, description="API vendor (e.g. 'stripe-com')")
    name: str = Field(min_length=1, max_length=100, description="API name")
    version: str = Field(min_length=1, max_length=100, description="API version")


class IntegrationsConnectRequest(BaseModel):
    # Reject unknown fields: a misspelt ``oauth_app_registration_id`` must not
    # silently fall through to unpinned source resolution.
    model_config = ConfigDict(extra="forbid")

    vendor: str | None = Field(
        default=None,
        description="Vendor registry key (e.g. 'github'). Exactly one of vendor or api.",
    )
    # A registry API identity instead of a vendor key. The API's live revision
    # decides the credential (its declared security scheme and hosts).
    # Refused with 404 ``manual_flows_disabled`` unless
    # ``control.connect.manual_flows_enabled`` is on.
    api: ApiTargetRequest | None = Field(
        default=None,
        description="Registry API to connect a credential for. Exactly one of vendor or api.",
    )
    # Proposed scheme for an ``api`` target: a declared scheme's name, or its
    # kind (``api_key``, ``bearer``, ``basic``, ``oauth2``). Only picks among
    # what the API's spec declares.
    auth_type: str | None = Field(
        default=None,
        max_length=255,
        description="Declared scheme name or kind to use for an api target",
    )
    # Optional user-facing label for the resulting credential. Defaults to
    # the vendor's display name when omitted. Lets a user distinguish
    # multiple credentials minted from the same vendor / shared registration
    # (e.g. ``"Google (personal)"`` vs ``"Google (work)"``).
    name: str | None = Field(default=None, max_length=255)
    # Optional pin to a specific admin-registered OAuth app. When set, the
    # connect session mints tokens through this registration; when omitted,
    # the config-shipped vendor entry is used if it offers the requested
    # flow, else the vendor's single matching active registration (more
    # than one → 400 ``ambiguous_vendor``).
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

    @model_validator(mode="after")
    def _one_target(self) -> IntegrationsConnectRequest:
        if (self.vendor is None) == (self.api is None):
            raise ValueError("pass exactly one of vendor or api")
        if self.auth_type is not None and self.api is None:
            raise ValueError("auth_type applies to an api target only")
        return self


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
#: (``created``/``awaiting_app``/``polling``) or ``connected`` sessions.
#: ``awaiting_app`` is an OAuth API target waiting for an app to connect with.
ConnectSessionState = Literal["created", "awaiting_app", "polling", "connected"]


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
    # The session's pending (later connected) credential. Lets a list reader
    # tell a session's own credential apart from an unrelated unfinished one.
    credential_id: str


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


class ReviewProvenanceResponse(BaseModel):
    """Where an API target's live revision came from (registry data)."""

    origin: str | None = None
    catalog_api_id: str | None = None
    submitted_by: str | None = None
    source_url: str | None = None
    revision_id: str


class ReviewAgentResponse(BaseModel):
    """The agent the session binds and its owner."""

    agent_id: str
    name: str | None = None
    owner_id: str | None = None
    status: str


class ReviewSchemeResponse(BaseModel):
    """The declared scheme an API target collects (snapshot from its spec)."""

    type: Literal["api_key", "bearer", "basic", "oauth2"]
    location: str | None = None
    field_name: str | None = None


class ExistingCredentialResponse(BaseModel):
    """A credential the viewer already holds for the target.

    ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials
    only; a static credential's upstream permissions are not visible to the
    platform. ``can_reauthorize`` is false whenever another agent is bound
    (``other_bound_agent_ids``) — widening it would widen theirs too.
    """

    credential_id: str
    name: str
    type: str
    granted_scopes: list[str] | None = None
    missing_scopes: list[str] | None = None
    other_bound_agent_ids: list[str] = Field(default_factory=list)
    can_bind: bool
    can_reauthorize: bool


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
    target_kind: Literal["vendor", "api"] = "vendor"
    requested_scopes: list[str] = Field(default_factory=list)
    provenance: ReviewProvenanceResponse | None = None
    agent: ReviewAgentResponse | None = None
    scheme: ReviewSchemeResponse | None = None
    pinned_hosts: list[str] | None = None
    # Echo on ``:confirm`` with ``expected_agent_id``; a mismatch is 409
    # ``review_stale``.
    digest: str
    can_confirm: bool
    existing_credentials: list[ExistingCredentialResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# POST /connect-sessions/{id}:confirm
# ---------------------------------------------------------------------------


class OAuthConfirmSessionRequest(BaseModel):
    """Confirm an OAuth session: the scopes to request and the agent's rules.

    The default variant — a body without ``kind`` is this one.
    """

    kind: Literal["oauth"] = "oauth"
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
    # The review the approver saw. Checked when given; required for an
    # ``api`` target.
    expected_agent_id: str | None = Field(default=None)
    digest: str | None = Field(default=None, max_length=64)


class _ReviewedConfirm(BaseModel):
    """Fields every non-OAuth confirm carries: rules and the review it answers."""

    # Secrets never echo back in a validation error.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    permission_rules: list[PermissionRuleSchema] = Field(max_length=100)
    agent_id: str | None = Field(default=None)
    expected_agent_id: str | None = Field(default=None)
    digest: str = Field(min_length=1, max_length=64)


class ApiKeyConfirmSessionRequest(_ReviewedConfirm):
    """Confirm a ``manual_api_key`` session with the API key to store."""

    kind: Literal["api_key"]
    key: SecretStr = Field(min_length=1, max_length=8192, json_schema_extra=SENSITIVE)


class BearerConfirmSessionRequest(_ReviewedConfirm):
    """Confirm a ``manual_bearer`` session with the bearer token to store."""

    kind: Literal["bearer"]
    token: SecretStr = Field(min_length=1, max_length=8192, json_schema_extra=SENSITIVE)


class BasicConfirmSessionRequest(_ReviewedConfirm):
    """Confirm a ``manual_basic`` session with the username and password to store."""

    kind: Literal["basic"]
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=8192, json_schema_extra=SENSITIVE)


class OwnOAuthClientConfirmSessionRequest(_ReviewedConfirm):
    """Resolve an ``awaiting_app`` session with the approver's own OAuth client.

    The client is stored on the session's credential (the same shape as a
    ``direct_oauth2`` credential) and the session continues as an
    authorization-code connect. Both endpoints are required: the API's
    declared OAuth endpoints are never used for the approver's client, since
    the spec may be agent-submitted and the token endpoint receives the
    client secret.
    """

    kind: Literal["own_oauth_client"]
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: SecretStr = Field(min_length=1, max_length=8192, json_schema_extra=SENSITIVE)
    authorize_url: str = Field(min_length=1, max_length=2048)
    token_url: str = Field(min_length=1, max_length=2048)
    confirmed_scopes: list[str] = Field(default_factory=list, max_length=100)


class ExistingCredentialConfirmSessionRequest(_ReviewedConfirm):
    """Bind a credential the approver already holds (OAuth only if its grant covers the ask)."""

    kind: Literal["existing_credential"]
    credential_id: str = Field(min_length=1, max_length=30)


class ReauthorizeConfirmSessionRequest(_ReviewedConfirm):
    """Bind the approver's OAuth credential and re-consent it with the requested scopes.

    Refused when another agent is bound to the credential.
    """

    kind: Literal["reauthorize"]
    credential_id: str = Field(min_length=1, max_length=30)


def _confirm_kind(value: Any) -> str:
    """Discriminate the confirm body; a body without ``kind`` is the OAuth variant."""
    kind = value.get("kind") if isinstance(value, dict) else getattr(value, "kind", None)
    return kind if isinstance(kind, str) and kind else "oauth"


ConfirmSessionRequest = Annotated[
    Annotated[OAuthConfirmSessionRequest, Tag("oauth")]
    | Annotated[ApiKeyConfirmSessionRequest, Tag("api_key")]
    | Annotated[BearerConfirmSessionRequest, Tag("bearer")]
    | Annotated[BasicConfirmSessionRequest, Tag("basic")]
    | Annotated[OwnOAuthClientConfirmSessionRequest, Tag("own_oauth_client")]
    | Annotated[ExistingCredentialConfirmSessionRequest, Tag("existing_credential")]
    | Annotated[ReauthorizeConfirmSessionRequest, Tag("reauthorize")],
    Discriminator(_confirm_kind),
]


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


class ConnectedConfirmSessionResponse(BaseModel):
    """The confirm finished the session; ``credential_id`` is bound to the agent."""

    kind: Literal["connected"] = "connected"
    credential_id: str


class ReauthorizeConfirmSessionResponse(BaseModel):
    """The agent is bound to ``credential_id``; open ``authorize_url`` to grant more scopes."""

    kind: Literal["reauthorize"] = "reauthorize"
    credential_id: str
    authorize_url: str


ConfirmSessionResponse = Annotated[
    DeviceAuthorizationConfirmSessionResponse
    | AuthCodeConfirmSessionResponse
    | ConnectedConfirmSessionResponse
    | ReauthorizeConfirmSessionResponse,
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
