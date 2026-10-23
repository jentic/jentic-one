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
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.sensitive import SENSITIVE

# ---------------------------------------------------------------------------
# Vendor discovery
# ---------------------------------------------------------------------------


class VendorScopeResponse(BaseModel):
    """One OAuth scope in a vendor's catalogue."""

    name: str = Field(description="The vendor's scope string.", examples=["repo"])
    classification: Literal["read", "write", "admin"] = Field(
        description="How much access the scope grants.", examples=["write"]
    )
    default: bool = Field(
        description="Whether the scope is requested unless the user deselects it.",
        examples=[True],
    )
    description: str = Field(
        description="What the scope lets the credential do.",
        examples=["Full control of private repositories."],
    )


class VendorFlowResponse(BaseModel):
    """One OAuth flow the vendor supports."""

    kind: str = Field(
        description="The flow: `authorization_code` or `device_authorization`.",
        examples=["authorization_code"],
    )


class VendorAuthCapabilitiesResponse(BaseModel):
    """How to connect a vendor: its OAuth flows and scope catalogue."""

    vendor: str = Field(description="Vendor registry key.", examples=[ex.VENDOR_KEY])
    display_name: str = Field(description="Display name of the vendor.", examples=["GitHub"])
    flows: list[VendorFlowResponse] = Field(description="OAuth flows the vendor supports.")
    scopes: list[VendorScopeResponse] = Field(description="Every scope the vendor offers.")


class VendorSummaryResponse(BaseModel):
    """A vendor, or one admin registration for it, that the caller can connect to."""

    # Stable per-row UI key. For admin-registered rows this is the
    # ``oar_...`` registration id; for platform-shipped config entries it
    # is the vendor slug. Lets the picker render one card per registration
    # even when multiple registrations share an ``api_vendor``.
    entry_id: str = Field(
        examples=[ex.OAUTH_APP_REGISTRATION_ID],
        description=(
            "Stable per-row key: the registration ID for admin registrations, else the vendor key."
        ),
    )
    # Present when the row came from an ``oauth_app_registrations`` row —
    # threaded onto ``POST /integrations:connect`` as the pin.
    registration_id: str | None = Field(
        default=None,
        examples=[ex.OAUTH_APP_REGISTRATION_ID],
        description=(
            "ID of the admin OAuth app registration behind "
            "this row; null for platform-shipped vendors."
        ),
    )
    # Vendor slug shared by every registration for the same vendor.
    key: str = Field(
        description="Vendor registry key shared by every registration for the vendor.",
        examples=[ex.VENDOR_KEY],
    )
    # Config-side fully-qualified vendor id (``<host>/<api-id>``) when
    # available; falls back to ``key`` for DB-only vendors.
    vendor: str = Field(
        examples=["github.com/api.github.com"],
        description=(
            "Fully-qualified vendor ID (`<host>/<api-id>`) when known; otherwise the vendor key."
        ),
    )
    # Vendor's family display name (e.g. "Gmail"). Two admin registrations
    # for the same vendor share this.
    display_name: str = Field(
        description="Vendor's display name (e.g. 'Gmail').", examples=["GitHub"]
    )
    # Per-row human label. For DB rows this is the admin-picked
    # registration name; for config rows this equals ``display_name``.
    name: str = Field(
        examples=["MyOrg GitHub"],
        description=(
            "Label for this row: the registration name for "
            "admin registrations, else the display name."
        ),
    )
    # Where this row came from — ``db`` = admin registration, ``config`` =
    # platform-shipped vendor entry.
    source: Literal["db", "config"] = Field(
        examples=["db"],
        description=(
            "Where the row came from: `db` for an admin registration, "
            "`config` for a platform-shipped vendor."
        ),
    )
    flow_kinds: list[str] = Field(
        examples=[["authorization_code"]],
        description=(
            "OAuth flow kinds this row supports (`authorization_code`, `device_authorization`)."
        ),
    )
    # For admin-registered rows, the catalog API the shared app signs in to
    # (``<domain>/<sub>``) — lets a flow already on one API offer only its
    # apps. ``None`` for platform-shipped config rows.
    catalog_api_id: str | None = Field(
        default=None,
        examples=["github.com/api.github.com"],
        description=(
            "Catalog API (`<domain>/<sub>`) the registration "
            "signs in to; null for platform-shipped vendors."
        ),
    )


