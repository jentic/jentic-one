"""ConnectSessionService — orchestrates the agent-driven integration flow.

Owns the flow-agnostic state machine on ``connect_sessions`` and the
finalise / identity-echo / catalog-auto-import sequence. Delegates every
flow-specific bit (storage setup, vendor conversation, status probing,
transient cleanup) to ``AuthFlowHandler`` implementations under
``flow_handlers/``. The callback-only ``complete_from_callback`` path is
called on the concrete ``AuthCodeFlowHandler`` from
``complete_from_callback`` below — no Protocol lie.
"""

from __future__ import annotations

import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import structlog
from pydantic import SecretStr

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.connect_session_outcomes import (
    OUTCOME_APPLIED,
    OUTCOME_CANCELLED,
    OUTCOME_CONNECTED,
    OUTCOME_REJECTED,
    ConnectSessionOutcome,
)
from jentic_one.control.core.schema.connect_session_revisions import ConnectSessionRevision
from jentic_one.control.core.schema.connect_sessions import (
    INTENT_CREATE,
    INTENT_EDIT,
    TARGET_KIND_API,
    TARGET_KIND_VENDOR,
    ConnectSession,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.permission_rule_sets import PermissionRuleSetRule
from jentic_one.control.repos import (
    AgentPermissionRuleRepository,
    CredentialRepository,
    OAuthAppRegistrationRepository,
    OAuthTokenRepository,
    PermissionRuleSetRepository,
)
from jentic_one.control.repos.connect_session_outcome_repo import (
    ConnectSessionOutcomeRepository,
)
from jentic_one.control.repos.connect_session_repo import LIVE_STATES, ConnectSessionRepository
from jentic_one.control.repos.connect_session_revision_repo import (
    ConnectSessionRevisionRepository,
)
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.repos.effects_repo import AgentOwnerRow, EffectsRepository
from jentic_one.control.repos.oauth_client_credential_repo import (
    OAuthClientCredentialRepository,
)
from jentic_one.control.repos.prerequisite_repo import (
    AgentCredentialBindingRow,
    PrerequisiteRepository,
)
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.credentials.mapping import to_wire
from jentic_one.control.services.credentials.state import consume_callback_state
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.api_targets import (
    SCHEME_OAUTH2,
    catalog_oauth_endpoints,
    classify_spec_scope,
    missing_scopes,
    oauth_scope_descriptions_of,
    oauth_scopes_of,
    require_pinnable_hosts,
    review_digest,
    scheme_still_declared,
    select_scheme,
)
from jentic_one.control.services.integrations.dedupe import oauth_dedupe_key
from jentic_one.control.services.integrations.edits import (
    EditRequest,
    added_scopes,
    baseline_matches,
    require_change,
    resolve_edit,
    rule_content,
    rule_to_write,
    target_rules,
    widens_access,
)
from jentic_one.control.services.integrations.errors import (
    AgentInactiveError,
    AgentNotFoundError,
    AmendFieldsMismatchError,
    BaselineChangedError,
    ConfirmationForbiddenError,
    ConfirmKindMismatchError,
    ConnectOpenError,
    CredentialMissingCreatorError,
    EditBindingNotFoundError,
    EditOpenError,
    ExistingCredentialNotFoundError,
    InsufficientGrantedScopesError,
    InvalidOAuthAppRegistrationError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    ManualFlowsDisabledError,
    NoOpForFlowError,
    NothingToChangeError,
    OAuthAppChangedError,
    OwnClientInvalidError,
    RecentlyRejectedError,
    ReviewStaleError,
    RevisionChangedError,
    RevisionRequiredError,
    RuleSetConfirmForbiddenError,
    RulesRequiredError,
    SchemeChangedError,
    ScopesNotSupportedError,
    ScopeValidationError,
    ScopeWideningForbiddenError,
    SecuritySchemesLookupUnavailableError,
    ServersChangedError,
    SessionClosedError,
    SessionNotFoundError,
    TooManyOpenSessionsError,
    UnknownApiError,
    UnknownRuleError,
    UnsupportedTargetKindError,
)
from jentic_one.control.services.integrations.flow_handlers import (
    AuthCodeFlowHandler,
    AuthFlowHandler,
    DeviceAuthorizationHandler,
    handler_for,
)
from jentic_one.control.services.integrations.flow_handlers.auth_code import (
    RegistrationInactiveError,
)
from jentic_one.control.services.integrations.flow_handlers.base import (
    AuthCodeBeginResult,
    SuccessTokens,
)
from jentic_one.control.services.integrations.flow_handlers.manual import (
    ManualSecret,
    manual_handler_for,
    manual_handler_for_scheme,
)
from jentic_one.control.services.integrations.flow_handlers.session_app import SessionApp
from jentic_one.control.services.integrations.revisions import create_revision, revision_digest
from jentic_one.control.services.vendors.service import (
    AmbiguousVendorError,
    ResolvedScope,
    ResolvedVendorSource,
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
    VendorRegistryService,
)
from jentic_one.shared.access_guidance import catalog_api_id_covers, catalog_import_name
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_WRITE,
    CREDENTIALS_WRITE,
    ORG_ADMIN,
    compute_effective,
)
from jentic_one.shared.catalog import (
    ApiSecurityView,
    CatalogAutoImportProtocol,
    SecuritySchemesLookupProtocol,
)
from jentic_one.shared.config import (
    VendorAuthConfig,
    connect_approval_url,
    resolved_control_egress,
)
from jentic_one.shared.context import Context
from jentic_one.shared.crypto import hash_secret
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.events import emit_event_best_effort, summary_label
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ActorStatus, ActorType
from jentic_one.shared.models.actors import Origin, actor_type_label_from_id
from jentic_one.shared.models.api_identity import (
    CredentialScope,
    canonical_credential_scope,
    slugify_api_field,
)
from jentic_one.shared.models.credentials import StoredCredentialType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor
from jentic_one.shared.url_validation import validate_upstream_url_async
from jentic_one.shared.vendor_domain import vendor_from_api_id

_logger = structlog.get_logger(__name__)

# Metrics for the agent-driven vendor-connect flow. These are
# the on-call's aggregate signal: is device-flow suddenly failing across the
# fleet? What fraction of confirms actually connect? A per-``vendor`` +
# ``flow`` + ``outcome`` breakdown lets us tell "GitHub broke" from "our
# device-flow handler regressed" without spelunking through logs.
_meter = get_meter("control")
_sessions_created = _meter.create_counter(
    "control.integrations.sessions_created_total",
    description="Vendor-connect sessions created (POST /integrations:connect).",
)
_sessions_terminal = _meter.create_counter(
    "control.integrations.sessions_terminal_total",
    description=(
        "Vendor-connect sessions that reached a terminal state. "
        "outcome=connected|failed|expired|cancelled — one label captures both "
        "the happy path and every unhappy-terminal branch."
    ),
)
_time_to_connected = _meter.create_histogram(
    "control.integrations.time_to_connected_seconds",
    unit="s",
    description=(
        "Wall time from ``:connect`` to ``connected`` for successful vendor-connect sessions."
    ),
)


# ---------------------------------------------------------------------------
# Return types (Pydantic-free — the web layer wraps these into response models)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ApiTarget:
    """A registry API identity a connect session can target instead of a vendor key."""

    vendor: str
    name: str
    version: str


@dataclass(slots=True, frozen=True)
class CreatedSession:
    session_id: str
    approval_url: str
    poll_token: str
    resolved_flow: str
    # Revision 1, which a confirm names until the session is amended.
    revision_id: str | None = None


@dataclass(slots=True, frozen=True)
class CreatedEditSession:
    """An opened edit session: what the initiator relays and polls with."""

    session_id: str
    approval_url: str
    poll_token: str
    revision_id: str
    resolved_flow: str = "edit"


@dataclass(slots=True, frozen=True)
class ConsentSource:
    """How a credential is re-consented with more scopes.

    ``app`` is the client the credential was connected through (a shared
    registration, or its own stored client); ``key`` / ``entry`` are its
    vendor-registry source when it has one, for scope validation and the
    identity echo.
    """

    handler_cls: type[AuthFlowHandler]
    app: SessionApp
    key: str | None
    entry: VendorAuthConfig | None


@dataclass(slots=True, frozen=True)
class SessionAmend:
    """What an amend changes; ``fields`` names the parts it carries.

    ``rules``, ``resume`` and ``add_scopes`` (the ``scopes`` field) apply to
    edit sessions;
    ``requested_permission_rules`` and ``requested_scopes`` to create
    sessions; ``reason`` to both.
    """

    fields: frozenset[str]
    rules: EditRequest | None = None
    resume: bool = False
    reason: str | None = None
    requested_permission_rules: list[dict[str, object]] | None = None
    requested_scopes: list[str] | None = None
    add_scopes: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class AmendedSession:
    session_id: str
    revision_id: str
    revision_number: int


@dataclass(slots=True, frozen=True)
class EditReview:
    """What an edit session's reviewer decides on.

    ``current`` is the binding as the session's baseline recorded it,
    ``result`` what confirming the current revision would leave in place.
    """

    agent_id: str
    credential_id: str
    credential_name: str | None
    revision_id: str
    revision_number: int
    current: dict[str, Any]
    result: dict[str, Any]
    widens_access: bool
    other_bound_agents: list[AgentView]
    # The result adds scopes: confirming starts a vendor consent.
    needs_vendor_consent: bool = False


@dataclass(slots=True, frozen=True)
class ScopeView:
    name: str
    classification: str
    default: bool
    requested: bool
    description: str


@dataclass(slots=True, frozen=True)
class ReviewData:
    session_id: str
    state: str
    vendor_key: str
    vendor_display_name: str
    resolved_flow: str
    reason: str | None
    requested_by_actor_id: str
    scopes: list[ScopeView]
    # As-requested permission rules from ``:connect`` — the human hasn't
    # approved them yet, they render on the review page as pre-filled rows.
    requested_permission_rules: list[dict[str, object]]
    # Where the vendor's OpenAPI lives once it's been imported by the
    # catalog auto-importer. Nullable because the version isn't known
    # until the import finishes; SPA polls / falls back to "importing…"
    # when it's ``None``.
    api_vendor: str
    api_name: str | None
    api_version: str | None
    target_kind: str = TARGET_KIND_VENDOR
    requested_scopes: list[str] = field(default_factory=list)
    # Server data the approver decides on (never echoed from the caller).
    provenance: ProvenanceView | None = None
    agent: AgentView | None = None
    scheme: SchemeView | None = None
    pinned_hosts: list[str] | None = None
    # SHA-256 over the fields above; ``:confirm`` echoes it back.
    digest: str = ""
    # Whether this viewer may confirm (so secret entry is blocked up front).
    can_confirm: bool = False
    # Credentials the viewer already holds for the target, with the
    # granted-scope comparison.
    existing_credentials: list[ExistingCredentialView] = field(default_factory=list)
    # The session's current revision; ``:confirm`` names it.
    revision_id: str | None = None
    revision_number: int | None = None
    # Edit sessions only: the binding change under review.
    edit: EditReview | None = None


@dataclass(slots=True, frozen=True)
class ProvenanceView:
    """Where an API target's live revision came from."""

    origin: str | None
    catalog_api_id: str | None
    submitted_by: str | None
    source_url: str | None
    revision_id: str


@dataclass(slots=True, frozen=True)
class AgentView:
    """The agent a session binds and its owner (admin DB, read at review)."""

    agent_id: str
    name: str | None
    owner_id: str | None
    status: str


@dataclass(slots=True, frozen=True)
class SchemeView:
    """The declared scheme an API target collects a credential for (spec snapshot)."""

    type: str
    location: str | None
    field_name: str | None
    # A public-catalog spec's authorization-code endpoints, offered to an approver
    # bringing their own OAuth app (see ``catalog_oauth_endpoints``).
    authorization_url: str | None = None
    token_url: str | None = None


@dataclass(slots=True, frozen=True)
class ExistingCredentialView:
    """A credential the approver could bind instead of connecting a new one.

    ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials
    only (``None`` for static ones, whose upstream permissions the platform
    cannot see). ``other_bound_agents`` are the agents other than the
    session's that are bound to it: granting the missing scopes widens
    their access too. ``needs_vendor_consent`` is set when binding it runs a
    vendor consent for ``missing_scopes`` first, and ``can_bind`` says
    whether this viewer may use it (widening a credential others use is
    its owner's or an org admin's call).
    """

    credential_id: str
    name: str
    #: The wire type, as ``GET /credentials`` reports it (``oauth2``, ``api_key``, ...).
    type: str
    granted_scopes: list[str] | None
    missing_scopes: list[str] | None
    other_bound_agents: list[AgentView]
    can_bind: bool
    needs_vendor_consent: bool


@dataclass(slots=True, frozen=True)
class SessionSummary:
    """Slim list-row projection for the console list (never the poll_token)."""

    session_id: str
    state: str
    vendor_key: str
    vendor_display_name: str
    agent_id: str | None
    requested_by_actor_id: str
    reason: str | None
    connected_as: str | None
    error_code: str | None
    created_at: datetime
    credential_id: str
    #: ``create`` connects a new credential; ``edit`` changes an existing binding.
    intent: str = INTENT_CREATE


@dataclass(slots=True, frozen=True)
class SessionPage:
    """Cursor-paginated envelope of :class:`SessionSummary` rows."""

    data: list[SessionSummary]
    has_more: bool
    next_cursor: str | None


@dataclass(slots=True, frozen=True)
class DeviceAuthorizationConfirmResult:
    """RFC 8628 confirm outcome — user_code + verification_uri.

    The ``kind`` matches the wire discriminator on
    ``ConnectChallengeResponse`` (``"device_authorization"``), not the persisted
    ``VendorFlowConfig.kind`` (``"device_authorization"``) — those are separate
    contracts.
    """

    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    poll_interval_seconds: int | None = None
    kind: str = "device_authorization"


@dataclass(slots=True, frozen=True)
class AuthCodeConfirmResult:
    """Authorization-code confirm outcome — client redirects to authorize_url."""

    authorize_url: str
    kind: str = "authorization_code"


@dataclass(slots=True, frozen=True)
class ConnectedConfirmResult:
    """The confirm finished the session: ``credential_id`` is bound to the agent."""

    credential_id: str
    kind: str = "connected"


ConfirmResult = DeviceAuthorizationConfirmResult | AuthCodeConfirmResult | ConnectedConfirmResult


@dataclass(slots=True, frozen=True)
class ConfirmChecks:
    """What every non-OAuth confirm carries besides its variant fields."""

    permission_rules: list[dict[str, object]]
    expected_agent_id: str | None
    digest: str | None
    agent_id: str | None = None
    revision_id: str | None = None


@dataclass(slots=True, frozen=True)
class SecretConfirm:
    """Confirm a ``manual_*`` session with the secret the approver entered."""

    kind: str
    secret: ManualSecret
    checks: ConfirmChecks


@dataclass(slots=True, frozen=True)
class OwnClientConfirm:
    """Resolve an ``awaiting_app`` session with the approver's own OAuth client.

    The approver enters both endpoints. The API's declared OAuth endpoints are
    never a fallback: the spec may be agent-submitted, and the token endpoint
    receives the client secret.
    """

    client_id: str
    client_secret: SecretStr
    authorize_url: str
    token_url: str
    confirmed_scopes: list[str]
    checks: ConfirmChecks


@dataclass(slots=True, frozen=True)
class ExistingCredentialConfirm:
    """Bind a credential the approver already holds (consenting to any missing scopes)."""

    credential_id: str
    checks: ConfirmChecks


ConfirmVariant = SecretConfirm | OwnClientConfirm | ExistingCredentialConfirm

#: Confirm ``kind`` values on the wire.
CONFIRM_KIND_OAUTH = "oauth"
CONFIRM_KIND_OWN_CLIENT = "own_oauth_client"
CONFIRM_KIND_EXISTING = "existing_credential"
CONFIRM_KIND_EDIT = "edit"


@dataclass(slots=True, frozen=True)
class StatusResult:
    status: str  # "pending" | "polling" | "connected" | "applied" | "failed" | "expired"
    connected_as: str | None = None
    credential_id: str | None = None
    bound_scopes: list[str] | None = None
    error_code: str | None = None


# ---------------------------------------------------------------------------
# Config / defaults
# ---------------------------------------------------------------------------

# Overall hard TTL for a vendor OAuth session — clamps stale rows even if
# flow-level device_code_expires_at hasn't been reached.
_SESSION_TTL_SECONDS = 30 * 60

# Flows that live for ``_SESSION_TTL_SECONDS``. Every other flow (a session a
# human completes by entering a credential, or one waiting for an OAuth app)
# lives for ``control.connect.manual_flows_ttl_hours``.
_OAUTH_FLOWS: tuple[str, ...] = (DeviceAuthorizationHandler.kind, AuthCodeFlowHandler.kind)

# How long a session's terminal outcome is kept once the session has ended.
_OUTCOME_RETENTION = timedelta(days=30)

#: ``resolved_flow`` / ``state`` of an OAuth API target with no OAuth app yet.
AWAITING_APP = "awaiting_app"

#: Amend fields that apply only to one intent.
_CREATE_AMEND_FIELDS = frozenset({"requested_permission_rules", "requested_scopes"})
_EDIT_AMEND_FIELDS = frozenset({"rules", "resume", "scopes"})

#: ``resolved_flow`` of an edit session: no vendor conversation.
EDIT_FLOW = "edit"

#: ``error_code`` of a session a human explicitly rejected.
ERROR_REJECTED = "rejected"

#: How many agents bound to a credential the review lists (re-authorize check).
_BOUND_AGENTS_LIMIT = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class _CapScope:
    """Whose open sessions a new session counts against."""

    agent_id: str | None
    owner_id: str | None
    owned_agent_ids: list[str]


def _state_secret(ctx: Context) -> str:
    return ctx.config.credentials.connect.state_secret.get_secret_value()


def _session_app_from_registration(ctx: Context, registration: OAuthAppRegistration) -> SessionApp:
    """Project an admin-registered OAuth app row into the handler seam.

    ``client_secret_provider`` is a lazy closure: device flow (a public
    client) never calls it, and auth-code only invokes it once at
    ``complete_from_callback`` time. Endpoints come off the flow-specific
    extension row, which is eager-loaded via ``selectin`` on the ORM model,
    so no extra DB round-trip is issued here.
    """
    if registration.flow_kind == "authorization_code":
        ac = registration.authorization_code_details
        assert ac is not None, (
            "authorization_code registration missing its details extension "
            "(schema invariant broken)"
        )
        encrypted_secret = ac.encrypted_client_secret

        def _decrypt() -> str:
            return ctx.encryption.decrypt(encrypted_secret)

        return SessionApp(
            flow_kind="authorization_code",
            client_id=registration.client_id,
            client_secret_provider=_decrypt,
            default_scopes=list(ac.default_scopes or []),
            registration_id=registration.id,
            authorize_url=ac.authorize_url,
            token_url=ac.token_url,
        )
    if registration.flow_kind == "device_authorization":
        dev = registration.device_authorization_details
        assert dev is not None, (
            "device_authorization registration missing its details extension "
            "(schema invariant broken)"
        )
        return SessionApp(
            flow_kind="device_authorization",
            client_id=registration.client_id,
            client_secret_provider=None,
            default_scopes=list(dev.default_scopes or []),
            registration_id=registration.id,
            authorization_endpoint=dev.authorization_endpoint,
            token_endpoint=dev.token_endpoint,
        )
    raise NoOpForFlowError(f"unsupported registration flow_kind: {registration.flow_kind!r}")


def _session_app_from_flow(flow: Any) -> SessionApp:
    """Project a config-shipped ``VendorFlowConfig`` into the handler seam.

    ``registration_id`` is None on this path — handlers write the legacy
    embedded aux row instead of setting the credential's registration FK.
    """
    if flow.kind == "authorization_code":
        secret_value: str = flow.client_secret.get_secret_value()

        def _config_secret() -> str:
            return secret_value

        return SessionApp(
            flow_kind="authorization_code",
            client_id=flow.client_id,
            client_secret_provider=_config_secret,
            default_scopes=[],
            registration_id=None,
            authorize_url=flow.authorize_url,
            token_url=flow.token_url,
        )
    if flow.kind == "device_authorization":
        return SessionApp(
            flow_kind="device_authorization",
            client_id=flow.client_id,
            client_secret_provider=None,
            default_scopes=[],
            registration_id=None,
            authorization_endpoint=flow.authorization_endpoint,
            token_endpoint=flow.token_endpoint,
        )
    raise NoOpForFlowError(f"unsupported config flow kind: {flow.kind!r}")


def _scope_view(s: ResolvedScope) -> ScopeView:
    return ScopeView(
        name=s.name,
        classification=s.classification,
        default=s.default,
        requested=s.requested,
        description=s.description,
    )


def _source_key(row: ConnectSession) -> str | None:
    """The vendor-registry key a session's OAuth app resolves from, if it has one.

    A ``vendor`` target's ``vendor`` is the key. An ``api`` target's ``vendor``
    is the API vendor; its key is ``vendor_key``, set only once an OAuth app
    resolved from the vendor registry for it.
    """
    if row.target_kind == TARGET_KIND_VENDOR:
        return row.vendor
    return row.vendor_key


def _require_vendor_source(row: ConnectSession, *, action: str) -> str:
    """Return the session's vendor-registry key, refusing a session without one.

    Review, confirm and finalise of an OAuth app re-open the session's source
    by key; an ``api`` target's ``vendor`` is an API vendor, so reading it as a
    key would silently pick the wrong source.
    """
    key = _source_key(row)
    if key is None:
        raise UnsupportedTargetKindError(row.id, row.target_kind, action)
    return key


def _outcome_for(state: str, error_code: str | None) -> str:
    """The recorded outcome of a terminal transition.

    A cancel is stored as state ``failed`` with ``error_code="cancelled"`` (the
    wire contract of ``/status``); its outcome is ``cancelled``. An explicit
    rejection is ``failed`` with ``error_code="rejected"``.
    """
    if state == "failed" and error_code == "cancelled":
        return OUTCOME_CANCELLED
    if state == "failed" and error_code == ERROR_REJECTED:
        return OUTCOME_REJECTED
    return state


