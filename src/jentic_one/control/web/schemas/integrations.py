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
    session_id: str
    approval_url: str
    poll_token: str
    resolved_flow: str
    revision_id: str | None = Field(
        default=None,
        description=(
            "The session's first revision; a confirm names it until the session is amended."
        ),
        examples=[ex.CONNECT_SESSION_REVISION_ID],
    )


# ---------------------------------------------------------------------------
# POST /integrations:edit-connection
# ---------------------------------------------------------------------------


class EditRuleAdd(BaseModel):
    """One rule to add to the binding."""

    model_config = ConfigDict(extra="forbid")

    rule: PermissionRuleSchema = Field(description="The rule to add.")
    before: str | None = Field(
        default=None,
        max_length=30,
        examples=[ex.PERMISSION_RULE_ID],
        description=(
            "ID of the rule to insert this one before (first match wins); appended when omitted."
        ),
    )


class EditRulesRequest(BaseModel):
    """A change to the binding's rules: `add`/`remove`, or the full list in `set`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    add: list[EditRuleAdd] = Field(
        default_factory=list, max_length=100, description="Rules to add (at most 100)."
    )
    remove: list[str] = Field(
        default_factory=list,
        max_length=100,
        examples=[[ex.PERMISSION_RULE_ID]],
        description="IDs of rules to remove (at most 100).",
    )
    set_rules: list[PermissionRuleSchema] | None = Field(
        default=None,
        alias="set",
        max_length=100,
        description=(
            "The full rule list to replace the current one with, in order (at most 100). "
            "Not combined with add or remove."
        ),
    )

    @model_validator(mode="after")
    def _set_alone(self) -> EditRulesRequest:
        if self.set_rules is not None and (self.add or self.remove):
            raise ValueError("pass either add/remove or set, not both")
        return self


class EditScopesRequest(BaseModel):
    """Scopes to add to the binding's OAuth credential."""

    model_config = ConfigDict(extra="forbid")

    add: list[str] = Field(
        default_factory=list,
        max_length=100,
        examples=[["gmail.send"]],
        description=(
            "Scopes to add (at most 100). Confirming runs a vendor consent on the credential; "
            "other agents bound to it gain them too."
        ),
    )


class EditConnectionRequest(BaseModel):
    """Ask to change an existing credential binding; a human approves the change."""

    model_config = ConfigDict(extra="forbid")

    credential_id: str = Field(
        min_length=1,
        max_length=30,
        examples=[ex.CREDENTIAL_ID],
        description="The binding's credential; it must already be bound to the agent.",
    )
    agent_id: str | None = Field(
        default=None,
        max_length=30,
        examples=[ex.AGENT_ID],
        description="The binding's agent. Required for humans; agent callers must omit it.",
    )
    rules: EditRulesRequest | None = Field(
        default=None,
        description=(
            "The rule change. While the binding points at a shared rule set, it changes "
            "that set's rules."
        ),
    )
    scopes: EditScopesRequest | None = Field(
        default=None,
        description=(
            "Scopes to add. OAuth credentials connected through an authorization-code or "
            "device-authorization app only; others are 422 scopes_not_supported."
        ),
    )
    resume: bool = Field(
        default=False,
        description="Switch a suspended binding back on; a no-op if it is already active.",
    )
    reason: str | None = Field(
        default=None,
        max_length=1024,
        examples=["Needs to send replies, not just read"],
        description="Why the change is needed, shown to the reviewer.",
    )
    apply: bool = Field(
        default=False,
        examples=[True],
        description=(
            "A human approving their own change as they make it: it is written at once, with "
            "no review. A change that adds scopes starts the vendor consent instead "
            "(`challenge`). Agent callers are 403."
        ),
    )


class AmendSessionRequest(BaseModel):
    """Change an open session's ask; fields sent replace those parts, the rest stay."""

    model_config = ConfigDict(extra="forbid")

    rules: EditRulesRequest | None = Field(
        default=None, description="Edit sessions: the rule change, relative to the baseline."
    )
    resume: bool | None = Field(
        default=None, examples=[True], description="Edit sessions: resume a suspended binding."
    )
    scopes: EditScopesRequest | None = Field(
        default=None, description="Edit sessions: the scopes to add, replacing the current ask."
    )
    requested_permission_rules: list[PermissionRuleSchema] | None = Field(
        default=None,
        max_length=100,
        description="Create sessions: the rules to ask for, replacing the current ask.",
    )
    requested_scopes: list[str] | None = Field(
        default=None,
        max_length=100,
        examples=[["repo"]],
        description="Create sessions: the scopes to ask for, replacing the current ask.",
    )
    reason: str | None = Field(
        default=None,
        max_length=1024,
        examples=["Also needs to label issues"],
        description="Why, shown to the reviewer.",
    )

    @model_validator(mode="after")
    def _changes_something(self) -> AmendSessionRequest:
        if not self.model_fields_set:
            raise ValueError("an amend must carry at least one field")
        return self