class VendorListResponse(BaseModel):
    """The vendors the caller can connect a credential for."""

    data: list[VendorSummaryResponse] = Field(description="The connectable vendors.")


# ---------------------------------------------------------------------------
# POST /integrations:connect
# ---------------------------------------------------------------------------


class ApiTargetRequest(BaseModel):
    """A registry API identity to connect a credential for."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(
        min_length=1,
        max_length=100,
        description="API vendor (e.g. 'stripe-com')",
        examples=[ex.API_VENDOR],
    )
    name: str = Field(min_length=1, max_length=100, description="API name", examples=[ex.API_NAME])
    version: str = Field(
        min_length=1, max_length=100, description="API version", examples=[ex.API_VERSION]
    )


class IntegrationsConnectRequest(BaseModel):
    """Start connecting a credential for a vendor or a registry API."""

    # Reject unknown fields: a misspelt ``oauth_app_registration_id`` must not
    # silently fall through to unpinned source resolution.
    model_config = ConfigDict(extra="forbid")

    vendor: str | None = Field(
        default=None,
        examples=[ex.VENDOR_KEY],
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
        examples=["oauth2"],
        description="Declared scheme name or kind to use for an api target",
    )
    # Optional user-facing label for the resulting credential. Defaults to
    # the vendor's display name when omitted. Lets a user distinguish
    # multiple credentials minted from the same vendor / shared registration
    # (e.g. ``"Google (personal)"`` vs ``"Google (work)"``).
    name: str | None = Field(
        default=None,
        max_length=255,
        examples=["GitHub (work)"],
        description="Label for the resulting credential; defaults to the vendor's display name.",
    )
    # Optional pin to a specific admin-registered OAuth app. When set, the
    # connect session mints tokens through this registration; when omitted,
    # the config-shipped vendor entry is used if it offers the requested
    # flow, else the vendor's single matching active registration (more
    # than one → 400 ``ambiguous_vendor``).
    oauth_app_registration_id: str | None = Field(
        default=None,
        max_length=30,
        examples=[ex.OAUTH_APP_REGISTRATION_ID],
        description=(
            "ID of the admin OAuth app registration to connect through; required when "
            "several active registrations offer the flow."
        ),
    )
    # Optional for user callers (an active agent they own and may approve
    # for; any agent for org:admin); refused for agent callers.
    agent_id: str | None = Field(
        default=None,
        examples=[ex.AGENT_ID],
        description=(
            "ID of the agent to bind the credential to on confirm; it must be an active agent "
            "the caller owns and may approve for (`agents:write`), or any agent for "
            "`org:admin`. Agent callers must omit it, and when omitted by a user the credential "
            "connects unbound."
        ),
    )
    # Cap mirrors the sister ``/credentials`` endpoints — a scope list
    # deep enough to spam a scope-classification pass in the worker is a
    # cheap DoS surface if left unbounded. 100 is well above any real
    # vendor's scope catalog.
    requested_scopes: list[str] = Field(
        default_factory=list,
        max_length=100,
        examples=[["repo", "read:user"]],
        description="Upstream scopes to request (at most 100).",
    )
    preferred_flow: str | None = Field(
        default=None,
        examples=["authorization_code"],
        description=(
            "Flow kind to use if the source offers it; defaults to the source's first flow."
        ),
    )
    reason: str | None = Field(
        default=None,
        max_length=1024,
        examples=["Needs read access to triage issues in the platform repo."],
        description="Free-text reason for the request, shown to the approver.",
    )
    # As-requested permission rules — typically the initiating agent's ask,
    # rendered on the review page as pre-filled rows the human owner can
    # accept / edit / drop before ``:confirm`` persists the final set.
    # Captured on the session row; never bound to
    # ``agent_permission_rules`` until ``:confirm``. Bound the list so the
    # session row (and the eventual binding write) cannot be inflated by
    # an unauthenticated ``:connect`` caller.
    requested_permission_rules: list[PermissionRuleSchema] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "Permission rules proposed for the agent, shown pre-filled on the review page; "
            "nothing is bound until confirm."
        ),
    )

    @model_validator(mode="after")
    def _one_target(self) -> IntegrationsConnectRequest:
        if (self.vendor is None) == (self.api is None):
            raise ValueError("pass exactly one of vendor or api")
        if self.auth_type is not None and self.api is None:
            raise ValueError("auth_type applies to an api target only")
        return self


class IntegrationsConnectResponse(BaseModel):
    """A started connect session: where a human approves it and how to poll it."""

    session_id: str = Field(
        description="Unique ID of the connect session.", examples=[ex.CONNECT_SESSION_ID]
    )
    approval_url: str = Field(
        description="URL a human opens to review and approve the session.",
        examples=[f"{ex.BASE_URL}/connect?approve={ex.CONNECT_SESSION_ID}"],
    )
    poll_token: str = Field(
        json_schema_extra=SENSITIVE,
        description="Opaque capability for the session's status, review and cancel routes.",
        examples=[ex.OPAQUE_TOKEN],
    )
    resolved_flow: str = Field(
        description="The flow the session runs, e.g. `authorization_code` or `manual_api_key`.",
        examples=["authorization_code"],
    )


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

    session_id: str = Field(
        description="Unique ID of the connect session.", examples=[ex.CONNECT_SESSION_ID]
    )
    state: ConnectSessionState = Field(
        description="Current lifecycle state of the session.", examples=["connected"]
    )
    vendor_key: str = Field(
        description="Vendor registry key the session connects.", examples=[ex.VENDOR_KEY]
    )
    vendor_display_name: str = Field(description="Display name of the vendor.", examples=["GitHub"])
    agent_id: str | None = Field(
        default=None,
        examples=[ex.AGENT_ID],
        description="ID of the agent the credential is bound to on confirm; null when unbound.",
    )
    requested_by_actor_id: str = Field(
        description="ID of the actor that started the session.", examples=[ex.AGENT_ID]
    )
    reason: str | None = Field(
        default=None,
        examples=["Needs read access to triage issues in the platform repo."],
        description="Free-text reason given when the session was started.",
    )
    connected_as: str | None = Field(
        default=None,
        examples=["octocat"],
        description="Upstream account the credential connected as; null until known.",
    )
    # Reserved: a failed session is deleted on its terminal transition, so a
    # listed row carries no error_code today.
    error_code: str | None = Field(
        default=None,
        examples=["access_denied"],
        description="Reserved for a failure code; always null on listed sessions.",
    )
    created_at: datetime = Field(
        description="When the session was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    # The session's pending (later connected) credential. Lets a list reader
    # tell a session's own credential apart from an unrelated unfinished one.
    credential_id: str = Field(
        description="ID of the session's credential (pending until the session connects).",
        examples=[ex.CREDENTIAL_ID],
    )


class ConnectSessionListResponse(BaseModel):
    """Cursor-paginated envelope of connect-session summaries."""

    data: list[ConnectSessionSummaryResponse] = Field(description="The page of connect sessions.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        examples=[ex.CURSOR],
        description="Cursor for the next page; null when there are no more results.",
    )


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}   (review page data)
# ---------------------------------------------------------------------------


class ReviewScopeResponse(BaseModel):
    """One scope in the review's catalogue, flagged default and requested."""

    name: str = Field(description="The vendor's scope string.", examples=["repo"])
    classification: Literal["read", "write", "admin"] = Field(
        description="How much access the scope grants.", examples=["write"]
    )
    default: bool = Field(
        description="Whether the vendor requests the scope by default.", examples=[True]
    )
    requested: bool = Field(
        description="Whether the session's initiator asked for the scope.", examples=[True]
    )
    description: str = Field(
        description="What the scope lets the credential do.",
        examples=["Full control of private repositories."],
    )