def _api_covers(row_vendor: str, api_name: str, api_version: str) -> Callable[[str], bool]:
    """Whether a catalog api_id (config entry / shared app) is the given API identity.

    Matched on every name a catalog import of the api_id can register under
    (``catalog_api_id_covers``), the same footing the broker's directives use.
    """

    def covers(catalog_api_id: str) -> bool:
        return catalog_api_id_covers(
            catalog_api_id, vendor=row_vendor, name=api_name, version=api_version
        )

    return covers


def _variant_kind(variant: ConfirmVariant) -> str:
    if isinstance(variant, SecretConfirm):
        return variant.kind
    if isinstance(variant, OwnClientConfirm):
        return CONFIRM_KIND_OWN_CLIENT
    return CONFIRM_KIND_EXISTING


def _allowed_confirm_kinds(row: ConnectSession) -> list[str]:
    """Confirm ``kind`` values that apply to the session's state and flow."""
    if row.intent == INTENT_EDIT:
        return [CONFIRM_KIND_EDIT]
    if row.state == AWAITING_APP:
        primary = CONFIRM_KIND_OWN_CLIENT
    else:
        manual = manual_handler_for(row.resolved_flow)
        primary = manual.scheme_kind if manual is not None else CONFIRM_KIND_OAUTH
    return [primary, CONFIRM_KIND_EXISTING]


def _require_confirm_kind(row: ConnectSession, kind: str) -> None:
    allowed = _allowed_confirm_kinds(row)
    if kind not in allowed:
        raise ConfirmKindMismatchError(kind, allowed)


def _provenance_view(view: ApiSecurityView | None) -> ProvenanceView | None:
    if view is None:
        return None
    return ProvenanceView(
        origin=view.provenance.origin,
        catalog_api_id=view.provenance.catalog_api_id,
        submitted_by=view.provenance.submitted_by,
        source_url=view.provenance.source_url,
        revision_id=view.revision_id,
    )


def _review_digest(
    row: ConnectSession, agent: AgentView | None, view: ApiSecurityView | None
) -> str:
    """Digest over everything the approver decides on, all of it server data.

    Covers the target, its live provenance, the agent and its owner, the
    scheme and pinned hosts, the requested rules, scopes and reason, and
    the session's state and flow — so a confirm against a review that no
    longer matches (a re-resolved app, a different agent, a new revision
    source) is refused.
    """
    provenance = _provenance_view(view)
    return review_digest(
        {
            "session_id": row.id,
            "state": row.state,
            "target_kind": row.target_kind,
            "vendor": row.vendor,
            "vendor_key": row.vendor_key,
            "api_name": row.api_name,
            "api_version": row.api_version,
            "resolved_flow": row.resolved_flow,
            "agent_id": row.agent_id,
            "agent_owner_id": agent.owner_id if agent is not None else None,
            "scheme": [row.scheme_type, row.scheme_location, row.scheme_field_name],
            "oauth_endpoints": (
                list(catalog_oauth_endpoints(view) or ())
                if row.scheme_type == SCHEME_OAUTH2
                else None
            ),
            "pinned_hosts": sorted(row.pinned_hosts or []),
            "requested_permission_rules": row.requested_permission_rules or [],
            "requested_scopes": sorted(row.requested_scopes or []),
            "reason": row.reason,
            "provenance": (
                [
                    provenance.origin,
                    provenance.catalog_api_id,
                    provenance.submitted_by,
                    provenance.source_url,
                ]
                if provenance is not None
                else None
            ),
        }
    )


def _require_api_own_client(row: ConnectSession) -> None:
    """Only an ``api`` target finishing on the approver's own client has no source key."""
    if row.target_kind != TARGET_KIND_API or row.resolved_flow != AuthCodeFlowHandler.kind:
        raise UnsupportedTargetKindError(row.id, row.target_kind, "finalise")


def _rule_snapshot(rule: AgentPermissionRule | PermissionRuleSetRule) -> dict[str, Any]:
    """A stored rule as an edit baseline records it: its id plus its content."""
    return {
        "id": rule.id,
        "effect": rule.effect,
        "methods": rule.methods,
        "path": rule.path,
        "match_mode": rule.match_mode,
        "operations": rule.operations,
        "comment": rule.comment,
    }


def _consent_handler_for(credential: Credential) -> type[AuthFlowHandler] | None:
    """The session flow that can re-consent ``credential`` with more scopes, if any."""
    for handler_cls in (AuthCodeFlowHandler, DeviceAuthorizationHandler):
        if (
            credential.type == handler_cls.stored_type.value
            and credential.provider == handler_cls.provider_id
        ):
            return handler_cls
    return None


def _is_oauth_type(credential_type: str) -> bool:
    return credential_type.startswith("OAUTH2_")


def _require_state(row: ConnectSession, *, expected: str, action: str) -> None:
    if row.state != expected:
        raise InvalidStateTransitionError(row.id, row.state, action)


def _require_revision(row: ConnectSession, revision_id: str | None) -> None:
    """A confirm must name the session's current revision, the one its reviewer saw."""
    if revision_id is None:
        raise RevisionRequiredError()
    if revision_id != row.current_revision_id:
        raise RevisionChangedError(row.id, row.current_revision_id)


def _session_closed(row: ConnectSession) -> SessionClosedError:
    """The refusal of an amend on a closed session; an edit's points at the binding."""
    error = SessionClosedError(row.id)
    if row.intent == INTENT_EDIT:
        error.point_at(row.credential_id, row.agent_id)
    return error


def _forbid_agent_confirm(caller_actor_type: ActorType) -> None:
    """Every session is confirmed by a human on the review page, never by an agent.

    This holds whoever started the session: a poll token reaching an agent
    (its own ask, or one a user started for it) must never let the agent
    approve its own access. ``caller_actor_type`` comes from the caller's
    verified identity, not from the payload.
    """
    if caller_actor_type == ActorType.AGENT:
        raise ConfirmationForbiddenError("connect sessions cannot be confirmed by an agent")


def _poll_token_matches(row: ConnectSession, token: str | None) -> bool:
    """Hash the presented poll_token and compare it to the stored digest in constant time."""
    if token is None:
        return False
    return secrets.compare_digest(row.poll_token_hash, hash_secret(token))


# Agents in these states can no longer use a credential, so ``:confirm`` refuses
# to bind one in their name.
_UNUSABLE_AGENT_STATUSES: frozenset[str] = frozenset(
    {ActorStatus.ARCHIVED.value, ActorStatus.DISABLED.value, ActorStatus.REJECTED.value}
)


def _is_org_admin(identity: Identity) -> bool:
    """``org:admin`` among the caller's effective permissions (implied grants count)."""
    return ORG_ADMIN in compute_effective(set(identity.permissions))


def _is_owner_approver(identity: Identity) -> bool:
    """An agent's owner may approve for it only with both write permissions.

    Confirm writes the agent's credential binding (which the bind route gates
    on ``agents:write``) and its permission rules on a credential
    (``credentials:write``). ``org:admin`` implies both.
    """
    effective = compute_effective(set(identity.permissions))
    return ORG_ADMIN in effective or {CREDENTIALS_WRITE, AGENTS_WRITE} <= effective


def _terminal_status(
    row: ConnectSession,
    bound_scopes: list[str] | None,
) -> StatusResult:
    """Serialise a terminal session back to a StatusResult.

    ``bound_scopes`` comes from the flow-agnostic ``oauth_token.scope``
    column (populated by ``_finalise_connected`` from
    ``SuccessTokens.granted_scopes``) — same source, both flows.
    """
    if row.state == "connected":
        return StatusResult(
            status="connected",
            connected_as=row.connected_as,
            credential_id=row.credential_id,
            bound_scopes=bound_scopes,
        )
    return StatusResult(
        status=row.state,
        error_code=row.error_code,
    )