class AmendSessionResponse(BaseModel):
    """The session's new current revision."""

    session_id: str = Field(examples=[ex.CONNECT_SESSION_ID])
    revision_id: str = Field(examples=[ex.CONNECT_SESSION_REVISION_ID])
    revision_number: int = Field(description="2 for the first amend, and so on.", examples=[2])


class EditConnectionResponse(BaseModel):
    """The opened edit session."""

    session_id: str = Field(description="ID of the edit session.", examples=[ex.CONNECT_SESSION_ID])
    approval_url: str = Field(description="Review page to relay to the human approver.")
    poll_token: str = Field(description="Capability to poll the session's status.")
    revision_id: str = Field(
        description="ID of the session's current revision (`csr_…`).",
        examples=[ex.CONNECT_SESSION_REVISION_ID],
    )
    resolved_flow: str = Field(
        description=(
            "`edit` when the change needs no vendor step; otherwise the vendor flow confirming "
            "it runs (`authorization_code` or `device_authorization`)."
        ),
        examples=["edit"],
    )
    status: Literal["pending", "applied", "consenting"] = Field(
        default="pending",
        description=(
            "`pending` waits on a human's approval; with `apply`, `applied` is written and "
            "`consenting` waits on the vendor sign-in in `challenge`."
        ),
        examples=["pending"],
    )
    challenge: ConfirmSessionResponse | None = Field(
        default=None,
        description="With `apply` and added scopes: the vendor sign-in to complete.",
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
        description=(
            "ID of the session's credential: pending until a create session connects; an edit "
            "session's is the live credential it changes."
        ),
        examples=[ex.CREDENTIAL_ID],
    )
    intent: Literal["create", "edit"] = Field(
        default="create",
        description="`create` connects a new credential; `edit` changes an existing binding.",
        examples=["create"],
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
    # An OAuth scheme's authorize and token URLs, set only when the API's spec is
    # from the public catalog — offered to an approver bringing their own app.
    authorization_url: str | None = None
    token_url: str | None = None


class EditConflict(BaseModel):
    """One part of an edit request the binding's change since has broken."""

    kind: Literal["missing_rule", "rules_changed", "rule_set_changed"] = Field(
        description=(
            "`missing_rule`: a rule the request removes or adds before is gone. "
            "`rules_changed`: the request replaces rules that changed since. "
            "`rule_set_changed`: a shared rule set was attached or detached since."
        ),
        examples=["missing_rule"],
    )
    rule_id: str | None = Field(
        default=None, description="The missing rule, for `missing_rule`.", examples=["apr_1"]
    )


class EditAgentRef(BaseModel):
    agent_id: str = Field(examples=[ex.AGENT_ID])
    name: str | None = None


class ExistingCredentialResponse(BaseModel):
    """A credential the viewer already holds for the target.

    ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials
    only; a static credential's upstream permissions are not visible to the
    platform. Binding one with ``missing_scopes`` runs a vendor consent for
    them first (``needs_vendor_consent``), which widens the access of every
    agent in ``other_bound_agents`` too.
    """

    credential_id: str
    name: str
    type: str
    granted_scopes: list[str] | None = None
    missing_scopes: list[str] | None = None
    other_bound_agents: list[EditAgentRef] = Field(
        default_factory=list,
        description="Other agents bound to the credential; added scopes reach them too.",
    )
    can_bind: bool = Field(
        description=(
            "Whether this viewer may use it: its grant covers the request, or the missing "
            "scopes can be consented and the viewer may widen it."
        )
    )
    needs_vendor_consent: bool = Field(
        default=False,
        description="Binding it first runs a vendor consent for missing_scopes.",
    )


class EditRuleView(BaseModel):
    """A rule in an edit's current or resulting access."""

    id: str | None = Field(
        description="The rule's id; null for a rule the edit adds.",
        examples=[ex.PERMISSION_RULE_ID],
    )
    effect: str = Field(description="`allow`, `deny` or `require-approval`.", examples=["allow"])
    methods: list[str] | None = Field(default=None, examples=[["GET"]])
    path: str | None = Field(default=None, examples=["/v1/charges"])
    match_mode: str = Field(default="regex", examples=["prefix"])
    operations: list[str] | None = Field(default=None)
    comment: str | None = Field(default=None)


class EditRuleSetView(BaseModel):
    """The shared rule set a binding points at."""

    id: str = Field(examples=[ex.RULE_SET_ID])
    name: str = Field(examples=["Stripe read-only"])
    curated: bool = Field(description="A curated set is never changed; an edit copies it.")
    created_by: str | None = Field(default=None, description="The set's owner.")
    rules: list[EditRuleView]


class EditCurrentAccess(BaseModel):
    """The binding as the edit session recorded it when it opened."""

    rules: list[EditRuleView] = Field(description="The binding's own rules.")
    rule_set: EditRuleSetView | None = Field(
        default=None, description="The attached shared rule set; its rules apply instead."
    )
    suspended: bool
    granted_scopes: list[str] | None = None


class EditResultAccess(BaseModel):
    """What confirming the current revision leaves in place."""

    target: Literal["binding", "rule_set"] = Field(
        description="Whether the edit changes the binding's own rules or its shared rule set."
    )
    rules: list[EditRuleView]
    scopes: list[str]
    suspended: bool


class EditBindingRef(BaseModel):
    agent_id: str = Field(examples=[ex.AGENT_ID])
    credential_id: str = Field(examples=[ex.CREDENTIAL_ID])
    credential_name: str | None = None


class SessionRevisionRef(BaseModel):
    id: str = Field(
        description="ID of the current revision; confirm names it.",
        examples=[ex.CONNECT_SESSION_REVISION_ID],
    )
    number: int = Field(description="1 for the first ask, one more per amend.", examples=[2])


class ReviewEditResponse(BaseModel):
    """The binding change an edit session asks a human to approve."""

    binding: EditBindingRef
    revision: SessionRevisionRef
    current: EditCurrentAccess
    result: EditResultAccess
    widens_access: bool = Field(
        description="True when the change could let the agent do more than today."
    )
    other_bound_agents: list[EditAgentRef] = Field(
        default_factory=list,
        description=(
            "Other agents bound to the credential; added scopes and a shared rule set change "
            "reach them."
        ),
    )
    needs_vendor_consent: bool = Field(
        default=False,
        description="True when the result adds scopes, so confirming starts a vendor consent.",
    )
    conflicts: list[EditConflict] = Field(
        default_factory=list,
        description=(
            "What the request can't follow since the binding changed under it. While any are "
            "listed, a confirm must set `resolve_conflicts`."
        ),
    )


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
    # The current revision; every ``:confirm`` names it as ``revision_id``.
    revision: SessionRevisionRef | None = None
    # Edit sessions only.
    edit: ReviewEditResponse | None = None


class SessionRevisionResponse(BaseModel):
    """One version of what a connect session asks for."""

    id: str = Field(examples=[ex.CONNECT_SESSION_REVISION_ID])
    number: int = Field(description="1 for the first ask, one more per amend.", examples=[1])
    created_by: str | None = Field(
        default=None, description="Who began or amended the session; null for a rebase."
    )
    created_at: datetime
    proposal: dict[str, Any] = Field(description="The ask as it was sent.")
    result: dict[str, Any] = Field(
        description="What confirming this revision would leave in place (rules, scopes, ...)."
    )


class SessionRevisionListResponse(BaseModel):
    """A session's revisions, oldest first."""

    data: list[SessionRevisionResponse]


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
    revision_id: str | None = Field(
        default=None,
        max_length=30,
        examples=[ex.CONNECT_SESSION_REVISION_ID],
        description=(
            "The revision the approver reviewed (`revision.id`); required. Missing is 422 "
            "revision_required, a newer one 409 revision_changed."
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
    revision_id: str | None = Field(
        default=None,
        max_length=30,
        examples=[ex.CONNECT_SESSION_REVISION_ID],
        description=(
            "The revision the approver reviewed (`revision.id`); required. Missing is 422 "
            "revision_required, a newer one 409 revision_changed."
        ),
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
    """Bind a credential the approver already holds.

    When its OAuth grant lacks scopes the session asks for, the response is
    the vendor challenge for them and the bind lands with the grant.
    """

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


class EditConfirmSessionRequest(BaseModel):
    """Approve an edit session's change with the final rules the reviewer chose."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["edit"] = Field(description="Confirm variant; always `edit`.", examples=["edit"])
    revision_id: str = Field(
        min_length=1,
        max_length=30,
        examples=[ex.CONNECT_SESSION_REVISION_ID],
        description=(
            "The revision the reviewer saw (`edit.revision.id`); a newer one is 409 "
            "revision_changed."
        ),
    )
    permission_rules: list[PermissionRuleSchema] = Field(
        max_length=100,
        description=(
            "The final rule list to apply, in order (at most 100). Replaces the binding's own "
            "rules, or its shared rule set's rules while one is attached."
        ),
    )
    resume: bool = Field(
        default=False,
        examples=[False],
        description="Switch the binding back on if it is suspended.",
    )
    resolve_conflicts: bool = Field(
        default=False,
        examples=[False],
        description=(
            "Approve a revision that lists `conflicts`: the reviewer's `permission_rules` "
            "settle them. Without it, such a confirm is 409 edit_conflicted."
        ),
    )
    confirmed_scopes: list[str] | None = Field(
        default=None,
        max_length=100,
        examples=[["gmail.readonly", "gmail.send"]],
        description=(
            "The final scope set (at most 100). Scopes beyond the credential's grant start a "
            "vendor consent; the rules and scopes land together when it completes. Omit to "
            "keep the grant as it is."
        ),
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
    | Annotated[EditConfirmSessionRequest, Tag("edit")],
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


ConfirmSessionResponse = Annotated[
    DeviceAuthorizationConfirmSessionResponse
    | AuthCodeConfirmSessionResponse
    | ConnectedConfirmSessionResponse,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}/status
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    # ``applied``: an edit session whose change was confirmed (edit sessions
    # only; create sessions report ``connected``).
    status: Literal["pending", "polling", "connected", "applied", "failed", "expired"]
    connected_as: str | None = None
    credential_id: str | None = None
    bound_scopes: list[str] | None = None
    error_code: str | None = None