class ConnectSessionApiReferenceResponse(BaseModel):
    """Where the vendor's OpenAPI lives in the registry.

    ``version`` is nullable because the catalog auto-importer runs
    asynchronously — the SPA's rules-page preview polls this endpoint
    and shows an "operations still importing…" skeleton until it
    becomes non-null and the operations endpoint returns 200.
    """

    vendor: str = Field(description="Registry vendor of the API.", examples=[ex.API_VENDOR])
    name: str | None = Field(
        description="Registry name of the API; null if not yet known.", examples=[ex.API_NAME]
    )
    version: str | None = Field(
        description="Registry version of the API; null until the catalog import completes.",
        examples=[ex.API_VERSION],
    )


class ReviewProvenanceResponse(BaseModel):
    """Where an API target's live revision came from (registry data)."""

    origin: str | None = Field(
        default=None,
        description="`catalog` for a public-catalog import; anything else is a submitted spec.",
        examples=["catalog"],
    )
    catalog_api_id: str | None = Field(
        default=None,
        description="Catalog identity of the API, when it came from the catalog.",
        examples=[ex.CATALOG_API_ID],
    )
    submitted_by: str | None = Field(
        default=None,
        description="ID of the actor who submitted the spec, when known.",
        examples=[ex.USER_ID],
    )
    source_url: str | None = Field(
        default=None,
        description="Where the spec was fetched from, when known.",
        examples=["https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json"],
    )
    revision_id: str = Field(
        description="ID of the API's live registry revision.", examples=[ex.REVISION_ID]
    )