class ConnectSessionService:
    """Orchestrates connect sessions across their state machine."""

    def __init__(
        self,
        ctx: Context,
        catalog_auto_importer: CatalogAutoImportProtocol | None = None,
        security_schemes_lookup: SecuritySchemesLookupProtocol | None = None,
    ) -> None:
        self._ctx = ctx
        self._vendors = VendorRegistryService(ctx)
        # Optional cross-surface seam: when the process also serves the
        # registry surface, an auto-importer is wired in so a fresh vendor
        # connect enqueues the OpenAPI import the broker will need. When
        # absent (registry deployed elsewhere) the finalise path skips the
        # import step silently — the operator retains the manual escape hatch
        # (``POST /catalog/{api_id}:import``).
        self._catalog_auto_importer = catalog_auto_importer
        # Optional cross-surface seam: reads a registry API's live revision
        # (declared schemes, hosts, provenance) for sessions that target an
        # API. Absent when this process cannot read the registry.
        self._security_schemes_lookup = security_schemes_lookup

    @property
    def _manual_flows_enabled(self) -> bool:
        return self._ctx.config.control.connect.manual_flows_enabled

    # ---- create -----------------------------------------------------------

    async def create_session(
        self,
        *,
        vendor_key: str,
        agent_id: str | None,
        identity: Identity,
        requested_scopes: list[str] | None = None,
        preferred_flow: str | None = None,
        reason: str | None = None,
        # Wire-shape ``PermissionRuleSchema`` dicts — validated at the
        # router boundary. Stored on the session row and surfaced on the
        # review page so the human owner sees exactly what the agent asked
        # for before committing anything to ``agent_permission_rules``.
        requested_permission_rules: list[dict[str, object]] | None = None,
        # Optional user-facing label for the resulting credential — lets a
        # user distinguish multiple credentials minted from the same vendor
        # (or shared registration). Falls back to the vendor's display name.
        credential_name: str | None = None,
        # Optional pin to a specific admin-registered OAuth app. Required
        # when the vendor has no config entry and several active
        # registrations offer the flow (``AmbiguousVendorError`` otherwise).
        oauth_app_registration_id: str | None = None,
        # A registry API identity to target instead of ``vendor_key``. Refused
        # while ``control.connect.manual_flows_enabled`` is off.
        api_target: ApiTarget | None = None,
        # The agent's proposed scheme for an ``api`` target: a declared
        # scheme's name or kind. The spec decides; this only picks among
        # what it declares.
        auth_type: str | None = None,
    ) -> CreatedSession:
        """Create a pending session + upfront credential row.

        ``identity`` is the initiator. An agent caller may name only itself
        (the router forces that); a user may name only an agent they own and
        could approve for, and ``org:admin`` any agent. Any other
        ``agent_id`` is refused before anything is counted or written (see
        :meth:`_require_session_agent_allowed`), so a session can never land
        in another owner's console or count against their open-session caps.
        When ``agent_id`` is None no
        agent-credential binding is created at confirm time — the user is
        connecting a credential without granting any agent access to it,
        and can bind an agent later through the credentials API.

        A repeat ask from an agent that already has an open session for the
        same target is :class:`ConnectOpenError` naming that session: the
        agent amends it (``amend_session``) instead. An agent
        whose ask for the same target a human rejected within the cooldown
        gets :class:`RecentlyRejectedError`.
        """
        agent = await self._require_session_agent_allowed(agent_id, identity)
        initiator_actor_id = identity.sub
        if api_target is not None:
            if not self._manual_flows_enabled:
                raise ManualFlowsDisabledError()
            return await self._create_api_session(
                api_target,
                auth_type=auth_type,
                agent_id=agent_id,
                agent=agent,
                identity=identity,
                requested_scopes=requested_scopes or [],
                preferred_flow=preferred_flow,
                reason=reason,
                requested_permission_rules=requested_permission_rules or [],
                credential_name=credential_name,
                oauth_app_registration_id=oauth_app_registration_id,
            )

        # Entry, flow and minting app come from one source — the pinned
        # registration, the config entry, or the vendor's single active
        # registration — so the credential's identity and scope catalog can
        # never belong to a different app than the one that mints it.
        resolved = await self._vendors.resolve_connect_source(
            vendor_key,
            registration_id=oauth_app_registration_id,
            preferred_flow=preferred_flow,
        )
        entry, flow = resolved.entry, resolved.flow

        try:
            handler_cls = handler_for(flow.kind)
        except KeyError as exc:
            raise NoOpForFlowError(flow.kind) from exc
        handler = handler_cls(self._ctx)

        await self._check_rejection_cooldown(
            agent_id, target_kind=TARGET_KIND_VENDOR, vendor=vendor_key
        )
        # One open session per agent-started OAuth request: the same agent
        # asking again for the same vendor, app and scopes gets the open one.
        dedupe_key = (
            oauth_dedupe_key(
                resolved_flow=flow.kind,
                registration_id=resolved.registration.id if resolved.registration else None,
                requested_scopes=requested_scopes or [],
            )
            if agent_id is not None and agent_id == initiator_actor_id
            else None
        )

        async def _find_open(db: Any) -> ConnectSession | None:
            assert agent_id is not None and dedupe_key is not None
            return await ConnectSessionRepository.get_open_vendor_target(
                db, agent_id=agent_id, vendor=vendor_key, dedupe_key=dedupe_key
            )

        if dedupe_key is not None:
            assert agent_id is not None

            async def _find_open_for_vendor(db: Any) -> ConnectSession | None:
                assert agent_id is not None
                return await ConnectSessionRepository.get_open_vendor_create(
                    db, agent_id=agent_id, vendor=vendor_key
                )

            await self._refuse_open_session(_find_open_for_vendor)
        cap_scope = await self._check_open_session_caps(agent_id, identity, agent)

        session_app = self._session_app_for(resolved)

        poll_token = secrets.token_urlsafe(32)

        api_scope = await self._vendor_api_scope(entry.vendor)

        try:
            async with self._ctx.control_db.transaction() as session:
                await self._enforce_open_session_caps(session, cap_scope)
                credential = await CredentialRepository.create(
                    session,
                    type=handler.stored_type.value,
                    # Credential name is display-only + user-editable. Prefer
                    # the caller-supplied label; fall back to the vendor's
                    # display name when the caller didn't pick one.
                    name=(credential_name.strip() if credential_name else None)
                    or entry.display_name,
                    api_vendor=api_scope.vendor,
                    api_name=api_scope.name,
                    catalog_api_id=entry.vendor,
                    created_by=initiator_actor_id,
                    provider=handler.provider_id,
                    state="pending",
                )
                await handler.prepare(
                    session,
                    credential_id=credential.id,
                    app=session_app,
                    requested_scopes=requested_scopes or [],
                    created_by=initiator_actor_id,
                )
                row = await ConnectSessionRepository.create(
                    session,
                    credential_id=credential.id,
                    vendor=vendor_key,
                    agent_id=agent_id,
                    initiator_actor_id=initiator_actor_id,
                    state="created",
                    resolved_flow=flow.kind,
                    poll_token_hash=hash_secret(poll_token),
                    dedupe_key=dedupe_key,
                    requested_scopes=requested_scopes or [],
                    requested_permission_rules=requested_permission_rules or [],
                    preferred_flow=preferred_flow,
                    reason=reason,
                    created_by=initiator_actor_id,
                )
                await self._record_first_revision(session, row)
        except DatabaseIntegrityError:
            # A concurrent identical ask won the dedupe index: point at its session.
            if dedupe_key is None:
                raise
            await self._refuse_open_session(_find_open)
            raise

        approval_url = self._approval_url_for(row.id)
        _logger.info(
            "connect_session.created",
            session_id=row.id,
            vendor=vendor_key,
            agent_id=agent_id,
            initiator_actor_id=initiator_actor_id,
        )
        _sessions_created.add(1, {"vendor": vendor_key, "flow": flow.kind})
        # Connect-session lifecycle is auditable — the row plus its cascade
        # (upfront credential, aux-flow row, later binding) can create
        # material access, and operators need to be able to reconstruct
        # "who started this, when, for which vendor" from the audit log
        # rather than only from ephemeral scanner state. Best-effort so a
        # failed admin-DB write never rolls back the committed session.
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=identity.actor_type.value,
            actor_id=initiator_actor_id,
            origin=identity.origin.value,
            after={
                "vendor": vendor_key,
                "resolved_flow": flow.kind,
                "agent_id": agent_id,
                "credential_id": row.credential_id,
            },
        )
        await self._emit_session_created(row, vendor_display_name=entry.display_name, agent=agent)
        # Kick off the vendor's OpenAPI import as early as we can — the SPA
        # opens the connect dialog and immediately calls ``:connect``, so
        # firing here (rather than at ``:confirm``) gives the import
        # ~seconds while the user reviews scopes + rules. By the time the
        # rules-page operation-impact preview mounts, the ops list is
        # usually available. Idempotent + best-effort; ``:confirm`` still
        # calls this as a defensive re-trigger for edge cases.
        await self._maybe_import_catalog(api_id=entry.vendor, initiator_actor_id=initiator_actor_id)
        return CreatedSession(
            session_id=row.id,
            approval_url=approval_url,
            poll_token=poll_token,
            resolved_flow=flow.kind,
            revision_id=row.current_revision_id,
        )

    async def _create_api_session(
        self,
        target: ApiTarget,
        *,
        auth_type: str | None,
        agent_id: str | None,
        agent: AgentOwnerRow | None,
        identity: Identity,
        requested_scopes: list[str],
        preferred_flow: str | None,
        reason: str | None,
        requested_permission_rules: list[dict[str, object]],
        credential_name: str | None,
        oauth_app_registration_id: str | None,
    ) -> CreatedSession:
        """Open a session for a registry API identity.

        The API's live revision decides everything about the credential: the
        declared scheme (``auth_type`` only picks among declared ones), where
        a key is injected, and the hosts it may go to. Scheme and hosts are
        snapshot on the session and re-checked at confirm. An OAuth scheme
        resolves its app like a vendor connect (pin → config entry → the one
        covering shared app); with none, the session waits in
        ``awaiting_app``.

        Only an agent's own ask is deduplicated, against the open session it
        started for the same API identity — like a vendor target. A user
        naming the agent always gets a session of their own, so neither side
        can rotate the other's poll token or swap the other's request.
        """
        initiator_actor_id = identity.sub
        view = await self._lookup_api(target.vendor, target.name, target.version)
        if view is None:
            raise UnknownApiError(target.vendor, target.name, target.version)
        scheme = select_scheme(view, auth_type)
        require_pinnable_hosts(view)

        await self._check_rejection_cooldown(
            agent_id,
            target_kind=TARGET_KIND_API,
            vendor=view.vendor,
            api_name=view.name,
            api_version=view.version,
        )

        agent_started = agent_id is not None and agent_id == initiator_actor_id

        async def _find_open(db: Any) -> ConnectSession | None:
            assert agent_id is not None and agent_started
            return await ConnectSessionRepository.get_open_api_target(
                db,
                agent_id=agent_id,
                vendor=view.vendor,
                api_name=view.name,
                api_version=view.version,
            )

        if agent_started:
            await self._refuse_open_session(_find_open)
        cap_scope = await self._check_open_session_caps(agent_id, identity, agent)

        manual = manual_handler_for_scheme(scheme.kind)
        oauth_source: tuple[str, ResolvedVendorSource] | None = None
        if scheme.kind == SCHEME_OAUTH2:
            oauth_source = await self._vendors.resolve_api_source(
                _api_covers(view.vendor, view.name, view.version),
                registration_id=oauth_app_registration_id,
                preferred_flow=preferred_flow,
            )

        vendor_key: str | None = None
        oauth_handler: AuthFlowHandler | None = None
        session_app: SessionApp | None = None
        catalog_api_id = view.provenance.catalog_api_id
        if manual is not None:
            stored_type, provider, state, resolved_flow = (
                manual.stored_type,
                manual.provider_id,
                "created",
                manual.kind,
            )
        elif oauth_source is not None:
            vendor_key, source = oauth_source
            try:
                handler_cls = handler_for(source.flow.kind)
            except KeyError as exc:
                raise NoOpForFlowError(source.flow.kind) from exc
            oauth_handler = handler_cls(self._ctx)
            session_app = self._session_app_for(source)
            stored_type, provider, state, resolved_flow = (
                oauth_handler.stored_type,
                oauth_handler.provider_id,
                "created",
                source.flow.kind,
            )
            catalog_api_id = source.entry.vendor
        else:
            # The credential's type is a placeholder until an app resolves;
            # ``connect_sessions.credential_id`` is NOT NULL.
            stored_type, provider, state, resolved_flow = (
                StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
                AuthCodeFlowHandler.provider_id,
                AWAITING_APP,
                AWAITING_APP,
            )

        # Name and version pinned: the credential covers exactly this API.
        api_scope = canonical_credential_scope(
            vendor=view.vendor, name=view.name, version=view.version
        )
        poll_token = secrets.token_urlsafe(32)
        try:
            async with self._ctx.control_db.transaction() as session:
                await self._enforce_open_session_caps(session, cap_scope)
                credential = await CredentialRepository.create(
                    session,
                    type=stored_type.value,
                    name=(credential_name.strip() if credential_name else None)
                    or view.display_name
                    or f"{view.vendor}/{view.name}",
                    api_vendor=api_scope.vendor,
                    api_name=api_scope.name,
                    api_version=api_scope.version,
                    catalog_api_id=catalog_api_id,
                    created_by=initiator_actor_id,
                    provider=provider,
                    state="pending",
                )
                if oauth_handler is not None and session_app is not None:
                    await oauth_handler.prepare(
                        session,
                        credential_id=credential.id,
                        app=session_app,
                        requested_scopes=requested_scopes,
                        created_by=initiator_actor_id,
                    )
                row = await ConnectSessionRepository.create(
                    session,
                    credential_id=credential.id,
                    target_kind=TARGET_KIND_API,
                    vendor=view.vendor,
                    api_name=view.name,
                    api_version=view.version,
                    scheme_type=scheme.kind,
                    scheme_location=scheme.location,
                    scheme_field_name=scheme.field_name,
                    pinned_hosts=list(view.hosts),
                    vendor_key=vendor_key,
                    agent_id=agent_id,
                    initiator_actor_id=initiator_actor_id,
                    state=state,
                    resolved_flow=resolved_flow,
                    poll_token_hash=hash_secret(poll_token),
                    requested_scopes=requested_scopes,
                    requested_permission_rules=requested_permission_rules,
                    preferred_flow=preferred_flow,
                    reason=reason,
                    created_by=initiator_actor_id,
                )
                await self._record_first_revision(session, row)
        except DatabaseIntegrityError:
            # A concurrent identical agent ask won the dedupe index.
            if not agent_started:
                raise
            await self._refuse_open_session(_find_open)
            raise

        _logger.info(
            "connect_session.created",
            session_id=row.id,
            vendor=view.vendor,
            api_name=view.name,
            api_version=view.version,
            resolved_flow=resolved_flow,
            agent_id=agent_id,
            initiator_actor_id=initiator_actor_id,
        )
        _sessions_created.add(1, {"vendor": view.vendor, "flow": resolved_flow})
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=identity.actor_type.value,
            actor_id=initiator_actor_id,
            origin=identity.origin.value,
            after={
                "target_kind": TARGET_KIND_API,
                "vendor": view.vendor,
                "api_name": view.name,
                "api_version": view.version,
                "resolved_flow": resolved_flow,
                "scheme_type": scheme.kind,
                "agent_id": agent_id,
                "credential_id": row.credential_id,
            },
        )
        await self._emit_session_created(
            row, vendor_display_name=f"{view.vendor}/{view.name}", agent=agent
        )
        return CreatedSession(
            session_id=row.id,
            approval_url=self._approval_url_for(row.id),
            poll_token=poll_token,
            resolved_flow=resolved_flow,
            revision_id=row.current_revision_id,
        )

    @staticmethod
    async def _record_first_revision(session: Any, row: ConnectSession) -> None:
        """Write revision 1 of a new create session: its full ask."""
        payload = create_revision(
            permission_rules=row.requested_permission_rules or [],
            scopes=row.requested_scopes or [],
            reason=row.reason,
        )
        revision = await ConnectSessionRevisionRepository.append(
            session,
            session_id=row.id,
            proposal=payload.proposal,
            result=payload.result,
            digest=payload.digest,
            created_by=row.initiator_actor_id,
        )
        row.current_revision_id = revision.id

    async def _lookup_api(self, vendor: str, name: str, version: str) -> ApiSecurityView | None:
        """The API's live revision through the registry seam (identity canonicalised)."""
        if self._security_schemes_lookup is None:
            raise SecuritySchemesLookupUnavailableError()
        return await self._security_schemes_lookup.lookup(
            vendor=slugify_api_field(vendor), name=slugify_api_field(name), version=version.strip()
        )

    async def _refuse_open_session(
        self, find_open: Callable[[Any], Awaitable[ConnectSession | None]]
    ) -> None:
        """Refuse a repeat ask while the session ``find_open`` finds is still open.

        The ask is not reused or frozen: the initiator amends the open session
        (``:amend``) instead, so the reviewer sees one request with its
        history. :class:`ConnectOpenError` names that session and its
        approval URL.
        """
        async with self._ctx.control_db.session() as session:
            row = await find_open(session)
        if row is None:
            return
        _logger.info(
            "connect_session.repeat_refused",
            session_id=row.id,
            vendor=row.vendor,
            agent_id=row.agent_id,
        )
        raise ConnectOpenError(row.id, self._approval_url_for(row.id))

    async def _check_rejection_cooldown(
        self,
        agent_id: str | None,
        *,
        target_kind: str,
        vendor: str,
        api_name: str | None = None,
        api_version: str | None = None,
        credential_id: str | None = None,
    ) -> None:
        """Refuse an agent's repeat ask for a target a human rejected within the cooldown.

        With ``credential_id`` the ask is an edit, and the target is that
        binding: a rejected edit of it holds back the next one.
        """
        hours = self._ctx.config.control.connect.rejection_cooldown_hours
        if agent_id is None or hours <= 0:
            return
        now = datetime.now(UTC)
        cooldown = timedelta(hours=hours)
        async with self._ctx.control_db.session() as session:
            if credential_id is not None:
                ended = await ConnectSessionOutcomeRepository.latest_edit_ended_at(
                    session,
                    agent_id=agent_id,
                    credential_id=credential_id,
                    outcome=OUTCOME_REJECTED,
                    since=now - cooldown,
                )
            else:
                ended = await ConnectSessionOutcomeRepository.latest_ended_at(
                    session,
                    agent_id=agent_id,
                    outcome=OUTCOME_REJECTED,
                    target_kind=target_kind,
                    vendor=vendor,
                    api_name=api_name,
                    api_version=api_version,
                    since=now - cooldown,
                )
        if ended is None:
            return
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=UTC)
        retry_after = max(1, math.ceil((ended + cooldown - now).total_seconds()))
        raise RecentlyRejectedError(retry_after)

    async def _check_open_session_caps(
        self, agent_id: str | None, identity: Identity, agent: AgentOwnerRow | None
    ) -> _CapScope | None:
        """Refuse a new session past the per-agent or per-owner open-session cap.

        Enforced while ``manual_flows_enabled`` is on. The owner is the
        agent's owner (``agent`` is the row :meth:`_require_session_agent_allowed`
        already read), or the initiating user when no agent is named; an
        owner's count covers their own sessions and their agents'. This is
        the early refusal; :meth:`_enforce_open_session_caps` repeats the
        count under a lock inside the creating transaction, so concurrent
        asks cannot all pass. Returns the scope that re-check needs (``None``
        when the caps are off).
        """
        if not self._manual_flows_enabled:
            return None
        owner_id: str | None = None
        if agent_id is not None:
            owner_id = agent.owner_id if agent is not None else None
        elif identity.actor_type == ActorType.USER:
            owner_id = identity.sub
        owned: list[str] = []
        if owner_id is not None:
            async with self._ctx.admin_db.session() as admin_session:
                owned = await PrerequisiteRepository.list_agent_ids_owned_by(
                    admin_session, owner_id=owner_id
                )
        scope = _CapScope(agent_id=agent_id, owner_id=owner_id, owned_agent_ids=owned)
        async with self._ctx.control_db.session() as session:
            await self._count_against_caps(session, scope)
        return scope

    async def _enforce_open_session_caps(self, session: Any, scope: _CapScope | None) -> None:
        """Re-count the caps inside the creating transaction, serialised per owner.

        Every creation for the same owner (or ownerless agent) takes the same
        transaction-scoped lock first, so the count and the insert are atomic
        with respect to each other. SQLite serialises write transactions
        already (``BEGIN IMMEDIATE``).
        """
        if scope is None:
            return
        lock_key = scope.owner_id or scope.agent_id
        if lock_key is None:
            return
        await ConnectSessionRepository.acquire_open_session_cap_lock(session, lock_key)
        await self._count_against_caps(session, scope)

    async def _count_against_caps(self, session: Any, scope: _CapScope) -> None:
        cfg = self._ctx.config.control.connect
        if scope.agent_id is not None:
            per_agent = await ConnectSessionRepository.count_open(
                session, agent_ids=[scope.agent_id]
            )
            if per_agent >= cfg.max_open_sessions_per_agent:
                raise TooManyOpenSessionsError("agent", cfg.max_open_sessions_per_agent)
        if scope.owner_id is not None:
            per_owner = await ConnectSessionRepository.count_open(
                session, agent_ids=scope.owned_agent_ids, initiator_actor_id=scope.owner_id
            )
            if per_owner >= cfg.max_open_sessions_per_owner:
                raise TooManyOpenSessionsError("owner", cfg.max_open_sessions_per_owner)

    def _session_app_for(self, resolved: ResolvedVendorSource) -> SessionApp:
        """Build the session's OAuth-app material from its resolved source.

        A registration-backed source carries the admin-registered client
        (secret decrypted); a config source carries the operator-config flow.
        """
        if resolved.registration is not None:
            return _session_app_from_registration(self._ctx, resolved.registration)
        return _session_app_from_flow(resolved.flow)

    async def _emit_session_created(
        self,
        row: ConnectSession,
        *,
        vendor_display_name: str | None,
        agent: AgentOwnerRow | None,
    ) -> None:
        """Emit the informational ``connect_session.created`` rail event (best-effort).

        Only for sessions an agent started for itself — the signal is "your
        agent is waiting for you"; a human connecting in the SPA is already
        looking at the result. ``agent`` is the row read when the session was
        allowed, so its name needs no second lookup. ``created_by`` is the
        agent, so the owner-scoped event read shows the row to the agent's
        owner (and ``org:admin``). The open session carries the live state,
        so the event never asks for action. The summary and data name only
        the agent, the vendor and the session — never the poll token.
        """
        if row.agent_id is None or row.initiator_actor_id != row.agent_id:
            return
        try:
            agent_name = agent.name if agent is not None else None
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CONNECT_SESSION_CREATED,
                    severity=EventSeverity.INFO,
                    summary=(
                        f"Agent {summary_label(agent_name, row.agent_id)} asked to connect "
                        f"{summary_label(vendor_display_name, row.vendor)}"
                    ),
                    requires_action=False,
                    data={
                        "session_id": row.id,
                        "agent_id": row.agent_id,
                        "vendor_key": row.vendor,
                        "credential_id": row.credential_id,
                    },
                    created_by=row.agent_id,
                    actor_id=row.initiator_actor_id,
                    actor_type=ActorType.AGENT.value,
                )
        except Exception:
            _logger.warning(
                "telemetry_emit_failed",
                event_type=EventType.CONNECT_SESSION_CREATED,
                session_id=row.id,
                exc_info=True,
            )

    def _approval_url_for(self, session_id: str) -> str:
        """Build the human-facing approval URL for an agent-initiated session.

        Lands on the Agents page (``/app/agents``) with the session id as the
        ``approve`` query param; the SPA detects it, opens the credential
        inventory and auto-opens the credential dialog into the vendor-approval
        flow. The URL carries no ``poll_token``: the target agent's owner and
        ``org:admin`` review, poll, confirm and cancel without it, so the token
        stays with the agent and never rides in a browser URL.

        The URL is relayed out-of-band (CLI output, MCP tool result), so it must
        be absolute even with no public URL configured — ``connect_approval_url``
        falls back to ``bind_origin`` rather than yielding a bare path. The
        broker's denial ``provisioning_url`` uses the same builder.
        """
        return connect_approval_url(self._ctx.config, session_id)

    # ---- amend ------------------------------------------------------------

    async def amend_session(
        self, session_id: str, *, identity: Identity, amend: SessionAmend
    ) -> AmendedSession:
        """Change what an open session asks for, as a new revision.

        Only the session's initiator, and only while it is open: a create
        session before it is confirmed (``created`` or ``awaiting_app``), an
        edit session in ``created``. A confirmed or ended session is
        :class:`SessionClosedError`. Anyone else gets the uniform 403.

        The fields ``amend`` carries replace those parts of the ask; the rest
        stay as the current revision has them. An edit's rule change and
        ``resume`` resolve against the session's baseline like the ask it
        opened with; a create session's ``requested_permission_rules``,
        ``requested_scopes`` and ``reason`` replace its ask outright. The
        target, flow and agent never change.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
            outcome = (
                await ConnectSessionOutcomeRepository.get_by_session_id(read_session, session_id)
                if row is None
                else None
            )
        if row is None:
            if outcome is not None and outcome.created_by == identity.sub:
                closed = SessionClosedError(session_id)
                closed.point_at(outcome.credential_id, outcome.agent_id)
                raise closed
            raise InvalidPollTokenError("invalid poll_token")
        if row.initiator_actor_id != identity.sub:
            raise InvalidPollTokenError("invalid poll_token")
        open_states: tuple[str, ...] = (
            ("created",) if row.intent == INTENT_EDIT else ("created", AWAITING_APP)
        )
        if row.state not in open_states:
            raise _session_closed(row)
        current = await self._current_revision(row)
        if current is None:
            raise _session_closed(row)

        fields: dict[str, Any]
        if row.intent == INTENT_EDIT:
            foreign = sorted(amend.fields & _CREATE_AMEND_FIELDS)
            if foreign:
                raise AmendFieldsMismatchError(INTENT_EDIT, foreign)
            assert row.baseline is not None
            previous = EditRequest.from_proposal(current.proposal)
            rules = amend.rules if "rules" in amend.fields else previous
            assert rules is not None
            request = EditRequest(
                add=rules.add,
                remove=rules.remove,
                set_rules=rules.set_rules,
                add_scopes=(
                    list(amend.add_scopes) if "scopes" in amend.fields else previous.add_scopes
                ),
                resume=amend.resume if "resume" in amend.fields else previous.resume,
                reason=amend.reason if "reason" in amend.fields else previous.reason,
            )
            try:
                result, _ = resolve_edit(row.baseline, request)
                require_change(row.baseline, result)
            except (UnknownRuleError, NothingToChangeError) as exc:
                exc.point_at(row.credential_id, row.agent_id)
                raise
            proposal = request.as_proposal()
            digest = revision_digest(baseline=row.baseline, result=result, reason=request.reason)
            fields = {"reason": request.reason}
        else:
            foreign = sorted(amend.fields & _EDIT_AMEND_FIELDS)
            if foreign:
                raise AmendFieldsMismatchError(INTENT_CREATE, foreign)
            permission_rules = (
                amend.requested_permission_rules
                if "requested_permission_rules" in amend.fields
                else row.requested_permission_rules
            ) or []
            scopes = (
                amend.requested_scopes
                if "requested_scopes" in amend.fields
                else row.requested_scopes
            ) or []
            reason = amend.reason if "reason" in amend.fields else row.reason
            payload = create_revision(
                permission_rules=permission_rules, scopes=scopes, reason=reason
            )
            proposal, result, digest = payload.proposal, payload.result, payload.digest
            fields = {
                "requested_permission_rules": list(permission_rules),
                "requested_scopes": list(scopes),
                "reason": reason,
            }
            if row.dedupe_key is not None:
                fields["dedupe_key"] = await self._amended_dedupe_key(row, scopes)

        revision = ConnectSessionRevision(
            id=generate_ksuid("csr"),
            session_id=row.id,
            number=current.number + 1,
            proposal=proposal,
            result=result,
            digest=digest,
            created_by=identity.sub,
        )
        try:
            async with self._ctx.control_db.transaction() as session:
                won = await ConnectSessionRepository.advance_revision(
                    session,
                    row.id,
                    open_states=open_states,
                    from_revision_id=current.id,
                    to_revision_id=revision.id,
                    **fields,
                )
                if not won:
                    latest = await ConnectSessionRepository.get_by_id(session, row.id)
                    if latest is None or latest.state not in open_states:
                        raise _session_closed(row)
                    raise RevisionChangedError(row.id, latest.current_revision_id)
                await ConnectSessionRevisionRepository.insert(session, revision)
        except DatabaseIntegrityError:
            # The amended scopes now match another open session of the agent's.
            assert row.agent_id is not None and "dedupe_key" in fields
            agent_id, dedupe_key = row.agent_id, fields["dedupe_key"]

            async def _find_open(db: Any) -> ConnectSession | None:
                return await ConnectSessionRepository.get_open_vendor_target(
                    db, agent_id=agent_id, vendor=row.vendor, dedupe_key=dedupe_key
                )

            await self._refuse_open_session(_find_open)
            raise

        _logger.info(
            "connect_session.amended",
            session_id=row.id,
            revision_id=revision.id,
            number=revision.number,
        )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            origin=identity.origin.value,
            before={"revision_id": current.id},
            after={"revision_id": revision.id, "number": revision.number},
        )
        await self._emit_amended(row, revision)
        return AmendedSession(
            session_id=row.id, revision_id=revision.id, revision_number=revision.number
        )

    async def _amended_dedupe_key(self, row: ConnectSession, scopes: list[str]) -> str:
        """The dedupe key of an agent-started vendor session once its scopes change."""
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
        return oauth_dedupe_key(
            resolved_flow=row.resolved_flow,
            registration_id=credential.oauth_app_registration_id if credential else None,
            requested_scopes=scopes,
        )

    async def _emit_amended(self, row: ConnectSession, revision: ConnectSessionRevision) -> None:
        """Emit ``connect_session.amended`` so the reviewer knows the ask changed."""
        if row.agent_id is None:
            return
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CONNECT_SESSION_AMENDED,
                    severity=EventSeverity.INFO,
                    summary=(
                        f"A connect request for agent {row.agent_id} changed "
                        f"(revision {revision.number})"
                    ),
                    requires_action=False,
                    data={
                        "session_id": row.id,
                        "agent_id": row.agent_id,
                        "revision_id": revision.id,
                        "number": revision.number,
                    },
                    created_by=row.agent_id,
                    actor_id=row.initiator_actor_id,
                    actor_type=actor_type_label_from_id(row.initiator_actor_id),
                )
        except Exception:
            _logger.warning(
                "telemetry_emit_failed",
                event_type=EventType.CONNECT_SESSION_AMENDED,
                session_id=row.id,
                exc_info=True,
            )

    # ---- edit sessions ----------------------------------------------------

    async def create_edit_session(
        self,
        *,
        credential_id: str,
        agent_id: str,
        identity: Identity,
        request: EditRequest,
    ) -> CreatedEditSession:
        """Open a session that edits an existing ``(agent, credential)`` binding.

        An agent edits its own binding (the router injects its id); a human
        names the agent and must be its approver (``org:admin``, or its owner
        with ``credentials:write`` and ``agents:write``). A binding that does
        not exist, one the caller may not edit, and an agent's suspended
        binding all answer :class:`EditBindingNotFoundError`.

        The binding is snapshot as the session's baseline and the request is
        resolved against it as revision 1. A request that changes nothing is
        :class:`NothingToChangeError`; one naming a rule the binding does not
        hold is :class:`UnknownRuleError`; a binding with an open edit is
        :class:`EditOpenError` naming that session. Nothing is written to the
        binding until a human confirms.
        """
        binding = await self._require_editable_binding(credential_id, agent_id, identity)
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, credential_id)
            open_edit = await ConnectSessionRepository.get_open_edit(
                session, agent_id=agent_id, credential_id=credential_id
            )
        if credential is None or credential.state == "pending":
            raise EditBindingNotFoundError(credential_id)
        if open_edit is not None:
            error = EditOpenError(open_edit.id)
            error.point_at(credential_id, agent_id)
            raise error
        await self._check_rejection_cooldown(
            agent_id, target_kind=TARGET_KIND_VENDOR, vendor="", credential_id=credential_id
        )
        baseline = await self._binding_snapshot(agent_id, credential_id, binding.rule_set_id)
        baseline["suspended"] = binding.suspended
        try:
            result, _ = resolve_edit(baseline, request)
            require_change(baseline, result)
        except (UnknownRuleError, NothingToChangeError) as exc:
            exc.point_at(credential_id, agent_id)
            raise
        # The vendor-registry key a consent for added scopes re-opens, kept
        # on the session like a vendor connect's; ``None`` when the
        # credential cannot be re-consented here.
        consent = await self._consent_source(credential)
        if added_scopes(baseline, result) and consent is None:
            raise ScopesNotSupportedError()

        cap_scope = await self._check_open_session_caps(agent_id, identity.sub)
        poll_token = secrets.token_urlsafe(32)
        try:
            async with self._ctx.control_db.transaction() as session:
                await self._enforce_open_session_caps(session, cap_scope)
                row = await ConnectSessionRepository.create(
                    session,
                    intent=INTENT_EDIT,
                    baseline=baseline,
                    credential_id=credential_id,
                    vendor=(
                        (consent.key if consent is not None else None)
                        or credential.api_vendor
                        or credential.catalog_api_id
                        or credential.name
                    ),
                    agent_id=agent_id,
                    initiator_actor_id=identity.sub,
                    state="created",
                    resolved_flow=EDIT_FLOW,
                    poll_token_hash=hash_secret(poll_token),
                    reason=request.reason,
                    created_by=identity.sub,
                )
                revision = await ConnectSessionRevisionRepository.append(
                    session,
                    session_id=row.id,
                    proposal=request.as_proposal(),
                    result=result,
                    digest=revision_digest(baseline=baseline, result=result, reason=request.reason),
                    created_by=identity.sub,
                )
        except DatabaseIntegrityError:
            # A concurrent edit of the same binding won the open-edit index.
            async with self._ctx.control_db.session() as session:
                winner = await ConnectSessionRepository.get_open_edit(
                    session, agent_id=agent_id, credential_id=credential_id
                )
            if winner is None:
                raise
            raise EditOpenError(winner.id) from None

        _logger.info(
            "connect_session.edit_created",
            session_id=row.id,
            agent_id=agent_id,
            credential_id=credential_id,
            initiator_actor_id=identity.sub,
        )
        _sessions_created.add(1, {"vendor": row.vendor, "flow": EDIT_FLOW})
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            origin=identity.origin.value,
            after={
                "intent": INTENT_EDIT,
                "agent_id": agent_id,
                "credential_id": credential_id,
                "revision_id": revision.id,
                "widens_access": widens_access(baseline, result),
            },
        )
        await self._emit_edit_requested(row, credential_name=credential.name)
        return CreatedEditSession(
            session_id=row.id,
            approval_url=self._approval_url_for(row.id),
            poll_token=poll_token,
            revision_id=revision.id,
            resolved_flow=(
                consent.handler_cls.kind
                if consent is not None and added_scopes(baseline, result)
                else EDIT_FLOW
            ),
        )

    async def confirm_edit(
        self,
        session_id: str,
        *,
        poll_token: str | None,
        identity: Identity,
        revision_id: str,
        permission_rules: list[dict[str, object]],
        resume: bool,
        confirmed_scopes: list[str] | None = None,
    ) -> ConfirmResult:
        """Apply an edit session's change as the reviewer submitted it.

        ``revision_id`` must be the session's current revision
        (:class:`RevisionChangedError` otherwise), and the binding must still
        match the session's baseline: when it changed since the session
        opened, the session is rebased onto it as a new revision and the
        confirm is :class:`BaselineChangedError`. The reviewer's
        ``permission_rules``, ``resume`` and ``confirmed_scopes`` are what
        gets applied, never the initiator's proposal.

        While the binding points at a shared rule set, the rules replace that
        set's rules, and only the set's owner or an org admin may confirm. A
        curated set is never changed: the rules become the binding's own and
        the set is detached from this binding only.

        ``confirmed_scopes`` beyond the credential's grant start a vendor
        consent on the credential's own flow (authorization code or device
        authorization). The session goes ``polling``, the approved change is
        held on it, and rules and scopes land together when the grant
        arrives; until then the binding keeps its rules and token. Widening a
        credential other agents use needs its owner or an org admin.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        row = await self._require_session_access(row, poll_token=poll_token, identity=identity)
        _require_confirm_kind(row, CONFIRM_KIND_EDIT)
        if identity.actor_type == ActorType.AGENT:
            raise ConfirmationForbiddenError("edit sessions are confirmed by a human")
        _require_state(row, expected="created", action="confirm")
        assert row.agent_id is not None and row.baseline is not None
        await self._require_agent_binding_allowed(row.agent_id, identity)
        if row.current_revision_id != revision_id:
            raise RevisionChangedError(row.id, row.current_revision_id)

        live = await self._live_binding_snapshot(row)
        if live is None:
            await self._mark_terminal(
                row.id, "failed", "binding removed during edit", error_code="binding_removed"
            )
            raise EditBindingNotFoundError(row.credential_id)
        if not baseline_matches(row.baseline, live):
            raise BaselineChangedError(row.id, await self._rebase_edit(row, live))

        rule_set = row.baseline.get("rule_set")
        copy_curated = rule_set is not None and bool(rule_set["curated"])
        if rule_set is not None and not copy_curated:
            owner = rule_set.get("created_by")
            if ORG_ADMIN not in identity.permissions and owner != identity.sub:
                raise RuleSetConfirmForbiddenError(rule_set["id"])
        granted = list(row.baseline.get("granted_scopes") or [])
        scopes = sorted(set(granted) | set(confirmed_scopes or []))
        change: dict[str, Any] = {
            "rules": [rule_to_write(r) for r in permission_rules],
            "rule_set_id": rule_set["id"] if rule_set is not None and not copy_curated else None,
            "detach_rule_set": copy_curated,
            "resume": resume and bool(row.baseline["suspended"]),
            "bind": False,
            "scopes": scopes,
            "outcome": OUTCOME_APPLIED,
            "approved_by": identity.sub,
            "revision_id": revision_id,
        }

        if set(scopes) - set(granted):
            return await self._begin_edit_consent(row, change, identity=identity)

        async with self._ctx.control_db.transaction() as session:
            won = await ConnectSessionRepository.transition_edit(
                session, row.id, revision_id=revision_id, to_state=OUTCOME_APPLIED
            )
            if not won:
                current = await ConnectSessionRepository.get_by_id(session, row.id)
                if current is not None and current.state == "created":
                    raise RevisionChangedError(row.id, current.current_revision_id)
                raise InvalidStateTransitionError(row.id, row.state, "confirm")
            await self._write_change(session, row, change)
        await self._apply_admin_change(row, change)
        await self._record_change_applied(row, change, origin=identity.origin.value)
        return ConnectedConfirmResult(credential_id=row.credential_id)

    async def _begin_edit_consent(
        self, row: ConnectSession, change: dict[str, Any], *, identity: Identity
    ) -> ConfirmResult:
        """Hold ``change`` on the session and start the vendor consent for its scopes."""
        assert row.agent_id is not None
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
        consent = await self._consent_source(credential) if credential is not None else None
        if credential is None or consent is None:
            raise ScopesNotSupportedError()
        handler_cls = consent.handler_cls
        if consent.entry is not None:
            unknown = self._vendors.validate_scopes(consent.entry, change["scopes"])
            if unknown:
                raise ScopeValidationError(unknown)
        await self._require_scope_widening_allowed(credential, row.agent_id, identity)

        async with self._ctx.control_db.transaction() as session:
            won = await ConnectSessionRepository.transition_edit(
                session,
                row.id,
                revision_id=change["revision_id"],
                to_state="polling",
                resolved_flow=handler_cls.kind,
                approved_change=change,
            )
            if not won:
                current = await ConnectSessionRepository.get_by_id(session, row.id)
                if current is not None and current.state == "created":
                    raise RevisionChangedError(row.id, current.current_revision_id)
                raise InvalidStateTransitionError(row.id, row.state, "confirm")
            polling = await ConnectSessionRepository.get_by_id(session, row.id)
        assert polling is not None
        challenge = await self._begin_consent(polling, consent, change["scopes"])
        _logger.info(
            "connect_session.edit_consent_started",
            session_id=row.id,
            credential_id=row.credential_id,
            flow=handler_cls.kind,
            scopes=change["scopes"],
        )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CONFIRM,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            origin=identity.origin.value,
            after={
                "intent": INTENT_EDIT,
                "agent_id": row.agent_id,
                "credential_id": row.credential_id,
                "revision_id": change["revision_id"],
                "confirmed_scopes": change["scopes"],
                "awaiting": "vendor_consent",
            },
        )
        return challenge

    async def _begin_consent(
        self,
        row: ConnectSession,
        consent: ConsentSource,
        scopes: list[str],
        *,
        retryable: bool = True,
    ) -> ConfirmResult:
        """Start the vendor consent for a polling edit session; undo the confirm on failure.

        A vendor-side failure puts the session back to ``created`` so the
        reviewer can retry; nothing on the credential has changed. A session
        moved onto an existing credential cannot go back (its own pending
        credential is gone), so it ends instead (``retryable=False``).
        """
        handler = consent.handler_cls(self._ctx)
        try:
            challenge = await handler.begin(row, app=consent.app, confirmed_scopes=scopes)
        except Exception:
            if not retryable:
                await self._mark_terminal(
                    row.id, "failed", "vendor consent could not start", error_code="consent_failed"
                )
                raise
            async with self._ctx.control_db.transaction() as session:
                await ConnectSessionRepository.transition_state(
                    session,
                    row.id,
                    to_state="created",
                    from_states=("polling",),
                    resolved_flow=EDIT_FLOW,
                    approved_change=None,
                )
            raise
        if challenge.kind == "device_authorization":
            return DeviceAuthorizationConfirmResult(
                user_code=challenge.user_code,
                verification_uri=challenge.verification_uri,
                verification_uri_complete=challenge.verification_uri_complete,
                poll_interval_seconds=challenge.poll_interval_seconds,
            )
        return AuthCodeConfirmResult(authorize_url=challenge.authorize_url)

    async def _require_scope_widening_allowed(
        self, credential: Credential, agent_id: str, identity: Identity
    ) -> None:
        """Widening a credential other agents use is its owner's or an org admin's call."""
        others = await self._other_bound_agent_ids(credential.id, agent_id)
        if not others or ORG_ADMIN in identity.permissions:
            return
        if credential.created_by != identity.sub:
            raise ScopeWideningForbiddenError(credential.id)

    async def _consent_source(self, credential: Credential) -> ConsentSource | None:
        """How ``credential`` is re-consented with more scopes, or ``None`` if it can't be.

        Only OAuth credentials connected through an authorization-code or
        device-authorization flow qualify. The consent runs through the
        client the credential already uses: its shared registration (which
        must be active), else the client stored on it.
        """
        handler_cls = _consent_handler_for(credential)
        if handler_cls is None:
            return None
        key: str | None = None
        entry: VendorAuthConfig | None = None
        app: SessionApp | None = None
        async with self._ctx.control_db.session() as session:
            if credential.oauth_app_registration_id is not None:
                registration = await OAuthAppRegistrationRepository.get_by_id(
                    session, credential.oauth_app_registration_id
                )
                if registration is None or not registration.is_active:
                    return None
                if registration.flow_kind != handler_cls.kind:
                    return None
                app = _session_app_from_registration(self._ctx, registration)
                key = registration.api_vendor
            elif handler_cls is AuthCodeFlowHandler:
                occ = await OAuthClientCredentialRepository.get_by_credential(
                    session, credential.id
                )
                if occ is not None and occ.authorize_url:
                    app = SessionApp(
                        flow_kind="authorization_code",
                        client_id=occ.client_id,
                        client_secret_provider=None,
                        default_scopes=[],
                        registration_id=None,
                        authorize_url=occ.authorize_url,
                        token_url=occ.token_url,
                    )
            else:
                dac = await DeviceAuthorizationCredentialRepository.get_by_credential(
                    session, credential.id
                )
                if dac is not None:
                    app = SessionApp(
                        flow_kind="device_authorization",
                        client_id=dac.client_id,
                        client_secret_provider=None,
                        default_scopes=[],
                        registration_id=None,
                        authorization_endpoint=dac.authorization_endpoint,
                        token_endpoint=dac.token_url,
                    )
        if app is None:
            return None
        if key is None:
            key = next(
                (
                    k
                    for k, cfg in self._ctx.config.vendors.entries.items()
                    if cfg.vendor == credential.catalog_api_id
                ),
                None,
            )
        if key is not None:
            try:
                entry = (
                    await self._vendors.resolve_session_source(
                        key,
                        registration_id=credential.oauth_app_registration_id,
                        flow_kind=handler_cls.kind,
                    )
                ).entry
            except (
                InvalidOAuthAppRegistrationError,
                UnknownVendorError,
                UnsupportedFlowError,
                VendorNotConfiguredError,
            ):
                entry = None
        return ConsentSource(handler_cls=handler_cls, app=app, key=key, entry=entry)

    async def _write_change(
        self, session: Any, row: ConnectSession, change: dict[str, Any]
    ) -> None:
        """Write an approved change's rules, record its outcome and close the session."""
        assert row.agent_id is not None
        if change["rule_set_id"] is not None:
            await PermissionRuleSetRepository.replace_user_rules(
                session, change["rule_set_id"], change["rules"], created_by=change["approved_by"]
            )
        else:
            await AgentPermissionRuleRepository.replace_user_rules(
                session,
                row.agent_id,
                row.credential_id,
                change["rules"],
                created_by=change["approved_by"],
            )
        await ConnectSessionOutcomeRepository.record(
            session,
            row=row,
            outcome=change["outcome"],
            error_code=None,
            ended_at=datetime.now(UTC),
            credential_id=row.credential_id,
        )
        await ConnectSessionRepository.delete(session, row.id)

    async def _apply_admin_change(self, row: ConnectSession, change: dict[str, Any]) -> None:
        """The admin-DB half of an approved change: bind, detach a curated set, resume."""
        assert row.agent_id is not None
        if not (change["bind"] or change["detach_rule_set"] or change["resume"]):
            return
        async with self._ctx.admin_db.transaction() as admin_session:
            if change["bind"]:
                await EffectsRepository.bind_agent_to_credential(
                    admin_session,
                    agent_id=row.agent_id,
                    credential_id=row.credential_id,
                    rule_set_id=None,
                    created_by=change["approved_by"],
                )
            if change["detach_rule_set"]:
                await PrerequisiteRepository.set_binding_rule_set(
                    admin_session,
                    agent_id=row.agent_id,
                    credential_id=row.credential_id,
                    rule_set_id=None,
                )
            if change["resume"]:
                await PrerequisiteRepository.resume_binding(
                    admin_session, agent_id=row.agent_id, credential_id=row.credential_id
                )

    async def _record_change_applied(
        self, row: ConnectSession, change: dict[str, Any], *, origin: str
    ) -> None:
        """Log, audit and emit events for an approved change that has landed.

        ``origin`` is the confirming request's, or ``system`` when the change
        landed with a vendor grant after the confirm.
        """
        assert row.agent_id is not None
        before: list[dict[str, Any]] = []
        if row.baseline is not None:
            _, before = target_rules(row.baseline)
        before_content = [rule_content(r) for r in before]
        after_content = [rule_content(r) for r in change["rules"]]
        _logger.info(
            "connect_session.edit_applied",
            session_id=row.id,
            agent_id=row.agent_id,
            credential_id=row.credential_id,
            revision_id=change["revision_id"],
        )
        approver = change["approved_by"]
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=row.credential_id,
            target_parent_id=row.agent_id,
            actor_type=actor_type_label_from_id(approver),
            actor_id=approver,
            origin=origin,
            reason="connect session approved",
            after={
                "session_id": row.id,
                "revision_id": change["revision_id"],
                "rule_set_id": change["rule_set_id"],
                "rules_added": sum(1 for r in after_content if r not in before_content),
                "rules_removed": sum(1 for r in before_content if r not in after_content),
                "scopes": change["scopes"],
                "resumed": change["resume"],
                "bound": change["bind"],
            },
        )
        await self._emit_edit_applied(row, approver, bound=change["bind"] or change["resume"])

    async def _rebase_edit(self, row: ConnectSession, live: dict[str, Any]) -> str:
        """Re-resolve the edit's latest ask onto the binding as it is now.

        The live binding becomes the baseline and the current revision's
        proposal is resolved against it as a new revision; any rule it named
        that no longer exists is dropped and listed in the proposal's
        ``dropped``. Returns the new current revision id.
        """
        async with self._ctx.control_db.transaction() as session:
            current = await ConnectSessionRevisionRepository.get_by_id(
                session, row.current_revision_id or ""
            )
            if current is None:
                raise SessionNotFoundError(row.id)
            request = EditRequest.from_proposal(current.proposal)
            result, dropped = resolve_edit(live, request, lenient=True)
            proposal = {**request.as_proposal(), "rebased_from": current.number, "dropped": dropped}
            revision = await ConnectSessionRevisionRepository.append(
                session,
                session_id=row.id,
                proposal=proposal,
                result=result,
                digest=revision_digest(baseline=live, result=result, reason=request.reason),
                created_by=None,
            )
            await ConnectSessionRepository.update_fields(session, row.id, baseline=live)
        _logger.info(
            "connect_session.edit_rebased",
            session_id=row.id,
            revision_id=revision.id,
            dropped=len(dropped),
        )
        return revision.id

    async def _emit_edit_applied(self, row: ConnectSession, actor_id: str, *, bound: bool) -> None:
        """``credential.permission_rule_set`` (and ``credential.bound_to_agent`` when bound)."""
        assert row.agent_id is not None
        events = [EventType.CREDENTIAL_PERMISSION_RULE_SET]
        if bound:
            events.append(EventType.CREDENTIAL_BOUND_TO_AGENT)
        for event_type in events:
            try:
                async with self._ctx.admin_db.transaction() as session:
                    await emit_event_best_effort(
                        session,
                        type=event_type,
                        severity=EventSeverity.INFO,
                        summary=(
                            f"Access of agent {row.agent_id} to credential {row.credential_id} "
                            "changed by an approved connect request"
                        ),
                        data={
                            "agent_id": row.agent_id,
                            "credential_id": row.credential_id,
                            "session_id": row.id,
                        },
                        created_by=row.agent_id,
                        actor_id=actor_id,
                        actor_type=actor_type_label_from_id(actor_id),
                    )
            except Exception:
                _logger.warning("telemetry_emit_failed", event_type=event_type, exc_info=True)

    async def _require_editable_binding(
        self, credential_id: str, agent_id: str, identity: Identity
    ) -> AgentCredentialBindingRow:
        """The binding an edit opens on, refusing callers who may not edit it.

        An agent may edit only its own active binding. A human must be the
        agent's approver. Every refusal is the same not-found error.
        """
        if identity.actor_type == ActorType.AGENT:
            if agent_id != identity.sub:
                raise EditBindingNotFoundError(credential_id)
        elif not await self._is_approver_for_agent(agent_id, identity):
            raise EditBindingNotFoundError(credential_id)
        async with self._ctx.admin_db.session() as admin_session:
            binding = await PrerequisiteRepository.get_agent_credential_binding(
                admin_session, agent_id=agent_id, credential_id=credential_id
            )
        if binding is None or (binding.suspended and identity.actor_type == ActorType.AGENT):
            raise EditBindingNotFoundError(credential_id)
        return binding

    async def _binding_snapshot(
        self, agent_id: str, credential_id: str, rule_set_id: str | None
    ) -> dict[str, Any]:
        """The binding's rules, rule set and granted scopes (``suspended`` is the caller's).

        System rules are left out: an edit never touches them.
        """
        async with self._ctx.control_db.session() as session:
            rules = await AgentPermissionRuleRepository.list_rules(session, agent_id, credential_id)
            rule_set: dict[str, Any] | None = None
            if rule_set_id is not None:
                found = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
                if found is not None:
                    set_rules = await PermissionRuleSetRepository.list_rules(session, rule_set_id)
                    rule_set = {
                        "id": found.id,
                        "name": found.name,
                        "curated": found.curated,
                        "created_by": found.created_by,
                        "rules": [_rule_snapshot(r) for r in set_rules if not r.is_system],
                    }
            token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        return {
            "rules": [_rule_snapshot(r) for r in rules if not r.is_system],
            "rule_set": rule_set,
            "suspended": False,
            "granted_scopes": token.scope.split() if token is not None and token.scope else None,
        }

    async def _live_binding_snapshot(self, row: ConnectSession) -> dict[str, Any] | None:
        """The edit session's binding as it is now; ``None`` when it is gone."""
        assert row.agent_id is not None
        async with self._ctx.admin_db.session() as admin_session:
            binding = await PrerequisiteRepository.get_agent_credential_binding(
                admin_session, agent_id=row.agent_id, credential_id=row.credential_id
            )
        if binding is None:
            return None
        snapshot = await self._binding_snapshot(
            row.agent_id, row.credential_id, binding.rule_set_id
        )
        snapshot["suspended"] = binding.suspended
        return snapshot

    async def _edit_review(self, row: ConnectSession) -> EditReview:
        """The review block of an edit session: baseline, current revision and reach."""
        assert row.agent_id is not None and row.baseline is not None
        async with self._ctx.control_db.session() as session:
            revision = (
                await ConnectSessionRevisionRepository.get_by_id(session, row.current_revision_id)
                if row.current_revision_id is not None
                else None
            )
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
        if revision is None:
            raise SessionNotFoundError(row.id)
        others = await self._other_bound_agents(row.credential_id, row.agent_id)
        return EditReview(
            agent_id=row.agent_id,
            credential_id=row.credential_id,
            credential_name=credential.name if credential is not None else None,
            revision_id=revision.id,
            revision_number=revision.number,
            current=dict(row.baseline),
            result=dict(revision.result),
            widens_access=widens_access(row.baseline, revision.result),
            other_bound_agents=others,
            needs_vendor_consent=bool(added_scopes(row.baseline, revision.result)),
        )

    async def _edit_review_data(self, row: ConnectSession, identity: Identity) -> ReviewData:
        """``get_review_data`` for an edit session."""
        edit = await self._edit_review(row)
        agent = await self._agent_view(row.agent_id)
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
            revision = await ConnectSessionRevisionRepository.get_by_id(session, edit.revision_id)
        assert revision is not None
        return ReviewData(
            session_id=row.id,
            state=row.state,
            vendor_key=row.vendor,
            vendor_display_name=(credential.name if credential is not None else None) or row.vendor,
            resolved_flow=row.resolved_flow,
            reason=row.reason,
            requested_by_actor_id=row.initiator_actor_id,
            scopes=[],
            requested_permission_rules=[],
            api_vendor=(credential.api_vendor or "") if credential is not None else "",
            api_name=credential.api_name if credential is not None else None,
            api_version=credential.api_version if credential is not None else None,
            agent=agent,
            digest=revision.digest,
            can_confirm=await self._can_confirm(row, identity, agent),
            revision_id=revision.id,
            revision_number=revision.number,
            edit=edit,
        )

    async def _emit_edit_requested(self, row: ConnectSession, *, credential_name: str) -> None:
        """Emit the ``connect_session.edit_requested`` rail event for an agent's edit.

        Like ``connect_session.created``: only when the agent itself asked,
        ``created_by`` is the agent so its owner sees the row, and the data
        never carries the poll token.
        """
        if row.agent_id is None or row.initiator_actor_id != row.agent_id:
            return
        try:
            async with self._ctx.admin_db.session() as admin_session:
                agent = await EffectsRepository.get_agent_owner(admin_session, row.agent_id)
            agent_name = agent.name if agent is not None else None
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CONNECT_SESSION_EDIT_REQUESTED,
                    severity=EventSeverity.INFO,
                    summary=(
                        f"Agent {summary_label(agent_name, row.agent_id)} asked to change its "
                        f"access to {summary_label(credential_name, row.credential_id)}"
                    ),
                    requires_action=False,
                    data={
                        "session_id": row.id,
                        "agent_id": row.agent_id,
                        "credential_id": row.credential_id,
                    },
                    created_by=row.agent_id,
                    actor_id=row.initiator_actor_id,
                    actor_type=ActorType.AGENT.value,
                )
        except Exception:
            _logger.warning(
                "telemetry_emit_failed",
                event_type=EventType.CONNECT_SESSION_EDIT_REQUESTED,
                session_id=row.id,
                exc_info=True,
            )

    # ---- review data ------------------------------------------------------

    async def get_review_data(
        self, session_id: str, *, poll_token: str | None, identity: Identity
    ) -> ReviewData:
        """Return everything the review page needs to render.

        Gated by :meth:`_require_session_access` — the session's
        ``poll_token``, or a human who is the target agent's owner or
        ``org:admin``. Session ids travel in approval URLs, so they are not
        secrets, and the review payload (vendor, scopes, requested rules,
        initiator) must not be readable by any actor that merely holds
        ``credentials:write``. Missing session, token mismatch and a caller
        who is neither surface identically (no session-id enumeration
        oracle).

        The scope list is the union of the source's catalog with the
        initiator's as-requested list — flagged so the UI can highlight
        write scopes the agent asked for. ``requested_scopes`` lives on the
        session row itself (flow-agnostic), so this method never needs to
        reach into a flow-specific aux table.

        An ``awaiting_app`` session first tries to resolve its OAuth app
        (a shared app or config entry registered since); the digest is
        computed after any move. Every review carries the agent and its
        owner, the scheme and pinned hosts of an API target, a ``digest``
        the confirm must echo, ``can_confirm`` for this viewer, and the
        viewer's existing credentials for the target.
        """
        async with self._ctx.control_db.session() as session:
            row = await ConnectSessionRepository.get_by_id(session, session_id)
        row = await self._require_session_access(row, poll_token=poll_token, identity=identity)
        if row.intent == INTENT_EDIT:
            return await self._edit_review_data(row, identity)
        if row.state == AWAITING_APP:
            row = await self._try_leave_awaiting_app(row)
        async with self._ctx.control_db.session() as session:
            # Pull the credential's api coords so the SPA can call
            # ``/apis/{vendor}/{name}/{version}/operations`` for the
            # rules-page preview. ``api_version`` is nullable — the
            # catalog import populates it asynchronously.
            credential = await CredentialRepository.get_by_id(session, row.credential_id)

        view: ApiSecurityView | None = None
        if row.target_kind == TARGET_KIND_API:
            assert row.api_name is not None and row.api_version is not None
            view = await self._lookup_api(row.vendor, row.api_name, row.api_version)

        key = _source_key(row)
        if key is not None:
            # The credential row records which admin-registered app minted it
            # (NULL = platform config), so the review page's scope catalog
            # comes from the session's own source, never a registration added
            # since.
            pinned_registration_id = (
                credential.oauth_app_registration_id if credential is not None else None
            )
            source = await self._reopen_session_source(row, pinned_registration_id)
            entry = source.entry
            scopes = [
                _scope_view(s) for s in self._vendors.merge_scopes(entry, row.requested_scopes)
            ]
            display_name = entry.display_name
        elif row.target_kind == TARGET_KIND_API:
            requested = set(row.requested_scopes or [])
            spec_scopes = oauth_scopes_of(view) if view is not None else []
            descriptions = oauth_scope_descriptions_of(view) if view is not None else {}
            scopes = [
                ScopeView(
                    name=name,
                    # The spec does not classify scopes: the name decides, and
                    # an unrecognised one reads as a write the approver reviews.
                    classification=classify_spec_scope(name),
                    default=False,
                    requested=name in requested,
                    description=descriptions.get(name, ""),
                )
                for name in [*spec_scopes, *sorted(requested - set(spec_scopes))]
            ]
            display_name = (view.display_name if view else None) or f"{row.vendor}/{row.api_name}"
        else:
            raise UnsupportedTargetKindError(row.id, row.target_kind, "build review data")

        if row.target_kind == TARGET_KIND_API:
            api_vendor, api_name, api_version = row.vendor, row.api_name, row.api_version
        else:
            # The credential row's ``api_version`` is set at create-time to
            # ``None`` — the imported OpenAPI decides its own version once the
            # catalog import completes. Look it up live from the registry via
            # the catalog-import DI seam so the SPA's rules-preview knows what
            # version to hit ``/apis/.../operations`` against. Returns ``None``
            # until the import lands; SPA polls the review-session endpoint
            # while ``api_version`` is None.
            api_vendor = credential.api_vendor if credential else ""
            api_name = credential.api_name if credential else None
            api_version = None
            if self._catalog_auto_importer is not None:
                api_version = await self._catalog_auto_importer.current_version(api_id=entry.vendor)

        agent = await self._agent_view(row.agent_id)
        endpoints = catalog_oauth_endpoints(view) if row.scheme_type == SCHEME_OAUTH2 else None
        revision = await self._current_revision(row)
        return ReviewData(
            session_id=row.id,
            state=row.state,
            vendor_key=key or row.vendor,
            vendor_display_name=display_name,
            resolved_flow=row.resolved_flow,
            reason=row.reason,
            requested_by_actor_id=row.initiator_actor_id,
            scopes=scopes,
            requested_permission_rules=row.requested_permission_rules or [],
            api_vendor=api_vendor,
            api_name=api_name,
            api_version=api_version,
            target_kind=row.target_kind,
            requested_scopes=list(row.requested_scopes or []),
            provenance=_provenance_view(view),
            agent=agent,
            scheme=(
                SchemeView(
                    type=row.scheme_type,
                    location=row.scheme_location,
                    field_name=row.scheme_field_name,
                    authorization_url=endpoints[0] if endpoints else None,
                    token_url=endpoints[1] if endpoints else None,
                )
                if row.scheme_type is not None
                else None
            ),
            pinned_hosts=list(row.pinned_hosts) if row.pinned_hosts is not None else None,
            digest=_review_digest(row, agent, view),
            can_confirm=await self._can_confirm(row, identity, agent),
            existing_credentials=await self._existing_candidates(row, identity, credential),
            revision_id=revision.id if revision is not None else None,
            revision_number=revision.number if revision is not None else None,
        )

    async def _current_revision(self, row: ConnectSession) -> ConnectSessionRevision | None:
        if row.current_revision_id is None:
            return None
        async with self._ctx.control_db.session() as session:
            return await ConnectSessionRevisionRepository.get_by_id(
                session, row.current_revision_id
            )

    async def edit_binding_of(self, session_id: str) -> tuple[str, str | None] | None:
        """The ``(credential_id, agent_id)`` an edit session changes; ``None`` otherwise."""
        async with self._ctx.control_db.session() as session:
            row = await ConnectSessionRepository.get_by_id(session, session_id)
        if row is None or row.intent != INTENT_EDIT:
            return None
        return row.credential_id, row.agent_id

    async def list_revisions(
        self, session_id: str, *, poll_token: str | None, identity: Identity
    ) -> list[ConnectSessionRevision]:
        """Every revision of a session, oldest first; gated like the review read."""
        async with self._ctx.control_db.session() as session:
            row = await ConnectSessionRepository.get_by_id(session, session_id)
        row = await self._require_session_access(row, poll_token=poll_token, identity=identity)
        async with self._ctx.control_db.session() as session:
            return await ConnectSessionRevisionRepository.list_for_session(session, row.id)

    async def _agent_view(self, agent_id: str | None) -> AgentView | None:
        if agent_id is None:
            return None
        async with self._ctx.admin_db.session() as admin_session:
            agent = await EffectsRepository.get_agent_owner(admin_session, agent_id)
        if agent is None:
            return None
        return AgentView(
            agent_id=agent_id, name=agent.name, owner_id=agent.owner_id, status=agent.status
        )

    async def _can_confirm(
        self, row: ConnectSession, identity: Identity, agent: AgentView | None
    ) -> bool:
        """Whether ``identity`` may confirm the session as it stands (no side effects)."""
        if row.state not in ("created", AWAITING_APP):
            return False
        if identity.actor_type == ActorType.AGENT:
            return False
        if row.agent_id is None:
            return True
        if agent is None or agent.status in _UNUSABLE_AGENT_STATUSES:
            return False
        if _is_org_admin(identity):
            return True
        return (
            agent.owner_id is not None
            and agent.owner_id == identity.sub
            and _is_owner_approver(identity)
        )

    async def _existing_candidates(
        self, row: ConnectSession, identity: Identity, pending: Credential | None
    ) -> list[ExistingCredentialView]:
        """The viewer's connected credentials covering the session's target.

        For OAuth credentials the granted scopes (``oauth_tokens.scope``) are
        compared with the session's requested scopes: a narrower grant cannot
        be bound, and re-authorizing it is offered only when no other agent
        is bound to it (widening it would widen theirs too).
        """
        if identity.actor_type != ActorType.USER or not identity.sub or pending is None:
            return []
        if row.target_kind == TARGET_KIND_API:
            name, version = row.api_name, row.api_version
        else:
            name, version = pending.api_name, pending.api_version
        filters = build_access_filters(identity, Credential)
        async with self._ctx.control_db.session() as session:
            candidates = await CredentialRepository.list_connected_covering(
                session,
                vendor=pending.api_vendor,
                name=name,
                version=version,
                exclude_ids=[row.credential_id],
                filters=filters,
            )
            tokens = {
                c.id: await OAuthTokenRepository.get_by_credential(session, c.id)
                for c in candidates
                if _is_oauth_type(c.type)
            }
        requested = list(row.requested_scopes or [])
        views: list[ExistingCredentialView] = []
        for credential in candidates:
            others = await self._other_bound_agents(credential.id, row.agent_id)
            wire_type = to_wire(StoredCredentialType(credential.type)).value
            if not _is_oauth_type(credential.type):
                views.append(
                    ExistingCredentialView(
                        credential_id=credential.id,
                        name=credential.name,
                        type=wire_type,
                        granted_scopes=None,
                        missing_scopes=None,
                        other_bound_agents=others,
                        can_bind=True,
                        needs_vendor_consent=False,
                    )
                )
                continue
            token = tokens.get(credential.id)
            granted = token.scope.split() if token is not None and token.scope else []
            missing = missing_scopes(requested, granted)
            consentable = bool(missing) and _consent_handler_for(credential) is not None
            may_widen = (
                not others
                or ORG_ADMIN in identity.permissions
                or credential.created_by == identity.sub
            )
            views.append(
                ExistingCredentialView(
                    credential_id=credential.id,
                    name=credential.name,
                    type=wire_type,
                    granted_scopes=granted,
                    missing_scopes=missing,
                    other_bound_agents=others,
                    can_bind=not missing or (consentable and may_widen),
                    needs_vendor_consent=consentable,
                )
            )
        return views

    async def _other_bound_agents(
        self, credential_id: str, agent_id: str | None
    ) -> list[AgentView]:
        """The agents other than ``agent_id`` bound to the credential, with their names."""
        views: list[AgentView] = []
        for other_id in await self._other_bound_agent_ids(credential_id, agent_id):
            view = await self._agent_view(other_id)
            views.append(view or AgentView(agent_id=other_id, name=None, owner_id=None, status=""))
        return views

    async def _other_bound_agent_ids(self, credential_id: str, agent_id: str | None) -> list[str]:
        async with self._ctx.admin_db.session() as admin_session:
            rows = await PrerequisiteRepository.list_agents_for_credential(
                admin_session, credential_id=credential_id, limit=_BOUND_AGENTS_LIMIT
            )
        return sorted({r.agent_id for r in rows if r.agent_id != agent_id})

    async def _try_leave_awaiting_app(self, row: ConnectSession) -> ConnectSession:
        """:meth:`_leave_awaiting_app`, returning the session as it stands afterwards."""
        await self._leave_awaiting_app(row)
        async with self._ctx.control_db.session() as session:
            current = await ConnectSessionRepository.get_by_id(session, row.id)
        return current if current is not None else row

    async def _leave_awaiting_app(self, row: ConnectSession) -> bool:
        """Move an ``awaiting_app`` session to ``created`` once an OAuth app resolves for it.

        CAS-first: the transition and the credential/aux writes share one
        transaction, and a session another path already moved (the
        approver's own client, a concurrent re-resolution, a cancel) is left
        alone. Returns whether this call moved it.
        """
        if row.state != AWAITING_APP or row.api_name is None or row.api_version is None:
            return False
        try:
            resolved = await self._vendors.resolve_api_source(
                _api_covers(row.vendor, row.api_name, row.api_version),
                preferred_flow=row.preferred_flow,
            )
        except (
            AmbiguousVendorError,
            InvalidOAuthAppRegistrationError,
            UnknownVendorError,
            UnsupportedFlowError,
            VendorNotConfiguredError,
        ) as exc:
            _logger.info(
                "connect_session.awaiting_app_unresolved", session_id=row.id, detail=str(exc)
            )
            return False
        if resolved is None:
            return False
        key, source = resolved
        try:
            handler_cls = handler_for(source.flow.kind)
        except KeyError:
            return False
        handler = handler_cls(self._ctx)
        session_app = self._session_app_for(source)
        async with self._ctx.control_db.transaction() as session:
            won = await ConnectSessionRepository.transition_state(
                session,
                row.id,
                to_state="created",
                from_states=(AWAITING_APP,),
                vendor_key=key,
                resolved_flow=source.flow.kind,
            )
            if won:
                await CredentialRepository.set_type_and_provider(
                    session,
                    row.credential_id,
                    type=handler.stored_type.value,
                    provider=handler.provider_id,
                    catalog_api_id=source.entry.vendor,
                )
                await handler.prepare(
                    session,
                    credential_id=row.credential_id,
                    app=session_app,
                    requested_scopes=list(row.requested_scopes or []),
                    created_by=row.initiator_actor_id,
                )
        if won:
            _logger.info(
                "connect_session.app_resolved",
                session_id=row.id,
                vendor_key=key,
                resolved_flow=source.flow.kind,
            )
        return won

    async def resolve_awaiting_app_sessions(self, *, limit: int = 100) -> int:
        """Try to resolve every ``awaiting_app`` session (sweep tick, new shared app).

        No-op while ``manual_flows_enabled`` is off. Returns how many moved.
        """
        if not self._manual_flows_enabled:
            return 0
        async with self._ctx.control_db.session() as session:
            rows = await ConnectSessionRepository.list_awaiting_app(session, limit=limit)
        moved = 0
        for row in rows:
            # Best-effort per session: one session that cannot move must not
            # stop the others (or fail the caller, e.g. a registration create).
            try:
                if await self._leave_awaiting_app(row):
                    moved += 1
            except Exception:
                _logger.warning(
                    "connect_session.awaiting_app_resolve_failed", session_id=row.id, exc_info=True
                )
        return moved

    # ---- list ---------------------------------------------------------------

    async def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        state: str | None = None,
        vendor: str | None = None,
        identity: Identity,
    ) -> SessionPage:
        """List connect sessions with cursor pagination, scoped to the caller.

        Visibility follows the credential axis (``build_access_filters``):
        plain callers see sessions they initiated, ``org:admin`` sees all,
        a delegated agent holding ``owner:credentials:read`` also sees its
        owner's sessions, and a human also sees the sessions of agents they
        own. Rows are slim summaries — the ``poll_token``
        capability never leaves the service on this path.
        """
        decoded_cursor = None
        if cursor is not None:
            ts, sid = decode_cursor_str(cursor)
            decoded_cursor = (ts, sid)

        access_filters = build_access_filters(
            identity,
            ConnectSession,
            owned_agent_ids=await self._owned_agent_ids(identity),
        )

        async with self._ctx.control_db.session() as session:
            rows = await ConnectSessionRepository.list_all(
                session,
                cursor=decoded_cursor,
                limit=limit,
                state=state,
                vendor=vendor,
                filters=access_filters,
            )

            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]

            registration_names = await CredentialRepository.get_registration_display_names(
                session, [r.credential_id for r in rows]
            )
            data = [self._to_summary(r, registration_names.get(r.credential_id)) for r in rows]
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)

        return SessionPage(data=data, has_more=has_more, next_cursor=next_cursor)

    def _to_summary(self, row: ConnectSession, registration_name: str | None) -> SessionSummary:
        return SessionSummary(
            session_id=row.id,
            state=row.state,
            vendor_key=row.vendor,
            vendor_display_name=self._vendor_display_name(row, registration_name),
            agent_id=row.agent_id,
            requested_by_actor_id=row.initiator_actor_id,
            reason=row.reason,
            connected_as=row.connected_as,
            error_code=row.error_code,
            created_at=row.created_at,
            credential_id=row.credential_id,
            intent=row.intent,
        )

    def _vendor_display_name(self, row: ConnectSession, registration_name: str | None) -> str:
        """Display name of the app a session ran through.

        ``registration_name`` comes off the session credential's
        ``oauth_app_registration_id`` — the pin every other session read
        uses — so a row never shows another registration's name. Without
        one (a config session) it's the config entry's name, else the raw
        key: the entry may have been removed since, and the list must not
        500 on such rows. An ``api`` target is never looked up as a config
        key; it shows its API identity.
        """
        if row.target_kind != TARGET_KIND_VENDOR:
            return f"{row.vendor}/{row.api_name}" if row.api_name else row.vendor
        if registration_name is not None:
            return registration_name
        cfg = self._ctx.config.vendors.entries.get(row.vendor)
        return cfg.display_name if cfg is not None else row.vendor

    # ---- confirm ----------------------------------------------------------

    async def confirm(
        self,
        session_id: str,
        *,
        poll_token: str | None,
        confirmed_scopes: list[str],
        # Rules arrive already-shaped as ``AgentPermissionRule`` dicts
        # (``{effect, methods, path, match_mode, operations, comment}``) —
        # the router validates against ``PermissionRuleSchema`` before we
        # ever see them, so no shape translation happens here.
        permission_rules: list[dict[str, object]],
        # Agent to bind the credential to, when the session was opened
        # without a target (user clicks a vendor tile before picking an
        # agent). Ignored when the session already carries an agent_id
        # — a user cannot silently re-target an existing session.
        agent_id: str | None = None,
        identity: Identity,
        # What the approver reviewed (``GET /connect-sessions/{id}``). Checked
        # whenever given; required for an ``api`` target.
        expected_agent_id: str | None = None,
        digest: str | None = None,
        # The revision the approver reviewed; required, and it must be current.
        revision_id: str | None = None,
    ) -> ConfirmResult:
        """Confirm scopes + permissions and kick off the vendor-side flow.

        Gated like ``get_review_data`` (``poll_token``, or the target
        agent's owner / ``org:admin``) — otherwise any actor holding
        ``credentials:write`` could confirm any session (ids travel in
        approval URLs) and bind an arbitrary agent. Missing session, token
        mismatch and a caller who is neither surface identically.

        The credential is attributed to the approver (``created_by``),
        whoever initiated the session; the initiator stays recorded on the
        session and in the audit entry.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
            # ``credentials.oauth_app_registration_id`` records the pinned
            # registration from ``:connect``; every vendor read at confirm
            # time must key off it so scopes / client material stay aligned
            # with what the picker showed and ``:connect`` used.
            credential = (
                await CredentialRepository.get_by_id(read_session, row.credential_id)
                if row is not None
                else None
            )
        row = await self._require_session_access(row, poll_token=poll_token, identity=identity)

        pinned_registration_id = (
            credential.oauth_app_registration_id if credential is not None else None
        )

        _forbid_agent_confirm(identity.actor_type)
        if row.state in ("created", AWAITING_APP):
            _require_confirm_kind(row, CONFIRM_KIND_OAUTH)
        # Friendly pre-check for the common stale-page case; the CAS below
        # is the authoritative guard against a concurrent confirm.
        _require_state(row, expected="created", action="confirm")
        _require_revision(row, revision_id)

        # Re-open the session's own source at confirm time: the pinned
        # registration, or the config entry when the FK is NULL. If that
        # source is no longer usable — registration missing / inactive, or
        # the config entry / flow removed — cancel: the credential's aux rows
        # were written for that app, so the caller must start a new session.
        source = await self._reopen_session_source(row, pinned_registration_id)
        flow = source.flow
        session_app = self._session_app_for(source)

        try:
            handler_cls = handler_for(flow.kind)
        except KeyError as exc:
            raise NoOpForFlowError(flow.kind) from exc
        handler = handler_cls(self._ctx)

        unknown = self._vendors.validate_scopes(source.entry, confirmed_scopes)
        if unknown:
            raise ScopeValidationError(unknown)

        # Late-bind agent_id: user-initiated sessions are opened without
        # a target and pick one on the rules-page Continue-click. The
        # write rides the CAS below, so the first winner sticks
        # atomically. Downstream reads use ``effective_agent_id`` rather
        # than ``row.agent_id`` so the binder + rules-write see the
        # freshly-persisted value without another read round-trip.
        late_bound = row.agent_id is None and agent_id is not None
        effective_agent_id = row.agent_id if row.agent_id is not None else agent_id

        # The agent named on the session (agent-initiated) or in the
        # payload (late-bind) has never been validated — it must exist
        # and be governable by the confirming caller before any binding
        # or rule write happens in its name.
        if effective_agent_id is not None:
            await self._require_agent_binding_allowed(
                effective_agent_id, identity, late_bound=late_bound
            )
        api_target = row.target_kind == TARGET_KIND_API
        await self._verify_review(
            row,
            effective_agent_id=effective_agent_id,
            expected_agent_id=expected_agent_id,
            digest=digest,
            permission_rules=permission_rules,
            require_digest=api_target,
            require_rules=api_target,
        )

        # CAS ``created`` → ``polling`` BEFORE the vendor call: without
        # it, two concurrent confirms both pass the stale read above and
        # both fire the vendor's ``begin`` (TOCTOU). The loser sees
        # rowcount 0 and gets the same 409 as the stale-page case.
        cas_fields: dict[str, Any] = {"agent_id": effective_agent_id} if late_bound else {}
        async with self._ctx.control_db.transaction() as cas_session:
            won = await ConnectSessionRepository.transition_state(
                cas_session,
                row.id,
                to_state="polling",
                from_states=("created",),
                revision_id=revision_id,
                **cas_fields,
            )
        if not won:
            async with self._ctx.control_db.session() as recheck_session:
                current = await ConnectSessionRepository.get_by_id(recheck_session, row.id)
            raise InvalidStateTransitionError(
                row.id, current.state if current else "deleted", "confirm"
            )

        # The handler owns the vendor conversation + any flow-specific
        # transient-state write (device_code + expires_at for RFC 8628; the
        # signed state token for auth-code). We only own the flow-agnostic
        # state machine + permission-rule capture below.
        try:
            challenge = await handler.begin(row, app=session_app, confirmed_scopes=confirmed_scopes)
        except Exception:
            # A vendor-side ``begin`` failure must leave the session
            # retryable — roll the CAS back to ``created`` (undoing a
            # late-bound agent too, so a retry can pick a different one).
            revert_fields: dict[str, Any] = {"agent_id": None} if late_bound else {}
            async with self._ctx.control_db.transaction() as revert_session:
                await ConnectSessionRepository.transition_state(
                    revert_session,
                    row.id,
                    to_state="created",
                    from_states=("polling",),
                    **revert_fields,
                )
            raise

        async with self._ctx.control_db.transaction() as session:
            await CredentialRepository.set_created_by(
                session, row.credential_id, created_by=identity.sub
            )
            # Persist the approved rules as direct agent-credential binding
            # rules (theme 5): ``agent_permission_rules`` is the list the
            # broker enforces for the ``(agent, credential)`` pair. Skipped
            # when no agent is named — a user connecting without an agent
            # leaves binding + rules to a later explicit bind.
            if effective_agent_id is not None:
                await AgentPermissionRuleRepository.replace_user_rules(
                    session,
                    effective_agent_id,
                    row.credential_id,
                    permission_rules,
                    created_by=identity.sub,
                )
            elif permission_rules:
                # Operators reading this log line can spot approvals whose
                # rules had nothing to bind against — the session names no
                # agent, so the rules are dropped, not silently applied.
                _logger.info(
                    "connect_session.permission_rules_dropped",
                    session_id=row.id,
                    vendor=row.vendor,
                    rules_count=len(permission_rules),
                    reason="no agent_id on session",
                )

        if effective_agent_id is not None:
            # Create the admin-DB binding row after the control commit
            # (intent-then-apply — the rules above are the committed intent;
            # the cross-DB binding is applied idempotently, so a re-connect
            # over an existing binding leaves the operator-owned row
            # untouched). ``_mark_terminal`` sweeps it if the flow dies.
            async with self._ctx.admin_db.transaction() as admin_session:
                await EffectsRepository.bind_agent_to_credential(
                    admin_session,
                    agent_id=effective_agent_id,
                    credential_id=row.credential_id,
                    rule_set_id=None,
                    created_by=identity.sub,
                )

        _logger.info(
            "connect_session.confirmed",
            session_id=row.id,
            vendor=row.vendor,
            resolved_flow=row.resolved_flow,
            confirmed_scopes=confirmed_scopes,
            rules_count=len(permission_rules),
        )
        # Confirm produces the material change: the vendor conversation
        # has begun, scopes and permission rules are committed, and an
        # agent binding may have been created. The audit entry pins
        # which caller approved which set — the log line above is
        # observability, not attribution.
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CONFIRM,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            origin=identity.origin.value,
            after={
                "vendor": row.vendor,
                "resolved_flow": row.resolved_flow,
                "credential_id": row.credential_id,
                "agent_id": effective_agent_id,
                "confirmed_scopes": list(confirmed_scopes),
                "rules_count": len(permission_rules),
                "credential_created_by": identity.sub,
            },
        )
        if challenge.kind == "device_authorization":
            return DeviceAuthorizationConfirmResult(
                user_code=challenge.user_code,
                verification_uri=challenge.verification_uri,
                verification_uri_complete=challenge.verification_uri_complete,
                poll_interval_seconds=challenge.poll_interval_seconds,
            )
        return AuthCodeConfirmResult(authorize_url=challenge.authorize_url)

    async def confirm_variant(
        self,
        session_id: str,
        *,
        poll_token: str | None,
        variant: ConfirmVariant,
        identity: Identity,
    ) -> ConfirmResult:
        """Confirm with an entered secret, an own OAuth client or an existing credential.

        Gated like :meth:`confirm`. Every variant re-checks the API target's
        live scheme and hosts (a change ends the session ``failed``),
        requires the reviewed ``expected_agent_id`` and ``digest``, and at
        least one permission rule when it binds an agent.

        * :class:`SecretConfirm` (``manual_*``) binds first — an idempotent
          admin-DB write the broker ignores until the credential is
          ``connected`` — then, in one control transaction, moves the
          session to ``connected`` and writes the typed secret, the
          credential's state and the rules.
        * :class:`ExistingCredentialConfirm` binds a credential the approver
          already holds and ends the session ``connected``. When its OAuth
          grant lacks scopes the session asks for, it runs a vendor consent
          for them first and the bind, rules and scopes land with the grant;
          widening a credential other agents use needs its owner or an org
          admin.
        * :class:`OwnClientConfirm` (``awaiting_app``) stores the approver's
          OAuth client on the pending credential and continues as an
          authorization-code connect.

        When the binding was written but the session's transition lost to a
        cancel or expiry, the binding is removed again; when it lost to a
        concurrent confirm that connected the same credential, it is kept.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        row = await self._require_session_access(row, poll_token=poll_token, identity=identity)
        _forbid_agent_confirm(identity.actor_type)
        if row.state not in ("created", AWAITING_APP):
            raise InvalidStateTransitionError(row.id, row.state, "confirm")

        kind = _variant_kind(variant)
        _require_confirm_kind(row, kind)
        checks = variant.checks
        _require_revision(row, checks.revision_id)
        late_bound = row.agent_id is None and checks.agent_id is not None
        effective_agent_id = row.agent_id if row.agent_id is not None else checks.agent_id
        if isinstance(variant, ExistingCredentialConfirm) and effective_agent_id is None:
            # Binding an existing credential needs an agent to bind it to.
            raise ConfirmKindMismatchError(kind, [_allowed_confirm_kinds(row)[0]])
        if effective_agent_id is not None:
            await self._require_agent_binding_allowed(
                effective_agent_id, identity, late_bound=late_bound
            )
        await self._verify_review(
            row,
            effective_agent_id=effective_agent_id,
            expected_agent_id=checks.expected_agent_id,
            digest=checks.digest,
            permission_rules=checks.permission_rules,
            require_digest=True,
            require_rules=True,
        )

        if isinstance(variant, SecretConfirm):
            result: ConfirmResult = await self._confirm_secret(
                row, variant, effective_agent_id, late_bound=late_bound, identity=identity
            )
        elif isinstance(variant, ExistingCredentialConfirm):
            assert effective_agent_id is not None
            result = await self._confirm_existing(
                row, variant, effective_agent_id, late_bound=late_bound, identity=identity
            )
        else:
            result = await self._confirm_own_client(
                row, variant, effective_agent_id, late_bound=late_bound, identity=identity
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CONFIRM,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            origin=identity.origin.value,
            # Type and target only — never the entered secret or client secret.
            after={
                "target_kind": row.target_kind,
                "vendor": row.vendor,
                "api_name": row.api_name,
                "api_version": row.api_version,
                "resolved_flow": row.resolved_flow,
                "confirm_kind": kind,
                "credential_id": getattr(result, "credential_id", row.credential_id),
                "agent_id": effective_agent_id,
                "rules_count": len(checks.permission_rules),
            },
        )
        return result

    async def _verify_review(
        self,
        row: ConnectSession,
        *,
        effective_agent_id: str | None,
        expected_agent_id: str | None,
        digest: str | None,
        permission_rules: list[dict[str, object]],
        require_digest: bool,
        require_rules: bool,
    ) -> ApiSecurityView | None:
        """Re-check an API target's live revision, then the review the approver saw.

        A scheme or host change ends the session ``failed`` with
        ``scheme_changed`` / ``servers_changed``: what the approver would
        confirm is no longer what the API declares, so the agent must start
        a new session. A digest or agent mismatch (or a missing digest when
        one is required) is ``review_stale`` and leaves the session open.
        """
        view: ApiSecurityView | None = None
        if row.target_kind == TARGET_KIND_API:
            assert row.api_name is not None and row.api_version is not None
            view = await self._lookup_api(row.vendor, row.api_name, row.api_version)
            if view is None or not scheme_still_declared(
                view,
                kind=row.scheme_type,
                location=row.scheme_location,
                field_name=row.scheme_field_name,
            ):
                await self._mark_terminal(
                    row.id, "failed", "declared scheme changed", error_code="scheme_changed"
                )
                raise SchemeChangedError(row.id)
            if set(view.hosts) != set(row.pinned_hosts or []) or view.unpinned_host_variables:
                await self._mark_terminal(
                    row.id, "failed", "server hosts changed", error_code="servers_changed"
                )
                raise ServersChangedError(row.id)
        if require_digest or digest is not None or expected_agent_id is not None:
            if require_digest and expected_agent_id != row.agent_id:
                raise ReviewStaleError(row.id)
            if expected_agent_id is not None and expected_agent_id != row.agent_id:
                raise ReviewStaleError(row.id)
            if require_digest or digest is not None:
                current = _review_digest(row, await self._agent_view(row.agent_id), view)
                if digest is None or not secrets.compare_digest(digest, current):
                    raise ReviewStaleError(row.id)
        if require_rules and effective_agent_id is not None and not permission_rules:
            raise RulesRequiredError()
        return view

    async def _bind_for_confirm(
        self, agent_id: str, credential_id: str, *, created_by: str
    ) -> bool:
        """Bind the agent (idempotent) and re-check it is still usable; True if newly bound.

        The re-check after the write closes the archive race: an archive
        that committed before the bind is seen here and the new binding is
        removed; one that commits after revokes the binding itself.
        """
        async with self._ctx.admin_db.transaction() as admin_session:
            _, already_bound = await EffectsRepository.bind_agent_to_credential(
                admin_session,
                agent_id=agent_id,
                credential_id=credential_id,
                rule_set_id=None,
                created_by=created_by,
            )
        async with self._ctx.admin_db.session() as admin_session:
            agent = await EffectsRepository.get_agent_owner(admin_session, agent_id)
        if agent is None or agent.status in _UNUSABLE_AGENT_STATUSES:
            if not already_bound:
                await self._unbind(agent_id, credential_id)
            if agent is None:
                raise AgentNotFoundError(agent_id)
            raise AgentInactiveError(agent_id, agent.status)
        return not already_bound

    async def _unbind(self, agent_id: str, credential_id: str) -> None:
        async with self._ctx.admin_db.transaction() as admin_session:
            await EffectsRepository.unbind_agent_from_credential(
                admin_session, agent_id=agent_id, credential_id=credential_id
            )

    async def _session_connected_to(self, session_id: str, credential_id: str) -> bool:
        """Whether the session ended ``connected`` with ``credential_id`` bound."""
        async with self._ctx.control_db.session() as session:
            current = await ConnectSessionRepository.get_by_id(session, session_id)
            if current is not None:
                return current.state == "connected" and current.credential_id == credential_id
            outcome = await ConnectSessionOutcomeRepository.get_by_session_id(session, session_id)
        return (
            outcome is not None
            and outcome.outcome == OUTCOME_CONNECTED
            and outcome.credential_id == credential_id
        )

    async def _after_lost_transition(
        self,
        row: ConnectSession,
        *,
        agent_id: str | None,
        credential_id: str,
        newly_bound: bool,
    ) -> InvalidStateTransitionError:
        """Compensate a binding written for a confirm whose transition lost.

        Re-reads the session first: a concurrent confirm that connected the
        same credential keeps the binding (it needs it); a cancel, rejection
        or expiry gets the binding removed. Only a binding this confirm
        created is ever removed.
        """
        if (
            agent_id is not None
            and newly_bound
            and not await self._session_connected_to(row.id, credential_id)
        ):
            await self._unbind(agent_id, credential_id)
            _logger.info(
                "connect_session.confirm_binding_compensated",
                session_id=row.id,
                agent_id=agent_id,
                credential_id=credential_id,
            )
        async with self._ctx.control_db.session() as session:
            current = await ConnectSessionRepository.get_by_id(session, row.id)
        return InvalidStateTransitionError(row.id, current.state if current else "ended", "confirm")

    async def _confirm_secret(
        self,
        row: ConnectSession,
        variant: SecretConfirm,
        agent_id: str | None,
        *,
        late_bound: bool,
        identity: Identity,
    ) -> ConnectedConfirmResult:
        handler = manual_handler_for(row.resolved_flow)
        assert handler is not None
        newly_bound = False
        if agent_id is not None:
            newly_bound = await self._bind_for_confirm(
                agent_id, row.credential_id, created_by=identity.sub
            )
        cas_fields: dict[str, Any] = {"agent_id": agent_id} if late_bound else {}
        try:
            async with self._ctx.control_db.transaction() as session:
                won = await ConnectSessionRepository.transition_state(
                    session,
                    row.id,
                    to_state="connected",
                    from_states=("created",),
                    revision_id=variant.checks.revision_id,
                    **cas_fields,
                )
                if won:
                    await handler.write_secret(
                        session,
                        self._ctx.encryption,
                        row=row,
                        secret=variant.secret,
                        created_by=identity.sub,
                    )
                    await CredentialRepository.set_state(session, row.credential_id, "connected")
                    await CredentialRepository.set_created_by(
                        session, row.credential_id, created_by=identity.sub
                    )
                    if agent_id is not None:
                        await AgentPermissionRuleRepository.replace_user_rules(
                            session,
                            agent_id,
                            row.credential_id,
                            variant.checks.permission_rules,
                            created_by=identity.sub,
                        )
                    current = await ConnectSessionRepository.get_by_id(session, row.id)
                    assert current is not None
                    await ConnectSessionOutcomeRepository.record(
                        session,
                        row=current,
                        outcome=OUTCOME_CONNECTED,
                        error_code=None,
                        ended_at=datetime.now(UTC),
                        credential_id=row.credential_id,
                    )
        except Exception:
            if agent_id is not None and newly_bound:
                await self._unbind(agent_id, row.credential_id)
            raise
        if not won:
            raise await self._after_lost_transition(
                row, agent_id=agent_id, credential_id=row.credential_id, newly_bound=newly_bound
            )
        self._record_connected(row)
        return ConnectedConfirmResult(credential_id=row.credential_id)

    async def _confirm_existing(
        self,
        row: ConnectSession,
        variant: ExistingCredentialConfirm,
        agent_id: str,
        *,
        late_bound: bool,
        identity: Identity,
    ) -> ConfirmResult:
        async with self._ctx.control_db.session() as session:
            pending = await CredentialRepository.get_by_id(session, row.credential_id)
        candidates = await self._existing_candidates(row, identity, pending)
        chosen = next((c for c in candidates if c.credential_id == variant.credential_id), None)
        if chosen is None:
            raise ExistingCredentialNotFoundError(variant.credential_id)
        if chosen.missing_scopes:
            if not chosen.needs_vendor_consent:
                raise InsufficientGrantedScopesError(chosen.credential_id, chosen.missing_scopes)
            return await self._confirm_existing_with_consent(
                row, variant, chosen, agent_id, late_bound=late_bound, identity=identity
            )

        newly_bound = await self._bind_for_confirm(
            agent_id, chosen.credential_id, created_by=identity.sub
        )
        cas_fields: dict[str, Any] = {"agent_id": agent_id} if late_bound else {}
        try:
            async with self._ctx.control_db.transaction() as session:
                won = await ConnectSessionRepository.transition_state(
                    session,
                    row.id,
                    to_state="connected",
                    from_states=(row.state,),
                    revision_id=variant.checks.revision_id,
                    **cas_fields,
                )
                if won:
                    await AgentPermissionRuleRepository.replace_user_rules(
                        session,
                        agent_id,
                        chosen.credential_id,
                        variant.checks.permission_rules,
                        created_by=identity.sub,
                    )
                    current = await ConnectSessionRepository.get_by_id(session, row.id)
                    assert current is not None
                    await ConnectSessionOutcomeRepository.record(
                        session,
                        row=current,
                        outcome=OUTCOME_CONNECTED,
                        error_code=None,
                        ended_at=datetime.now(UTC),
                        credential_id=chosen.credential_id,
                    )
                    # The session's own pending credential is not needed; it
                    # takes the session row (and any aux rows) with it.
                    await CredentialRepository.delete(session, row.credential_id)
        except Exception:
            if newly_bound:
                await self._unbind(agent_id, chosen.credential_id)
            raise
        if not won:
            raise await self._after_lost_transition(
                row, agent_id=agent_id, credential_id=chosen.credential_id, newly_bound=newly_bound
            )
        self._record_connected(row)
        return ConnectedConfirmResult(credential_id=chosen.credential_id)

    async def _confirm_existing_with_consent(
        self,
        row: ConnectSession,
        variant: ExistingCredentialConfirm,
        chosen: ExistingCredentialView,
        agent_id: str,
        *,
        late_bound: bool,
        identity: Identity,
    ) -> ConfirmResult:
        """Use an existing credential that lacks scopes the session asks for, in one step.

        The session moves onto that credential as an edit of the agent's
        future binding: the approved rules and the bind are held on it, a
        vendor consent for the missing scopes runs on the credential's own
        flow, and the bind, rules and new scopes land together with the
        grant. The session's own pending credential is dropped. Until the
        grant lands nothing changes for the credential or the agents already
        bound to it; a failed consent only ends the session.
        """
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, chosen.credential_id)
        consent = await self._consent_source(credential) if credential is not None else None
        if credential is None or consent is None:
            raise InsufficientGrantedScopesError(chosen.credential_id, chosen.missing_scopes or [])
        scopes = sorted(set(chosen.granted_scopes or []) | set(row.requested_scopes or []))
        if consent.entry is not None:
            unknown = self._vendors.validate_scopes(consent.entry, scopes)
            if unknown:
                raise ScopeValidationError(unknown)
        await self._require_scope_widening_allowed(credential, agent_id, identity)
        change: dict[str, Any] = {
            "rules": [rule_to_write(r) for r in variant.checks.permission_rules],
            "rule_set_id": None,
            "detach_rule_set": False,
            "resume": False,
            "bind": True,
            "scopes": scopes,
            "outcome": OUTCOME_CONNECTED,
            "approved_by": identity.sub,
            "revision_id": variant.checks.revision_id,
        }
        cas_fields: dict[str, Any] = {"agent_id": agent_id} if late_bound else {}
        try:
            async with self._ctx.control_db.transaction() as session:
                won = await ConnectSessionRepository.transition_state(
                    session,
                    row.id,
                    to_state="polling",
                    from_states=(row.state,),
                    revision_id=variant.checks.revision_id,
                    intent=INTENT_EDIT,
                    credential_id=credential.id,
                    target_kind=TARGET_KIND_VENDOR,
                    vendor=consent.key or credential.api_vendor,
                    vendor_key=None,
                    dedupe_key=None,
                    resolved_flow=consent.handler_cls.kind,
                    approved_change=change,
                    **cas_fields,
                )
                if won:
                    # The session now points at the chosen credential, so its
                    # own pending one goes alone.
                    await CredentialRepository.delete(session, row.credential_id)
        except DatabaseIntegrityError:
            async with self._ctx.control_db.session() as session:
                open_edit = await ConnectSessionRepository.get_open_edit(
                    session, agent_id=agent_id, credential_id=credential.id
                )
            if open_edit is None:
                raise
            raise EditOpenError(open_edit.id) from None
        if not won:
            raise InvalidStateTransitionError(row.id, row.state, "confirm")
        async with self._ctx.control_db.session() as session:
            polling = await ConnectSessionRepository.get_by_id(session, row.id)
        assert polling is not None
        return await self._begin_consent(polling, consent, scopes, retryable=False)

    async def _confirm_own_client(
        self,
        row: ConnectSession,
        variant: OwnClientConfirm,
        agent_id: str | None,
        *,
        late_bound: bool,
        identity: Identity,
    ) -> AuthCodeConfirmResult:
        if not variant.authorize_url.strip() or not variant.token_url.strip():
            raise OwnClientInvalidError("authorize_url and token_url are required")
        # The token endpoint receives the client secret: https only, no
        # scheme-less input for the validator to complete.
        if not all(
            urlsplit(url.strip()).scheme == "https"
            for url in (variant.authorize_url, variant.token_url)
        ):
            raise OwnClientInvalidError("authorize_url and token_url must be https URLs")
        # Same policy as ``POST /credentials`` (same permission): the
        # control-plane egress allowlist, so the endpoints confirmed here match
        # the ones the token exchange, refresh and later edits accept.
        egress = resolved_control_egress(self._ctx.config)
        try:
            authorize_url = await validate_upstream_url_async(variant.authorize_url, egress)
            token_url = await validate_upstream_url_async(variant.token_url, egress)
        except ValueError as exc:
            raise OwnClientInvalidError(str(exc)) from exc

        handler = AuthCodeFlowHandler(self._ctx)
        cas_fields: dict[str, Any] = {"agent_id": agent_id} if late_bound else {}
        async with self._ctx.control_db.transaction() as session:
            won = await ConnectSessionRepository.transition_state(
                session,
                row.id,
                to_state="polling",
                from_states=(AWAITING_APP,),
                revision_id=variant.checks.revision_id,
                resolved_flow=AuthCodeFlowHandler.kind,
                **cas_fields,
            )
            if won:
                await OAuthClientCredentialRepository.create(
                    session,
                    credential_id=row.credential_id,
                    token_url=token_url,
                    client_id=variant.client_id,
                    encrypted_client_secret=self._ctx.encryption.encrypt(
                        variant.client_secret.get_secret_value()
                    ),
                    authorize_url=authorize_url,
                    scope=" ".join(variant.confirmed_scopes) if variant.confirmed_scopes else None,
                    created_by=identity.sub,
                )
            current = await ConnectSessionRepository.get_by_id(session, row.id)
        if not won or current is None:
            raise InvalidStateTransitionError(
                row.id, current.state if current else "ended", "confirm"
            )

        secret = variant.client_secret

        def _client_secret() -> str:
            return secret.get_secret_value()

        app = SessionApp(
            flow_kind=AuthCodeFlowHandler.kind,
            client_id=variant.client_id,
            client_secret_provider=_client_secret,
            default_scopes=[],
            registration_id=None,
            authorize_url=authorize_url,
            token_url=token_url,
        )
        try:
            challenge = await handler.begin(
                current, app=app, confirmed_scopes=variant.confirmed_scopes
            )
        except Exception:
            # Leave the session as it was so the approver can retry.
            revert_fields: dict[str, Any] = {"agent_id": None} if late_bound else {}
            async with self._ctx.control_db.transaction() as revert_session:
                reverted = await ConnectSessionRepository.transition_state(
                    revert_session,
                    row.id,
                    to_state=AWAITING_APP,
                    from_states=("polling",),
                    resolved_flow=AWAITING_APP,
                    **revert_fields,
                )
                if reverted:
                    await OAuthClientCredentialRepository.delete_by_credential(
                        revert_session, row.credential_id
                    )
            raise
        assert isinstance(challenge, AuthCodeBeginResult)

        async with self._ctx.control_db.transaction() as session:
            await CredentialRepository.set_created_by(
                session, row.credential_id, created_by=identity.sub
            )
            if agent_id is not None:
                await AgentPermissionRuleRepository.replace_user_rules(
                    session,
                    agent_id,
                    row.credential_id,
                    variant.checks.permission_rules,
                    created_by=identity.sub,
                )
        if agent_id is not None:
            # Same intent-then-apply order as the OAuth confirm: the broker
            # ignores the binding until the credential is ``connected``, and
            # ``_mark_terminal`` removes it if the flow dies.
            async with self._ctx.admin_db.transaction() as admin_session:
                await EffectsRepository.bind_agent_to_credential(
                    admin_session,
                    agent_id=agent_id,
                    credential_id=row.credential_id,
                    rule_set_id=None,
                    created_by=identity.sub,
                )
        return AuthCodeConfirmResult(authorize_url=challenge.authorize_url)

    async def _require_session_agent_allowed(
        self, agent_id: str | None, identity: Identity
    ) -> AgentOwnerRow | None:
        """The agent a new session names must be one the caller may act for.

        Returns the agent's row (``None`` when no agent is named, or when an
        agent caller has no admin-DB row) so the caps and the rail event need
        no second admin-DB read.

        An agent may name only itself. It is not checked for status here:
        its access token and API key resolve only while it is active, so an
        archived, disabled or rejected agent never gets this far.

        A user may name an agent they own; ``org:admin`` (effective, so an
        implied grant counts) may name any existing agent. A missing agent
        and one owned by someone else — or by nobody — raise the same
        :class:`AgentNotFoundError`, so the route is not an agent-ownership
        oracle. Only after ownership is settled are the remaining refusals
        reached, so neither leaks anything about another owner's agent: an
        owner who could never approve for the agent (``credentials:write``
        and ``agents:write``, the confirm gate) gets
        :class:`ConfirmationForbiddenError`, and an archived, disabled or
        rejected agent :class:`AgentInactiveError`, since such a session
        could never be confirmed.
        """
        if agent_id is None:
            return None
        if identity.actor_type != ActorType.USER and agent_id != identity.sub:
            raise AgentNotFoundError(agent_id)
        async with self._ctx.admin_db.session() as admin_session:
            agent = await EffectsRepository.get_agent_owner(admin_session, agent_id)
        if identity.actor_type != ActorType.USER:
            return agent
        if agent is None:
            raise AgentNotFoundError(agent_id)
        if not _is_org_admin(identity):
            if agent.owner_id is None or agent.owner_id != identity.sub:
                raise AgentNotFoundError(agent_id)
            if not _is_owner_approver(identity):
                raise ConfirmationForbiddenError(
                    "starting a session for an agent requires credentials:write and agents:write"
                )
        if agent.status in _UNUSABLE_AGENT_STATUSES:
            raise AgentInactiveError(agent_id, agent.status)
        return agent

    async def _require_agent_binding_allowed(
        self, agent_id: str, identity: Identity, *, late_bound: bool
    ) -> None:
        """The target agent must exist, be governable by the caller, and be usable.

        Cross-DB read (agents live in the admin DB) through the
        ``EffectsRepository`` seam. The confirm is about to write
        ``agent_permission_rules`` and an admin-DB binding in this agent's
        name, so the caller must be ``org:admin`` or the agent's owner
        holding both ``credentials:write`` and ``agents:write`` (the bind
        route's own gate). An archived, disabled or rejected agent is
        refused — it can no longer use the credential, and archive revokes
        its bindings.

        A missing agent raises :class:`AgentNotFoundError`. When the agent is
        ``late_bound`` (the session had none and the confirm names it), one
        the caller does not govern (owned by someone else, or by nobody)
        raises the same :class:`AgentNotFoundError` as
        :meth:`_require_session_agent_allowed`, so it answers exactly like an
        agent named at create time. An agent already named on the session is
        shown in its review, so a caller who does not govern it gets
        :class:`ConfirmationForbiddenError`.
        """
        async with self._ctx.admin_db.session() as admin_session:
            agent = await EffectsRepository.get_agent_owner(admin_session, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        if not _is_org_admin(identity):
            # An ownerless agent (nullable ``owner_id``) has no owner to
            # match — only ``org:admin`` may bind in its name. Fail closed.
            if agent.owner_id is None or identity.sub != agent.owner_id:
                if late_bound:
                    raise AgentNotFoundError(agent_id)
                raise ConfirmationForbiddenError(f"agent {agent_id!r} is not owned by the caller")
            if not _is_owner_approver(identity):
                raise ConfirmationForbiddenError(
                    "approving for an agent requires credentials:write and agents:write"
                )
        if agent.status in _UNUSABLE_AGENT_STATUSES:
            raise AgentInactiveError(agent_id, agent.status)

    async def _require_session_access(
        self,
        row: ConnectSession | None,
        *,
        poll_token: str | None,
        identity: Identity,
    ) -> ConnectSession:
        """Gate review / status / confirm / cancel: poll token, or owner / ``org:admin``.

        The ``poll_token`` is the agent-side capability. A human who is
        ``org:admin``, or the owner of the session's target agent holding
        both ``credentials:write`` and ``agents:write``, may act without it.
        Agents never get the token-less path. Every refusal — no session,
        wrong token, a caller who is neither — is the same
        ``InvalidPollTokenError`` so the routes stay free of a session-id
        enumeration oracle.
        """
        if row is None:
            raise InvalidPollTokenError("invalid poll_token")
        if _poll_token_matches(row, poll_token):
            return row
        if await self._is_session_approver(row, identity):
            return row
        raise InvalidPollTokenError("invalid poll_token")

    async def _is_session_approver(self, row: ConnectSession, identity: Identity) -> bool:
        """Whether a human may act on the session without its ``poll_token``."""
        return await self._is_approver_for_agent(row.agent_id, identity)

    async def _is_approver_for_agent(self, agent_id: str | None, identity: Identity) -> bool:
        """``org:admin``, or the human owning ``agent_id`` with both write permissions."""
        if identity.actor_type != ActorType.USER or not identity.sub:
            return False
        if _is_org_admin(identity):
            return True
        if agent_id is None or not _is_owner_approver(identity):
            return False
        async with self._ctx.admin_db.session() as admin_session:
            agent = await EffectsRepository.get_agent_owner(admin_session, agent_id)
        return agent is not None and agent.owner_id is not None and agent.owner_id == identity.sub

    async def _owned_agent_ids(self, identity: Identity) -> list[str]:
        """Agent ids the caller owns, for the read-only owned-agent scoping clause.

        Only humans own agents; ``org:admin`` is unrestricted already, so both
        skip the admin-DB lookup.
        """
        if _is_org_admin(identity) or identity.actor_type != ActorType.USER or not identity.sub:
            return []
        async with self._ctx.admin_db.session() as admin_session:
            return await PrerequisiteRepository.list_agent_ids_owned_by(
                admin_session, owner_id=identity.sub
            )

    # ---- status --------------------------------------------------------

    async def get_status(
        self,
        session_id: str,
        *,
        poll_token: str | None,
        identity: Identity,
    ) -> StatusResult:
        """Return the session's current status — stored-state read only.

        Never touches the vendor. Vendor advancement is scanner-driven for
        polling flows (``ConnectPollScanner`` → ``advance_polling_session``)
        and callback-driven for redirect flows (the OAuth callback route
        → ``complete_from_callback``). One code path drives progress; this
        method just reports whatever state the row is currently in.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
            outcome = (
                await ConnectSessionOutcomeRepository.get_by_session_id(read_session, session_id)
                if row is None
                else None
            )
        if row is None and outcome is not None:
            # The session row went with its pending credential; its recorded
            # outcome answers instead, behind the same token-or-approver gate.
            return await self._outcome_status(outcome, poll_token=poll_token, identity=identity)
        # Uniformly surface "missing session" as ``InvalidPollTokenError``
        # (403) rather than ``SessionNotFoundError`` (404). Anything else
        # would give a caller a session-id enumeration oracle: the
        # ``credentials:connect`` permission guards the endpoint, but the
        # ``poll_token`` (or being the agent's owner / ``org:admin``) is the
        # real capability — without it, 403 for every id is the only
        # non-leaky answer.
        row = await self._require_session_access(row, poll_token=poll_token, identity=identity)

        # Terminal states are immutable. bound_scopes comes off
        # ``oauth_token.scope`` — a single flow-agnostic column that
        # ``_finalise_connected`` populates from
        # ``SuccessTokens.granted_scopes`` for both flows.
        if row.state in ("connected", "expired", "failed"):
            return _terminal_status(row, await self._bound_scopes(row))

        # ``created`` = confirm not called yet; ``awaiting_app`` = no OAuth
        # app resolved yet; ``polling`` = advancement in flight (scanner or
        # callback route). All surface as pending.
        return StatusResult(status="pending")

    async def _outcome_status(
        self, outcome: ConnectSessionOutcome, *, poll_token: str | None, identity: Identity
    ) -> StatusResult:
        """``/status`` for an ended session, from its outcome row.

        The five wire statuses are unchanged: a rejection is ``failed`` with
        ``error_code="rejected"``, a cancel ``failed`` / ``cancelled``.
        """
        token_ok = poll_token is not None and secrets.compare_digest(
            outcome.poll_token_hash, hash_secret(poll_token)
        )
        if not token_ok and not await self._is_approver_for_agent(outcome.agent_id, identity):
            raise InvalidPollTokenError("invalid poll_token")
        if outcome.outcome == OUTCOME_APPLIED:
            return StatusResult(status=OUTCOME_APPLIED, credential_id=outcome.credential_id)
        if outcome.outcome == OUTCOME_CONNECTED:
            bound: list[str] | None = None
            if outcome.credential_id is not None:
                async with self._ctx.control_db.session() as session:
                    token = await OAuthTokenRepository.get_by_credential(
                        session, outcome.credential_id
                    )
                bound = token.scope.split() if token is not None and token.scope else None
            return StatusResult(
                status="connected", credential_id=outcome.credential_id, bound_scopes=bound
            )
        if outcome.outcome == OUTCOME_REJECTED:
            return StatusResult(status="failed", error_code=ERROR_REJECTED)
        if outcome.outcome == OUTCOME_CANCELLED:
            return StatusResult(status="failed", error_code="cancelled")
        if outcome.outcome == "expired":
            return StatusResult(status="expired", error_code=outcome.error_code)
        return StatusResult(status="failed", error_code=outcome.error_code)

    async def _bound_scopes(self, row: ConnectSession) -> list[str] | None:
        """Read ``oauth_token.scope`` for a terminal session (flow-agnostic).

        Populated by ``_finalise_connected`` from ``SuccessTokens.granted_scopes``.
        Absent for non-``connected`` terminal states (failed / expired) — the
        service was never reached to write it — which is the right answer.
        """
        async with self._ctx.control_db.session() as read_session:
            token = await OAuthTokenRepository.get_by_credential(read_session, row.credential_id)
        if token is None or not token.scope:
            return None
        return token.scope.split()

    # ---- scanner-driven advancement (device flow only) ----------------

    async def advance_polling_target(self, credential_id: str) -> None:
        """Dispatch entrypoint called by ``ConnectPollScanner`` for each
        in-flight device-flow credential.

        Two entrypoints write ``device_authorization_credentials`` (the scanner's
        query target): the connect-session flow (has a wrapping
        ``ConnectSession``) and the raw-credential connect flow (no
        session). This method checks for a live session and dispatches to
        the matching advancement path — session mode updates the session
        state machine, credential mode advances ``credentials.state``
        directly. Both delegate the vendor conversation to
        ``DeviceAuthorizationHandler.advance``.
        """
        async with self._ctx.control_db.session() as read_session:
            live_session = await ConnectSessionRepository.get_live_by_credential(
                read_session, credential_id
            )
        if live_session is not None:
            await self.advance_polling_session(live_session.id)
        else:
            await self.advance_polling_credential(credential_id)

    async def advance_polling_session(self, session_id: str) -> None:
        """Session-mode advancement.

        Owns the outer clock (session TTL) and the state-machine
        transitions; delegates the vendor conversation itself to
        ``DeviceAuthorizationHandler.advance``. Callback flows never reach here —
        the scanner filters on the aux row, and callback flows don't
        write one.

        Non-retryable vendor errors surface as terminal ``StatusReport``s
        from the handler (see the fail-fast note in the phase-2 plan); we
        persist them and stop. No retry, no exponential backoff.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        if row is None:
            return
        if row.state != "polling":
            # Terminal or pre-confirm — no advancement to do.
            return
        if row.resolved_flow != DeviceAuthorizationHandler.kind:
            # Callback flows advance via the OAuth callback route.
            return

        # Session TTL guard (outer clock): vendor OAuth sessions get the fixed
        # OAuth TTL, an API target keeps its longer human-approval TTL.
        session_age = (datetime.now(UTC) - row.created_at).total_seconds()
        if session_age > self._ttl_seconds(row):
            await self._mark_terminal(row.id, "expired", "session TTL exceeded")
            return

        handler = DeviceAuthorizationHandler(self._ctx)
        report = await handler.advance(row.credential_id)
        if report.kind == "pending":
            return
        if report.kind == "success":
            assert report.tokens is not None
            await self._finalise_connected(row, handler, report.tokens)
            return
        # Terminal (failed / expired) — persist and stop.
        await self._mark_terminal(
            row.id,
            report.kind,
            report.terminal_detail or report.error_code or report.kind,
            error_code=report.error_code,
        )

    def _ttl_seconds(self, row: ConnectSession) -> float:
        # An edit waits for a human before its consent starts, so it keeps
        # the human-approval TTL throughout (the device code has its own).
        if (
            row.intent != INTENT_EDIT
            and row.target_kind == TARGET_KIND_VENDOR
            and row.resolved_flow in _OAUTH_FLOWS
        ):
            return _SESSION_TTL_SECONDS
        return self._ctx.config.control.connect.manual_flows_ttl_hours * 3600.0

    async def advance_polling_credential(self, credential_id: str) -> None:
        """Credential-mode advancement.

        Mirrors ``advance_polling_session`` but operates on
        ``credentials.state`` — the raw-credential connect path (user
        clicked Connect on a manually-created device-flow credential) has
        no wrapping session, so terminal transitions land on the credential
        row directly. Shares ``DeviceAuthorizationHandler.advance`` verbatim with
        the session path.

        The "flow in flight" signal is the aux row's
        ``encrypted_device_code`` being non-NULL and within its TTL —
        the scanner query already filters on that, and
        ``DeviceAuthorizationHandler.on_finalise`` clears it on success
        + ``_mark_credential_terminal`` clears it on failure. Gating
        here on ``credential.state`` was tempting but wrong: a user
        re-connecting an already-connected credential (fresh grant,
        rotated scopes) starts a new device flow while the credential
        stays ``connected``, and that flow must still be advanced.
        """
        async with self._ctx.control_db.session() as read_session:
            credential = await CredentialRepository.get_by_id(read_session, credential_id)
        if credential is None:
            return

        handler = DeviceAuthorizationHandler(self._ctx)
        report = await handler.advance(credential.id)
        if report.kind == "pending":
            return
        if report.kind == "success":
            assert report.tokens is not None
            await self._finalise_credential_connected(credential, handler, report.tokens)
            return
        # Terminal (failed / expired) — flip the credential row and clear
        # the aux row so the scanner stops picking it up.
        await self._mark_credential_terminal(
            credential.id,
            report.terminal_detail or report.error_code or report.kind,
        )

    async def _mark_credential_terminal(
        self,
        credential_id: str,
        detail: str,
    ) -> None:
        """Move a standalone device-flow credential to ``failed`` + clear aux.

        Clears the aux row's transient state regardless of the specific
        terminal cause so the scanner stops picking it up on the next
        tick. ``credentials`` has no ``error_detail`` column today, so
        detail is logged and lost after this call.
        """
        async with self._ctx.control_db.transaction() as session:
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is None:
                return
            if credential.state == "pending":
                credential.state = "failed"
                await session.flush()
            handler = DeviceAuthorizationHandler(self._ctx)
            await handler.on_finalise(session, credential_id=credential_id)
        _logger.info(
            "credential.device_authorization.terminal",
            credential_id=credential_id,
            detail=detail,
        )

    async def _reopen_session_source(
        self, row: ConnectSession, registration_id: str | None
    ) -> ResolvedVendorSource:
        """Re-open a pre-confirm session's own source, or end the session.

        If the source is no longer usable — registration missing / inactive,
        or the config entry / flow removed — the session fails with
        ``oauth_app_changed``: its aux rows were written for that app, so
        the caller must start a new session.
        """
        key = _require_vendor_source(row, action="re-open its vendor source")
        try:
            return await self._vendors.resolve_session_source(
                key, registration_id=registration_id, flow_kind=row.resolved_flow
            )
        except (
            InvalidOAuthAppRegistrationError,
            UnknownVendorError,
            UnsupportedFlowError,
        ) as exc:
            _logger.info(
                "connect_session.oauth_app_changed",
                session_id=row.id,
                vendor=row.vendor,
                detail=str(exc),
            )
            await self._mark_terminal(row.id, "failed", str(exc), error_code="oauth_app_changed")
            raise OAuthAppChangedError(row.id) from exc

    async def _finalise_connected(
        self,
        row: ConnectSession,
        handler: AuthFlowHandler,
        tokens: SuccessTokens,
    ) -> StatusResult:
        """Session-mode finalise: identity echo → shared write → session close.

        The vendor's ``identity_probe`` comes off the vendor registry entry.
        When it's absent — the case for admin-registered OAuth apps whose
        vendor has no matching config entry — identity echo is skipped and
        the credential lands with ``connected_as=None``. When present, a
        failed echo marks the session ``failed`` (no vaulting) so we don't
        strand a credential we can't tie back to a human.
        """
        if row.intent == INTENT_EDIT:
            return await self._finalise_edit(row, handler, tokens)
        key = _source_key(row)
        if key is None:
            # An API target on the approver's own OAuth client: no registry
            # entry, so no identity probe, and the API is already registered.
            _require_api_own_client(row)
            await self._write_finalise(
                credential_id=row.credential_id,
                handler=handler,
                tokens=tokens,
                connected_as=None,
                created_by=row.initiator_actor_id,
                close_session_id=row.id,
            )
            self._record_connected(row)
            return StatusResult(
                status="connected",
                credential_id=row.credential_id,
                bound_scopes=tokens.granted_scopes,
            )
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
        pinned_registration_id = (
            credential.oauth_app_registration_id if credential is not None else None
        )
        try:
            source = await self._vendors.resolve_session_source(
                key, registration_id=pinned_registration_id, flow_kind=row.resolved_flow
            )
        except (InvalidOAuthAppRegistrationError, UnknownVendorError, UnsupportedFlowError) as exc:
            # The session's app (registration or config entry) went away
            # after the vendor issued tokens. Fail the session rather than
            # raising out of the scanner tick, which would leave it stuck in
            # ``polling``.
            _logger.warning(
                "connect_session.registration_unavailable_at_finalise",
                session_id=row.id,
                error=str(exc),
            )
            await self._mark_terminal(
                row.id, "failed", str(exc), error_code="registration_inactive"
            )
            return StatusResult(status="failed", error_code="registration_inactive")
        entry = source.entry

        connected_as: str | None
        if entry.identity_probe is None:
            connected_as = None
        else:
            # Identity echo — outside the DB transaction (external HTTP).
            try:
                echo = await identity_echo.echo_identity(
                    probe=entry.identity_probe,
                    access_token=tokens.access_token,
                    egress=resolved_control_egress(self._ctx.config),
                )
                connected_as = echo.display
            except identity_echo.IdentityEchoError as exc:
                _logger.warning(
                    "connect_session.identity_echo_failed",
                    session_id=row.id,
                    error=str(exc),
                )
                await self._mark_terminal(
                    row.id, "failed", str(exc), error_code="identity_echo_failed"
                )
                return StatusResult(status="failed", error_code="identity_echo_failed")

        await self._write_finalise(
            credential_id=row.credential_id,
            handler=handler,
            tokens=tokens,
            connected_as=connected_as,
            created_by=row.initiator_actor_id,
            close_session_id=row.id,
        )

        self._record_connected(row, connected_as=connected_as)
        await self._maybe_import_catalog(
            api_id=entry.vendor, initiator_actor_id=row.initiator_actor_id
        )
        return StatusResult(
            status="connected",
            connected_as=connected_as,
            credential_id=row.credential_id,
            bound_scopes=tokens.granted_scopes,
        )

    async def _finalise_edit(
        self,
        row: ConnectSession,
        handler: AuthFlowHandler,
        tokens: SuccessTokens,
    ) -> StatusResult:
        """The vendor grant for an edit's added scopes landed: apply the held change.

        The new token replaces the credential's, then the approved rules (and
        any bind or resume) land and the session closes with its outcome. A
        grant for a different vendor account than the credential's is
        refused: re-consenting must never swap whose account the agent uses.
        """
        change = row.approved_change
        if change is None:
            await self._mark_terminal(
                row.id, "failed", "no approved change held", error_code="approval_missing"
            )
            return StatusResult(status="failed", error_code="approval_missing")
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
        consent = await self._consent_source(credential) if credential is not None else None
        connected_as: str | None = None
        if consent is not None and consent.entry is not None and consent.entry.identity_probe:
            # Identity echo — outside the DB transaction (external HTTP).
            try:
                echo = await identity_echo.echo_identity(
                    probe=consent.entry.identity_probe, access_token=tokens.access_token
                )
                connected_as = echo.display
            except identity_echo.IdentityEchoError as exc:
                await self._mark_terminal(
                    row.id, "failed", str(exc), error_code="identity_echo_failed"
                )
                return StatusResult(status="failed", error_code="identity_echo_failed")
        previous = credential.provider_account_ref if credential is not None else None
        if previous is not None and connected_as is not None and previous != connected_as:
            _logger.warning(
                "connect_session.edit_account_changed",
                session_id=row.id,
                credential_id=row.credential_id,
            )
            await self._mark_terminal(
                row.id, "failed", "consented as another account", error_code="account_changed"
            )
            return StatusResult(status="failed", error_code="account_changed")

        await self._write_finalise(
            credential_id=row.credential_id,
            handler=handler,
            tokens=tokens,
            connected_as=connected_as,
            created_by=change["approved_by"],
            close_session_id=None,
        )
        async with self._ctx.control_db.transaction() as session:
            won = await ConnectSessionRepository.transition_state(
                session, row.id, to_state=change["outcome"], from_states=("polling",)
            )
            if won:
                await self._write_change(session, row, change)
        if not won:
            return StatusResult(status="failed", error_code="session_closed")
        await self._apply_admin_change(row, change)
        await self._record_change_applied(row, change, origin=Origin.SYSTEM.value)
        return StatusResult(
            status=change["outcome"],
            connected_as=connected_as,
            credential_id=row.credential_id,
            bound_scopes=tokens.granted_scopes,
        )

    @staticmethod
    def _record_connected(row: ConnectSession, *, connected_as: str | None = None) -> None:
        """Log line + metrics for a session that reached ``connected``."""
        _logger.info(
            "connect_session.connected",
            session_id=row.id,
            credential_id=row.credential_id,
            connected_as=connected_as,
        )
        # Metrics: terminal-connected + wall-clock time-to-connect. The
        # histogram is a load-bearing SLO surface for the whole feature
        # (fraction of sessions that reach ``connected`` in <N seconds).
        attrs = {"vendor": row.vendor, "flow": row.resolved_flow}
        _sessions_terminal.add(1, {**attrs, "outcome": "connected"})
        _time_to_connected.record((datetime.now(UTC) - row.created_at).total_seconds(), attrs)

    async def _finalise_credential_connected(
        self,
        credential: Credential,
        handler: AuthFlowHandler,
        tokens: SuccessTokens,
    ) -> StatusResult:
        """Credential-mode finalise: shared write, no session row to close.

        Standalone credentials (users clicking Connect on a manually-created
        device-flow row) don't have a vendor-registry entry, so identity
        echo is skipped — ``provider_account_ref`` stays unset and the UI
        can prompt for a display name later if needed. Catalog auto-import
        keys on the credential's own ``catalog_api_id`` column instead of
        a vendor slug.
        """
        # Every credential is created via ``POST /credentials`` behind
        # ``credentials:write``, so ``created_by`` is always populated by
        # the time a connect finalise runs against it — no need for a
        # ``"system"`` fallback (which would violate the no-system-actor
        # invariant enforced by tests/arch). Typed error rather than a
        # bare ``RuntimeError`` so the router's
        # ``ConnectSessionServiceError`` handler renders a structured
        # 500 with ``error_code`` instead of an opaque exception.
        if credential.created_by is None:
            raise CredentialMissingCreatorError(credential.id)
        await self._write_finalise(
            credential_id=credential.id,
            handler=handler,
            tokens=tokens,
            connected_as=None,
            created_by=credential.created_by,
            close_session_id=None,
        )

        _logger.info(
            "credential.device_authorization.connected",
            credential_id=credential.id,
        )
        if credential.catalog_api_id:
            await self._maybe_import_catalog(
                api_id=credential.catalog_api_id,
                initiator_actor_id=credential.created_by,
            )
        return StatusResult(
            status="connected",
            credential_id=credential.id,
            bound_scopes=tokens.granted_scopes,
        )

    async def _write_finalise(
        self,
        *,
        credential_id: str,
        handler: AuthFlowHandler,
        tokens: SuccessTokens,
        connected_as: str | None,
        created_by: str,
        close_session_id: str | None,
    ) -> None:
        """Shared finalise write — vault token, cleanup aux, flip credential.

        One txn covers everything: OAuth token vault, handler's aux-table
        cleanup (device flow clears its transient state), credential.state
        flip, optional connect-session close. ``connected_as`` is written
        onto ``credential.provider_account_ref`` when non-None (session
        mode after identity echo); credential-mode passes ``None`` and the
        column stays unset.
        """
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
        )
        encrypted_access = self._ctx.encryption.encrypt(tokens.access_token)
        encrypted_refresh = (
            self._ctx.encryption.encrypt(tokens.refresh_token) if tokens.refresh_token else None
        )

        # ``granted_scopes`` is the flow-agnostic "what did the human end
        # up with" list — device flow supplies the confirmed set (vendor's
        # ``scope`` field is unreliable there), auth-code supplies what the
        # server actually granted. Persist it verbatim onto
        # ``oauth_token.scope`` so terminal readback is one column, all flows.
        scope_to_persist = (
            " ".join(tokens.granted_scopes) if tokens.granted_scopes else tokens.scope
        )

        async with self._ctx.control_db.transaction() as session:
            # Look up the credential once so we can stamp the oauth_tokens
            # row with the shared-registration provenance. Nullable — legacy
            # embedded credentials just leave it NULL.
            credential_for_stamp = await CredentialRepository.get_by_id(session, credential_id)
            registration_id_stamp: str | None = None
            if credential_for_stamp is not None:
                registration_id_stamp = credential_for_stamp.oauth_app_registration_id

            # Upsert: a re-connect over an existing token row (same
            # credential, fresh grant) MUST update in place rather than
            # INSERT — ``oauth_tokens.credential_id`` is uniquely
            # indexed, and a plain create would trip the constraint on
            # the second successful poll. ``update_tokens`` also clears
            # ``revoked_at``, so a re-connect over a revoked row yields
            # a live token (the derived ``connected`` flag reads that
            # column).
            existing = await OAuthTokenRepository.get_by_credential(session, credential_id)
            if existing is None:
                await OAuthTokenRepository.create(
                    session,
                    credential_id=credential_id,
                    encrypted_access_token=encrypted_access,
                    encrypted_refresh_token=encrypted_refresh,
                    expires_at=expires_at,
                    scope=scope_to_persist,
                    app_registration_id=registration_id_stamp,
                    created_by=created_by,
                )
            else:
                await OAuthTokenRepository.update_tokens(
                    session,
                    credential_id,
                    encrypted_access_token=encrypted_access,
                    encrypted_refresh_token=encrypted_refresh,
                    expires_at=expires_at,
                    scope=scope_to_persist,
                )
                # Refresh the provenance columns on the existing row too so
                # a re-connect that starts going through a shared registration
                # (or migrates off one) is reflected on the token row.
                if registration_id_stamp is not None:
                    existing.app_registration_id = registration_id_stamp
                    await session.flush()
            await handler.on_finalise(session, credential_id=credential_id)
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is not None:
                credential.state = "connected"
                if connected_as is not None:
                    credential.provider_account_ref = connected_as
                # Force an ``updated_at`` bump so client-side pollers
                # detect the transition even on a re-connect where the
                # state was already ``"connected"``. Without this,
                # SQLAlchemy may short-circuit the UPDATE when every
                # assigned column matches its stored value, leaving
                # ``updated_at`` unchanged and ``runConnectFlow`` /
                # ``get_credential``-based watchers polling forever.
                credential.updated_at = datetime.now(UTC)
                await session.flush()
            if close_session_id is not None:
                closed = await ConnectSessionRepository.update_fields(
                    session,
                    close_session_id,
                    state="connected",
                    connected_as=connected_as,
                )
                if closed is not None:
                    await ConnectSessionOutcomeRepository.record(
                        session,
                        row=closed,
                        outcome=OUTCOME_CONNECTED,
                        error_code=None,
                        ended_at=datetime.now(UTC),
                    )

    async def _vendor_api_scope(self, catalog_api_id: str) -> CredentialScope:
        """The API scope a vendor connect stamps on its credential.

        The identity the registry gives the API imported from the vendor's
        catalog api_id (e.g. ``github.com/api.github.com``), so the credential
        matches ``list_by_vendor`` **and** the broker's per-operation identity
        check: the stored ``(vendor, name)`` when the API is already imported,
        else what a first catalog import seeds — ``api_vendor`` from the
        registrable domain of the host (``vendor_from_api_id``, the helper the
        catalog manifest uses) and ``api_name`` from the sub segment
        (``catalog_import_name``). The version stays unscoped.
        """
        registered = None
        if self._catalog_auto_importer is not None:
            registered = await self._catalog_auto_importer.registered_identity(
                api_id=catalog_api_id
            )
        if registered is not None:
            return canonical_credential_scope(
                vendor=registered[0], name=registered[1], version=None
            )
        return canonical_credential_scope(
            vendor=vendor_from_api_id(catalog_api_id) or catalog_api_id,
            name=catalog_import_name(catalog_api_id),
            version=None,
        )

    async def _maybe_import_catalog(self, *, api_id: str, initiator_actor_id: str) -> None:
        """Best-effort catalog auto-import — see the phase-1 rationale.

        A connected credential is only useful to the broker once the
        vendor's OpenAPI is registered — the broker's URL→operation
        discovery is a registry-DB read and returns 404 for unregistered
        upstreams. Idempotent (skips when already registered), best-effort
        (never raises — the credential is still valid without the import).
        """
        if self._catalog_auto_importer is None:
            return
        await self._catalog_auto_importer.ensure_imported(
            api_id=api_id,
            initiator_actor_id=initiator_actor_id,
        )

    async def _mark_terminal(
        self,
        session_id: str,
        state: str,
        detail: str,
        *,
        error_code: str | None = None,
    ) -> bool:
        """Log the terminal outcome, then delete the credential + session.

        An edit session never touches its credential or binding: only the
        session row is deleted, and its outcome recorded.

        A failed / expired / cancelled session leaves an unusable
        ``pending`` credential behind, which shows up in the credentials
        list as a stale row the user then has to hand-delete. We take
        the credential with us: cascade-drops the ``connect_sessions``
        row (FK ``ondelete=CASCADE``) plus every flow-specific aux row
        (device_authorization_credentials, oauth_client_credentials,
        oauth_tokens, etc. via ``all, delete-orphan``). The SPA polling
        ``/status`` sees the session vanish and treats the 404 as
        terminal-failed — cleaner than a lingering ``failed`` row it
        would have to garbage-collect later.

        Compare-and-swap guarded: the delete only happens if the session
        is still live (``LIVE_STATES``) at the moment of the UPDATE.
        Without the CAS, a replayed callback URL carrying
        ``error=access_denied`` — or a second scanner pod whose in-flight
        poll loses the race against a successful one — would delete an
        already-``connected`` credential, its vaulted token, and the
        admin binding. Returns True when this call won the transition.

        The winner records the session's outcome in the same transaction, so
        how it ended outlives the cascade-deleted session row.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        if row is None:
            return False
        async with self._ctx.control_db.transaction() as session:
            won = await ConnectSessionRepository.transition_state(
                session,
                session_id,
                to_state=state,
                from_states=LIVE_STATES,
                error_code=error_code,
            )
            if won:
                await ConnectSessionOutcomeRepository.record(
                    session,
                    row=row,
                    outcome=_outcome_for(state, error_code),
                    error_code=error_code,
                    ended_at=datetime.now(UTC),
                    # An edit's outcome names its binding's credential (the
                    # rejection cooldown keys on it); a create's has none.
                    credential_id=row.credential_id if row.intent == INTENT_EDIT else None,
                )
                if row.intent == INTENT_EDIT:
                    # An edit's credential is the live one the binding uses:
                    # only the session goes, its outcome stays. A device
                    # consent in flight leaves the credential's token alone;
                    # only its transient device code is cleared.
                    if row.resolved_flow == DeviceAuthorizationHandler.kind:
                        await DeviceAuthorizationHandler(self._ctx).on_finalise(
                            session, credential_id=row.credential_id
                        )
                    await ConnectSessionRepository.delete(session, row.id)
                else:
                    await CredentialRepository.delete(session, row.credential_id)
        if not won:
            _logger.info(
                "connect_session.terminal_skipped",
                session_id=session_id,
                requested_state=state,
                reason="session already terminal or gone",
            )
            return False
        _logger.info(
            "connect_session.terminal",
            session_id=session_id,
            state=state,
            error_code=error_code,
            error_detail=detail,
            credential_id=row.credential_id,
        )
        # Terminal transitions delete the credential + cascade the aux
        # rows, so they're the most consequential mutation on the
        # lifecycle — an operator needs to be able to say "who / what
        # tore this session down" (scanner-TTL sweep, vendor rejection,
        # callback error, or user-driven cancel). ``_mark_terminal`` is
        # called from scanner / callback contexts without a caller
        # ``Identity``, so attribute the revoke to the session's
        # ``initiator_actor_id`` — the real actor whose session is
        # being torn down. The ``reason`` field (``detail``) captures
        # *why* (TTL vs. callback vs. user cancel); the actor field
        # names *whose* session it was, without inventing a "system"
        # sentinel that no longer exists in ``ActorType``.
        # Tolerant of residual ``sva_`` initiators (theme-8 L4).
        initiator_actor_type = actor_type_label_from_id(row.initiator_actor_id)
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.REVOKE,
            target_type=AuditTargetType.SESSION,
            target_id=session_id,
            actor_type=initiator_actor_type,
            actor_id=row.initiator_actor_id,
            # Expiry, a failed poll or a cancel tears the session down on the
            # platform's side, attributed to its initiator.
            origin=Origin.SYSTEM.value,
            before={"state": row.state},
            after={
                "state": state,
                "error_code": error_code,
                "credential_id": row.credential_id,
            },
            reason=detail,
        )
        # Metrics: per-vendor + per-flow + per-outcome unhappy-terminal
        # counter. ``state`` here is the wire outcome (``failed``,
        # ``expired``, ``cancelled``) — the connected path emits from
        # ``_finalise_connected``, not through here.
        _sessions_terminal.add(
            1,
            {
                "vendor": row.vendor,
                "flow": row.resolved_flow,
                "outcome": state,
            },
        )
        if row.agent_id is not None and row.intent != INTENT_EDIT:
            # The control-side ``agent_permission_rules`` rows cascade with
            # the credential; the admin-DB binding row is cross-DB (no FK)
            # and must be swept explicitly so no ghost binding outlives the
            # credential. Idempotent — a pre-confirm terminal never created
            # one, and the DELETE is a no-op then.
            async with self._ctx.admin_db.transaction() as admin_session:
                await EffectsRepository.unbind_agent_from_credential(
                    admin_session,
                    agent_id=row.agent_id,
                    credential_id=row.credential_id,
                )
        return True

    # ---- TTL sweep (scanner-driven, flow-agnostic) ---------------------

    async def expire_stale_sessions(self, *, limit: int = 100) -> int:
        """Expire live sessions older than their TTL (scanner tick).

        The device-flow scanner only ever sees sessions with an active
        device-code aux row, so a session whose initiator never called
        ``:confirm`` (state ``created``) or whose auth-code popup was
        abandoned (state ``polling``, callback never fires) has no other
        expiry driver — it, and the upfront ``pending`` credential row it
        minted, would leak forever. Each expiry goes through
        ``_mark_terminal`` (CAS-guarded), so a session that completes
        between the read and the sweep is left alone. Vendor OAuth flows
        expire after ``_SESSION_TTL_SECONDS``; every other flow after
        ``control.connect.manual_flows_ttl_hours``.

        The same tick drops outcomes older than the retention window.
        Returns the number of sessions actually expired.
        """
        now = datetime.now(UTC)
        oauth_cutoff = now - timedelta(seconds=_SESSION_TTL_SECONDS)
        manual_cutoff = now - timedelta(
            hours=self._ctx.config.control.connect.manual_flows_ttl_hours
        )
        # An ``awaiting_app`` session whose app has since been registered
        # moves on before the expiry check sees it.
        await self.resolve_awaiting_app_sessions(limit=limit)
        async with self._ctx.control_db.session() as read_session:
            stale_ids = await ConnectSessionRepository.list_stale_live_ids(
                read_session,
                older_than=oauth_cutoff,
                limit=limit,
                flows=_OAUTH_FLOWS,
                target_kind=TARGET_KIND_VENDOR,
                intent=INTENT_CREATE,
            )
            stale_ids += await ConnectSessionRepository.list_stale_live_ids(
                read_session, older_than=manual_cutoff, limit=limit, exclude_flows=_OAUTH_FLOWS
            )
            # An edit keeps the human-approval TTL even once its consent runs
            # on an OAuth flow.
            stale_ids += await ConnectSessionRepository.list_stale_live_ids(
                read_session,
                older_than=manual_cutoff,
                limit=limit,
                flows=_OAUTH_FLOWS,
                intent=INTENT_EDIT,
            )
            # An API target that resolved to an OAuth flow keeps the
            # human-approval TTL it was opened with.
            stale_ids += await ConnectSessionRepository.list_stale_live_ids(
                read_session,
                older_than=manual_cutoff,
                limit=limit,
                flows=_OAUTH_FLOWS,
                exclude_target_kind=TARGET_KIND_VENDOR,
            )
        expired = 0
        for session_id in stale_ids:
            if await self._mark_terminal(session_id, "expired", "session TTL exceeded"):
                expired += 1
        async with self._ctx.control_db.transaction() as session:
            await ConnectSessionOutcomeRepository.delete_ended_before(
                session, older_than=now - _OUTCOME_RETENTION, limit=limit
            )
        return expired

    # ---- redirect-based (auth-code / MCP) completion ----------------------

    async def mark_terminal_from_callback(self, *, raw_state: str, error: str) -> str:
        """Mark a session ``failed`` from a callback landing that carried no
        usable code (vendor returned ``error`` or dropped ``code`` entirely).

        Takes the raw signed state (not a pre-decoded session id) and runs
        the same ``consume_callback_state`` prologue as
        ``complete_from_callback`` — the error branch must consume the
        one-shot nonce too, or a captured callback URL with
        ``error=access_denied`` could be replayed after a successful
        connect (``_mark_terminal``'s CAS is the second line of defence).
        Raises ``StateError`` subclasses on decode / replay failures.
        Returns the session id for the router's log line.
        """
        state = await consume_callback_state(self._ctx, raw_state)
        if state.session_id is None:
            raise NoOpForFlowError("callback state missing session id")
        await self._mark_terminal(state.session_id, "failed", error, error_code="callback_error")
        return state.session_id

    async def cancel_session(
        self, session_id: str, *, poll_token: str | None, identity: Identity
    ) -> None:
        """User-driven cancellation from the SPA (Cancel button or dialog dismiss).

        Gated like ``/status`` — the session's ``poll_token`` (the SPA
        already holds it), or the target agent's owner / ``org:admin``.
        Idempotent: already-terminal sessions are a no-op (the credential +
        session have already been cleaned up by ``_mark_terminal`` on a
        prior terminal transition).
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        # ``get_status`` parity: no session-existence oracle. A caller
        # without a valid ``poll_token`` who is not the owner / admin gets
        # 403 whether the session exists or not; a legitimate caller with a
        # poll_token that predates cleanup also gets 403, which is harmless
        # for the fire-and-forget unmount cancel (the caller ``.catch``es it).
        row = await self._require_session_access(row, poll_token=poll_token, identity=identity)
        if row.state in ("connected", "failed", "expired"):
            return
        await self._mark_terminal(session_id, "failed", "user cancelled", error_code="cancelled")

    async def reject_session(self, session_id: str, *, identity: Identity) -> None:
        """A human explicitly turns the agent's request down (the approve dialog's Reject).

        Only the target agent's owner (with ``credentials:write`` and
        ``agents:write``) or ``org:admin`` — never the poll token, never an
        agent; anyone else gets the same uniform 403 as the other session
        routes. The session ends ``failed`` with ``error_code="rejected"``,
        and the agent's repeat ask for the same target is refused for the
        rejection cooldown. Rejecting an already-ended session is a no-op.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        if row is None or not await self._is_session_approver(row, identity):
            raise InvalidPollTokenError("invalid poll_token")
        if row.state not in LIVE_STATES:
            return
        won = await self._mark_terminal(
            session_id, "failed", "rejected by approver", error_code=ERROR_REJECTED
        )
        if won:
            await record_audit_best_effort(
                self._ctx,
                action=AuditAction.DENY,
                target_type=AuditTargetType.SESSION,
                target_id=row.id,
                actor_type=identity.actor_type.value,
                actor_id=identity.sub,
                origin=identity.origin.value,
                after={
                    "target_kind": row.target_kind,
                    "vendor": row.vendor,
                    "api_name": row.api_name,
                    "api_version": row.api_version,
                    "agent_id": row.agent_id,
                    "outcome": OUTCOME_REJECTED,
                },
            )

    async def complete_from_callback(
        self,
        *,
        raw_state: str,
        code: str,
    ) -> StatusResult:
        """Complete a connect session from an OAuth callback landing.

        Takes the raw signed state JWT (rather than a pre-decoded
        session_id) so the shared ``consume_callback_state`` prologue
        gates BOTH callback paths — replay protection can't be silently
        skipped by a future new entrypoint. The helper raises
        ``StateError`` subclasses on decode / actor / replay failures;
        we let those propagate to the router (it maps them to the
        canonical error redirect and logs the specific reason).

        Raises ``NoOpForFlowError`` if the session's resolved flow
        doesn't support a callback path (device flow) — a defensive
        guard the router should never trip, since only auth-code state
        JWTs carry a ``sid``.
        """
        state = await consume_callback_state(self._ctx, raw_state)
        if state.session_id is None:
            # State without ``sid`` doesn't belong on this path — router
            # dispatches to the standalone-credential handler for those.
            raise NoOpForFlowError("callback state missing session id")

        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, state.session_id)
            if row is None:
                raise SessionNotFoundError(state.session_id)

        # Callback-only handler concretely by construction — the state JWT
        # that carries ``sid`` is signed by the auth-code path, so any
        # callback landing must belong to that flow. Everything else is a
        # bug (device flow doesn't route here).
        if row.resolved_flow != AuthCodeFlowHandler.kind:
            raise NoOpForFlowError(row.resolved_flow)
        handler = AuthCodeFlowHandler(self._ctx)

        try:
            tokens = await handler.complete_from_callback(row, code=code)
        except RegistrationInactiveError as exc:
            # The admin deactivated the app between confirm and the callback.
            await self._mark_terminal(
                row.id, "failed", str(exc), error_code="registration_inactive"
            )
            return StatusResult(status="failed", error_code="registration_inactive")
        except Exception as exc:
            # Any exchange failure ⇒ terminal-failed; the human's popup will
            # observe the transition on the next status poll.
            await self._mark_terminal(
                row.id, "failed", str(exc), error_code="token_exchange_failed"
            )
            return StatusResult(status="failed", error_code="token_exchange_failed")
        finally:
            # The verifier is single-use (RFC 7636 §4.5): once the code has
            # been exchanged — or the exchange failed — it has no further
            # purpose, so don't leave it at rest on the session row.
            async with self._ctx.control_db.transaction() as session:
                await ConnectSessionRepository.update_fields(
                    session, row.id, pkce_code_verifier=None
                )

        return await self._finalise_connected(row, handler, tokens)