class ReviewAgentResponse(BaseModel):
    """The agent the session binds and its owner."""

    agent_id: str = Field(description="ID of the agent.", examples=[ex.AGENT_ID])
    name: str | None = Field(
        default=None, description="Name of the agent.", examples=["invoice-reconciler"]
    )
    owner_id: str | None = Field(
        default=None, description="ID of the agent's owner.", examples=[ex.USER_ID]
    )
    status: str = Field(description="The agent's lifecycle status.", examples=["active"])


class ReviewSchemeResponse(BaseModel):
    """The declared scheme an API target collects (snapshot from its spec)."""

    type: Literal["api_key", "bearer", "basic", "oauth2"] = Field(
        description="The kind of secret the scheme collects.", examples=["api_key"]
    )
    location: str | None = Field(
        default=None,
        description="Where an API key goes: `header`, `query` or `cookie`.",
        examples=["header"],
    )
    field_name: str | None = Field(
        default=None,
        description="Header, query or cookie name an API key is sent in.",
        examples=["X-Api-Key"],
    )
    # An OAuth scheme's authorize and token URLs, set only when the API's spec is
    # from the public catalog — offered to an approver bringing their own app.
    authorization_url: str | None = Field(
        default=None,
        description="An OAuth scheme's authorize URL; set only for a public-catalog spec.",
        examples=["https://github.com/login/oauth/authorize"],
    )
    token_url: str | None = Field(
        default=None,
        description="An OAuth scheme's token URL; set only for a public-catalog spec.",
        examples=["https://github.com/login/oauth/access_token"],
    )


class ExistingCredentialResponse(BaseModel):
    """A credential the viewer already holds for the target.

    ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials
    only; a static credential's upstream permissions are not visible to the
    platform. ``can_reauthorize`` is false whenever another agent is bound
    (``other_bound_agent_ids``) — widening it would widen theirs too.
    """

    credential_id: str = Field(description="ID of the credential.", examples=[ex.CREDENTIAL_ID])
    name: str = Field(description="Name of the credential.", examples=["GitHub (octocat)"])
    type: str = Field(description="The credential's type.", examples=["oauth2"])
    granted_scopes: list[str] | None = Field(
        default=None,
        description="Scopes the credential holds; null for a non-OAuth credential.",
        examples=[["repo", "read:org"]],
    )
    missing_scopes: list[str] | None = Field(
        default=None,
        description="Requested scopes the credential lacks; null for a non-OAuth credential.",
        examples=[["admin:org"]],
    )
    other_bound_agent_ids: list[str] = Field(
        default_factory=list,
        description="Other agents bound to the credential.",
        examples=[[ex.AGENT_ID]],
    )
    can_bind: bool = Field(
        description="Whether the viewer may bind the credential to the agent.", examples=[True]
    )
    can_reauthorize: bool = Field(
        description="Whether the viewer may widen the credential's scopes.", examples=[False]
    )


class ReviewSessionResponse(BaseModel):
    """What the review page shows the approver of a connect session."""

    session_id: str = Field(
        description="Unique ID of the connect session.", examples=[ex.CONNECT_SESSION_ID]
    )
    state: str = Field(description="Current lifecycle state of the session.", examples=["created"])
    vendor_key: str = Field(
        description="Vendor registry key the session connects.", examples=[ex.VENDOR_KEY]
    )
    vendor_display_name: str = Field(description="Display name of the vendor.", examples=["GitHub"])
    resolved_flow: str = Field(
        description="The flow the session runs.", examples=["authorization_code"]
    )
    reason: str | None = Field(
        description="Free-text reason given when the session was started.",
        examples=["Needs read access to triage issues in the platform repo."],
    )
    requested_by_actor_id: str = Field(
        description="ID of the actor that started the session.", examples=[ex.AGENT_ID]
    )
    scopes: list[ReviewScopeResponse] = Field(
        description="The vendor's scope catalogue, flagged default and requested."
    )
    # Captured verbatim from ``IntegrationsConnectRequest.requested_permission_rules``
    # so the approve page can render them as pre-filled rows.
    requested_permission_rules: list[PermissionRuleSchema] = Field(
        default_factory=list, description="Permission rules the initiator asked for."
    )
    # Where the vendor's OpenAPI lives once imported — SPA needs this
    # to hit ``/apis/{vendor}/{name}/{version}/operations`` on the
    # rules-page preview.
    api_reference: ConnectSessionApiReferenceResponse = Field(
        description="Where the target's OpenAPI lives in the registry."
    )
    target_kind: Literal["vendor", "api"] = Field(
        default="vendor",
        description="`vendor` for a vendor-registry key, `api` for a registry API.",
        examples=["api"],
    )
    requested_scopes: list[str] = Field(
        default_factory=list,
        description="OAuth scopes the session asks for.",
        examples=[["repo", "read:org"]],
    )
    provenance: ReviewProvenanceResponse | None = Field(
        default=None, description="Where an API target's live revision came from."
    )
    agent: ReviewAgentResponse | None = Field(
        default=None, description="The agent the session binds, and its owner."
    )
    scheme: ReviewSchemeResponse | None = Field(
        default=None, description="The declared scheme an API target collects."
    )
    pinned_hosts: list[str] | None = Field(
        default=None,
        description="Hosts the credential may be sent to, pinned at `:connect`.",
        examples=[["api.github.com"]],
    )
    # Echo on ``:confirm`` with ``expected_agent_id``; a mismatch is 409
    # ``review_stale``.
    digest: str = Field(
        description="Fingerprint of the review; echo it on `:confirm`.",
        examples=[ex.REVIEW_DIGEST],
    )
    can_confirm: bool = Field(
        description="Whether this viewer may confirm the session as it stands.", examples=[True]
    )
    existing_credentials: list[ExistingCredentialResponse] = Field(
        default_factory=list, description="Credentials the viewer already holds for the target."
    )


# ---------------------------------------------------------------------------
# POST /connect-sessions/{id}:confirm
# ---------------------------------------------------------------------------


class OAuthConfirmSessionRequest(BaseModel):
    """Confirm an OAuth session: the scopes to request and the agent's rules.

    The default variant — a body without ``kind`` is this one.
    """

    kind: Literal["oauth"] = Field(
        default="oauth",
        description="Confirm variant; `oauth` (the default when omitted).",
        examples=["oauth"],
    )
    # Caps mirror ``IntegrationsConnectRequest`` — the eventual write
    # touches the same broker classification and binding paths, so leaving
    # them unbounded here would defeat the ``:connect`` cap. 100 is far
    # above any real vendor's scope catalog or a sane per-binding rule
    # count.
    confirmed_scopes: list[str] = Field(
        max_length=100,
        description="Upstream scopes the approver confirmed (at most 100).",
        examples=[["repo", "read:user"]],
    )
    permission_rules: list[PermissionRuleSchema] = Field(
        default_factory=list,
        max_length=100,
        description="Permission rules to bind for the agent (at most 100).",
    )
    # Selected at the rules-page Continue-click when the session was
    # opened without a target agent (user starts a session by clicking a
    # vendor tile, then picks the agent to bind on the way through).
    # Ignored when the session already carries an ``agent_id`` — the
    # service refuses to switch the target once a binding has been made
    # visible to the user.
    agent_id: str | None = Field(
        default=None,
        examples=[ex.AGENT_ID],
        description=(
            "Agent to bind when the session has none; ignored if the session already has one."
        ),
    )
    # The review the approver saw. Checked when given; required for an
    # ``api`` target.
    expected_agent_id: str | None = Field(
        default=None,
        examples=[ex.AGENT_ID],
        description=(
            "Agent ID from the review the approver saw; a mismatch is 409 review_stale. "
            "Required for an API target."
        ),
    )
    digest: str | None = Field(
        default=None,
        max_length=64,
        examples=[ex.REVIEW_DIGEST],
        description=(
            "Review digest echoed from the review data; a mismatch is 409 review_stale. "
            "Required for an API target."
        ),
    )


class _ReviewedConfirm(BaseModel):
    """Fields every non-OAuth confirm carries: rules and the review it answers."""

    # Secrets never echo back in a validation error.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    permission_rules: list[PermissionRuleSchema] = Field(
        max_length=100, description="Permission rules to bind for the agent (at most 100)."
    )
    agent_id: str | None = Field(
        default=None,
        examples=[ex.AGENT_ID],
        description=(
            "Agent to bind when the session has none; ignored if the session already has one."
        ),
    )
    expected_agent_id: str | None = Field(
        default=None,
        examples=[ex.AGENT_ID],
        description="Agent ID from the review the approver saw; a mismatch is 409 review_stale.",
    )
    digest: str = Field(
        min_length=1,
        max_length=64,
        examples=[ex.REVIEW_DIGEST],
        description="Review digest echoed from the review data; a mismatch is 409 review_stale.",
    )


class ApiKeyConfirmSessionRequest(_ReviewedConfirm):
    """Confirm a ``manual_api_key`` session with the API key to store."""

    kind: Literal["api_key"] = Field(
        description="Confirm variant; always `api_key`.", examples=["api_key"]
    )
    key: SecretStr = Field(
        min_length=1,
        max_length=8192,
        json_schema_extra=SENSITIVE,
        description="API key to store; write-only.",
        examples=["example-secret"],  # pragma: allowlist secret
    )


class BearerConfirmSessionRequest(_ReviewedConfirm):
    """Confirm a ``manual_bearer`` session with the bearer token to store."""

    kind: Literal["bearer"] = Field(
        description="Confirm variant; always `bearer`.", examples=["bearer"]
    )
    token: SecretStr = Field(
        min_length=1,
        max_length=8192,
        json_schema_extra=SENSITIVE,
        description="Bearer token to store; write-only.",
        examples=["example-secret"],  # pragma: allowlist secret
    )


class BasicConfirmSessionRequest(_ReviewedConfirm):
    """Confirm a ``manual_basic`` session with the username and password to store."""

    kind: Literal["basic"] = Field(
        description="Confirm variant; always `basic`.", examples=["basic"]
    )
    username: str = Field(
        min_length=1, max_length=255, description="Username to store.", examples=["svc-reporting"]
    )
    password: SecretStr = Field(
        min_length=1,
        max_length=8192,
        json_schema_extra=SENSITIVE,
        description="Password to store; write-only.",
        examples=["example-secret"],  # pragma: allowlist secret
    )


class OwnOAuthClientConfirmSessionRequest(_ReviewedConfirm):
    """Resolve an ``awaiting_app`` session with the approver's own OAuth client.

    The client is stored on the session's credential (the same shape as a
    ``direct_oauth2`` credential) and the session continues as an
    authorization-code connect. Both endpoints are required: the API's
    declared OAuth endpoints are never used for the approver's client, since
    the spec may be agent-submitted and the token endpoint receives the
    client secret.
    """

    kind: Literal["own_oauth_client"] = Field(
        description="Confirm variant; always `own_oauth_client`.", examples=["own_oauth_client"]
    )
    client_id: str = Field(
        min_length=1,
        max_length=255,
        description="Client ID of the approver's OAuth app.",
        examples=["Iv1.a1b2c3d4"],
    )
    client_secret: SecretStr = Field(
        min_length=1,
        max_length=8192,
        json_schema_extra=SENSITIVE,
        description="Client secret of the approver's OAuth app; write-only.",
        examples=["example-secret"],  # pragma: allowlist secret
    )
    authorize_url: str = Field(
        min_length=1,
        max_length=2048,
        description="Authorization endpoint URL of the OAuth app.",
        examples=["https://github.com/login/oauth/authorize"],
    )
    token_url: str = Field(
        min_length=1,
        max_length=2048,
        description="Token endpoint URL of the OAuth app.",
        examples=["https://github.com/login/oauth/access_token"],
    )
    confirmed_scopes: list[str] = Field(
        default_factory=list,
        max_length=100,
        examples=[["repo", "read:user"]],
        description="Upstream scopes to request (at most 100).",
    )


class ExistingCredentialConfirmSessionRequest(_ReviewedConfirm):
    """Bind a credential the approver already holds (OAuth only if its grant covers the ask)."""

    kind: Literal["existing_credential"] = Field(
        description="Confirm variant; always `existing_credential`.",
        examples=["existing_credential"],
    )
    credential_id: str = Field(
        min_length=1,
        max_length=30,
        description="ID of the approver's credential to bind.",
        examples=[ex.CREDENTIAL_ID],
    )


class ReauthorizeConfirmSessionRequest(_ReviewedConfirm):
    """Bind the approver's OAuth credential and re-consent it with the requested scopes.

    Refused when another agent is bound to the credential.
    """

    kind: Literal["reauthorize"] = Field(
        description="Confirm variant; always `reauthorize`.", examples=["reauthorize"]
    )
    credential_id: str = Field(
        min_length=1,
        max_length=30,
        description="ID of the approver's OAuth credential to bind and re-consent.",
        examples=[ex.CREDENTIAL_ID],
    )


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
    user_code: str = Field(
        description="The code the user enters at `verification_uri`.", examples=["WDJB-MJHT"]
    )
    verification_uri: str = Field(
        description="Where the user enters `user_code`.",
        examples=["https://github.com/login/device"],
    )
    verification_uri_complete: str | None = Field(
        default=None,
        description="`verification_uri` with the code filled in, when the vendor offers one.",
        examples=["https://github.com/login/device?user_code=WDJB-MJHT"],
    )
    poll_interval_seconds: int | None = Field(
        default=None, description="Seconds to wait between status polls.", examples=[5]
    )


class AuthCodeConfirmSessionResponse(BaseModel):
    """Authorization-code result — browser redirect target; completion
    lands server-side at ``/credentials/oauth/callback`` and the client
    observes it via ``/status``."""

    kind: Literal["authorization_code"] = "authorization_code"
    authorize_url: str = Field(
        description="Vendor sign-in URL to open in the approver's browser.",
        examples=["https://github.com/login/oauth/authorize?client_id=Iv1.8a61f9b3a7aba766"],
    )


class ConnectedConfirmSessionResponse(BaseModel):
    """The confirm finished the session; ``credential_id`` is bound to the agent."""

    kind: Literal["connected"] = "connected"
    credential_id: str = Field(
        description="ID of the credential bound to the agent.", examples=[ex.CREDENTIAL_ID]
    )


class ReauthorizeConfirmSessionResponse(BaseModel):
    """The agent is bound to ``credential_id``; open ``authorize_url`` to grant more scopes."""

    kind: Literal["reauthorize"] = "reauthorize"
    credential_id: str = Field(
        description="ID of the credential the agent is bound to.", examples=[ex.CREDENTIAL_ID]
    )
    authorize_url: str = Field(
        description="Vendor sign-in URL that grants the wider scopes.",
        examples=["https://github.com/login/oauth/authorize?client_id=Iv1.8a61f9b3a7aba766"],
    )


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


class ConnectSessionStatusResponse(BaseModel):
    """A connect session's lifecycle status."""

    status: Literal["pending", "polling", "connected", "failed", "expired"] = Field(
        description="The session's lifecycle status.", examples=["connected"]
    )
    connected_as: str | None = Field(
        default=None,
        description="Upstream account the credential connected as; null until known.",
        examples=["octocat"],
    )
    credential_id: str | None = Field(
        default=None,
        description="ID of the connected credential; null until connected.",
        examples=[ex.CREDENTIAL_ID],
    )
    bound_scopes: list[str] | None = Field(
        default=None,
        description="Scopes the connected credential holds.",
        examples=[["repo", "read:org"]],
    )
    error_code: str | None = Field(
        default=None,
        description="Why a failed session failed, e.g. `rejected` or `access_denied`.",
        examples=["access_denied"],
    )
