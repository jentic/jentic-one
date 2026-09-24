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

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import (
    AgentPermissionRuleRepository,
    CredentialRepository,
    OAuthTokenRepository,
)
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.credentials.state import consume_callback_state
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.errors import (
    AgentNotFoundError,
    ConfirmationForbiddenError,
    CredentialMissingCreatorError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    NoOpForFlowError,
    ScopeValidationError,
    SessionNotFoundError,
)
from jentic_one.control.services.integrations.flow_handlers import (
    AuthCodeFlowHandler,
    AuthFlowHandler,
    DeviceAuthorizationHandler,
    handler_for,
)
from jentic_one.control.services.integrations.flow_handlers.base import SuccessTokens
from jentic_one.control.services.vendors.service import (
    ResolvedScope,
    UnknownVendorError,
    VendorRegistryService,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.catalog import CatalogAutoImportProtocol
from jentic_one.shared.context import Context
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.actors import actor_type_from_id
from jentic_one.shared.models.api_identity import canonical_credential_scope
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor

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
class CreatedSession:
    session_id: str
    approval_url: str
    poll_token: str
    resolved_flow: str


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


ConfirmResult = DeviceAuthorizationConfirmResult | AuthCodeConfirmResult


@dataclass(slots=True, frozen=True)
class StatusResult:
    status: str  # "pending" | "polling" | "connected" | "failed" | "expired"
    connected_as: str | None = None
    credential_id: str | None = None
    bound_scopes: list[str] | None = None
    error_code: str | None = None


# ---------------------------------------------------------------------------
# Config / defaults
# ---------------------------------------------------------------------------

# Overall hard TTL for a session — clamps stale rows even if flow-level
# device_code_expires_at hasn't been reached.
_SESSION_TTL_SECONDS = 30 * 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_view(s: ResolvedScope) -> ScopeView:
    return ScopeView(
        name=s.name,
        classification=s.classification,
        default=s.default,
        requested=s.requested,
        description=s.description,
    )


def _require_state(row: ConnectSession, *, expected: str, action: str) -> None:
    if row.state != expected:
        raise InvalidStateTransitionError(row.id, row.state, action)


def _forbid_self_confirm(row: ConnectSession, caller_actor_type: ActorType) -> None:
    """Agent-initiated sessions must be confirmed by a human on the review page.

    ``caller_actor_type`` comes from the caller's verified identity, not from
    the payload.
    """
    initiator_is_agent = row.initiator_actor_id.startswith("agnt_")
    if initiator_is_agent and caller_actor_type == ActorType.AGENT:
        raise ConfirmationForbiddenError("agent-initiated sessions cannot be confirmed by an agent")


def _verify_poll_token(row: ConnectSession, token: str) -> None:
    """Constant-time comparison against the session's poll_token."""
    if not secrets.compare_digest(row.poll_token, token):
        raise InvalidPollTokenError("poll_token mismatch")


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

    # ---- create -----------------------------------------------------------

    async def create_session(
        self,
        *,
        vendor_key: str,
        agent_id: str | None,
        initiator_actor_id: str,
        requested_scopes: list[str] | None = None,
        preferred_flow: str | None = None,
        reason: str | None = None,
        # Wire-shape ``PermissionRuleSchema`` dicts — validated at the
        # router boundary. Stored on the session row and surfaced on the
        # review page so the human owner sees exactly what the agent asked
        # for before committing anything to ``agent_permission_rules``.
        requested_permission_rules: list[dict[str, object]] | None = None,
    ) -> CreatedSession:
        """Create a pending session + upfront credential row.

        Actor-type checks live in the router (agent callers have
        ``agent_id`` forced to their own identity and refuse a payload
        override; user callers may omit it). When ``agent_id`` is None no
        agent-credential binding is created at confirm time — the user is
        connecting a credential without granting any agent access to it,
        and can bind an agent later through the credentials API.
        """
        entry = self._vendors.get(vendor_key)
        flow = self._vendors.resolve_flow(vendor_key, preferred_flow)

        try:
            handler_cls = handler_for(flow.kind)
        except KeyError as exc:
            raise NoOpForFlowError(flow.kind) from exc
        handler = handler_cls(self._ctx)

        poll_token = secrets.token_urlsafe(32)

        # Decompose the vendor's catalog api_id (e.g. ``github.com/api.github.com``)
        # into the same identity axes a normal catalog import puts on the
        # registered Api row and the credential: ``api_vendor`` slugged from the
        # host portion, ``api_name`` slugged from the *whole* api_id (mirrors
        # registry ``_to_import_source`` which passes ``entry.api_id`` verbatim as
        # ``api_name`` and lets the import pipeline slugify it), and
        # ``catalog_api_id`` verbatim as display-only provenance. That way the
        # credential's identity matches ``list_by_vendor`` **and** the broker's
        # per-operation identity check.
        raw_vendor = entry.vendor.split("/", 1)[0]
        api_scope = canonical_credential_scope(
            vendor=raw_vendor,
            name=entry.vendor,
            version=None,
        )

        async with self._ctx.control_db.transaction() as session:
            credential = await CredentialRepository.create(
                session,
                type=handler.stored_type.value,
                # Credential name is display-only + user-editable; scoping
                # by ``agent_id`` here would collapse to ``(None)`` in the
                # UI when ``agent_id`` is omitted and be redundant even
                # when present (the agent-credential binding row is the
                # source of truth for "which agent uses this").
                name=entry.display_name,
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
                flow=flow,
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
                poll_token=poll_token,
                requested_scopes=requested_scopes or [],
                requested_permission_rules=requested_permission_rules or [],
                preferred_flow=preferred_flow,
                reason=reason,
                created_by=initiator_actor_id,
            )

        approval_url = self._approval_url_for(row.id, poll_token)
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
        # Actor type is inferred from the id prefix — the router already
        # resolves it, but we deliberately don't thread ``Identity`` into
        # this service method so agent callers stay decoupled from the
        # confirm/binding surface.
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.SESSION,
            target_id=row.id,
            actor_type=actor_type_from_id(initiator_actor_id).value,
            actor_id=initiator_actor_id,
            after={
                "vendor": vendor_key,
                "resolved_flow": flow.kind,
                "agent_id": agent_id,
                "credential_id": row.credential_id,
            },
        )
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
        )

    def _approval_url_for(self, session_id: str, poll_token: str) -> str:
        """Build the human-facing approval URL for an agent-initiated session.

        Lands on the credentials page (``/app/credentials``) with the session id
        and poll token as query params; the SPA detects the ``approve`` param
        and auto-opens the credential dialog into the vendor-approval flow. The
        poll token rides along because the status endpoint (RFC-8628 poller) is
        gated by the token — the human owner needs it to observe completion.
        """
        base = (self._ctx.config.auth.canonical_base_url or "").rstrip("/")
        return f"{base}/app/credentials?approve={session_id}&poll_token={poll_token}"

    # ---- review data ------------------------------------------------------

    async def get_review_data(self, session_id: str, *, poll_token: str) -> ReviewData:
        """Return everything the review page needs to render.

        Gated by the session's ``poll_token`` — session ids travel in
        approval URLs, so they are not secrets, and the review payload
        (vendor, scopes, requested rules, initiator) must not be readable
        by any actor that merely holds ``credentials:write``. Missing
        session and token mismatch surface identically (mirrors
        ``get_status`` — no session-id enumeration oracle).

        The scope list is the union of the vendor's catalog with the
        initiator's as-requested list — flagged so the UI can highlight
        write scopes the agent asked for. ``requested_scopes`` lives on the
        session row itself (flow-agnostic), so this method never needs to
        reach into a flow-specific aux table.
        """
        async with self._ctx.control_db.session() as session:
            row = await ConnectSessionRepository.get_by_id(session, session_id)
            if row is None:
                raise InvalidPollTokenError("invalid poll_token")
            _verify_poll_token(row, poll_token)
            # Pull the credential's api coords so the SPA can call
            # ``/apis/{vendor}/{name}/{version}/operations`` for the
            # rules-page preview. ``api_version`` is nullable — the
            # catalog import populates it asynchronously.
            credential = await CredentialRepository.get_by_id(session, row.credential_id)

        entry = self._vendors.get(row.vendor)
        resolved = self._vendors.merge_scopes(row.vendor, row.requested_scopes or [])
        # The credential row's ``api_version`` is set at create-time to
        # ``None`` — the imported OpenAPI decides its own version once the
        # catalog import completes. Look it up live from the registry via
        # the catalog-import DI seam so the SPA's rules-preview knows what
        # version to hit ``/apis/.../operations`` against. Returns ``None``
        # until the import lands; SPA polls the review-session endpoint
        # while ``api_version`` is None.
        api_version: str | None = None
        if self._catalog_auto_importer is not None:
            api_version = await self._catalog_auto_importer.current_version(api_id=entry.vendor)
        return ReviewData(
            session_id=row.id,
            state=row.state,
            vendor_key=row.vendor,
            vendor_display_name=entry.display_name,
            resolved_flow=row.resolved_flow,
            reason=row.reason,
            requested_by_actor_id=row.initiator_actor_id,
            scopes=[_scope_view(s) for s in resolved],
            requested_permission_rules=row.requested_permission_rules or [],
            api_vendor=credential.api_vendor if credential else "",
            api_name=credential.api_name if credential else None,
            api_version=api_version,
        )

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
        and a delegated agent holding ``owner:credentials:read`` also sees
        its owner's sessions. Rows are slim summaries — the ``poll_token``
        capability never leaves the service on this path.
        """
        decoded_cursor = None
        if cursor is not None:
            ts, sid = decode_cursor_str(cursor)
            decoded_cursor = (ts, sid)

        access_filters = build_access_filters(identity, ConnectSession)

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

            data = [self._to_summary(r) for r in rows]
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)

        return SessionPage(data=data, has_more=has_more, next_cursor=next_cursor)

    def _to_summary(self, row: ConnectSession) -> SessionSummary:
        return SessionSummary(
            session_id=row.id,
            state=row.state,
            vendor_key=row.vendor,
            vendor_display_name=self._vendor_display_name(row.vendor),
            agent_id=row.agent_id,
            requested_by_actor_id=row.initiator_actor_id,
            reason=row.reason,
            connected_as=row.connected_as,
            error_code=row.error_code,
            created_at=row.created_at,
        )

    def _vendor_display_name(self, vendor_key: str) -> str:
        """Resolve a vendor key to its display name, tolerating removed vendors.

        The vendor registry is config-seeded — an operator can drop an entry
        after sessions referencing it were persisted, and the list must not
        500 on such historical rows. Fall back to the raw key.
        """
        try:
            return self._vendors.get(vendor_key).display_name
        except UnknownVendorError:
            return vendor_key

    # ---- confirm ----------------------------------------------------------

    async def confirm(
        self,
        session_id: str,
        *,
        poll_token: str,
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
    ) -> ConfirmResult:
        """Confirm scopes + permissions and kick off the vendor-side flow.

        Gated by the ``poll_token`` capability like ``get_review_data`` —
        without it, any actor holding ``credentials:write`` could confirm
        any session (ids travel in approval URLs) and bind an arbitrary
        agent. Missing session and token mismatch surface identically.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        if row is None:
            raise InvalidPollTokenError("invalid poll_token")
        _verify_poll_token(row, poll_token)

        _forbid_self_confirm(row, identity.actor_type)
        # Friendly pre-check for the common stale-page case; the CAS below
        # is the authoritative guard against a concurrent confirm.
        _require_state(row, expected="created", action="confirm")

        flow = self._vendors.resolve_flow(row.vendor, row.resolved_flow)
        try:
            handler_cls = handler_for(flow.kind)
        except KeyError as exc:
            raise NoOpForFlowError(flow.kind) from exc
        handler = handler_cls(self._ctx)

        unknown = self._vendors.validate_scopes(row.vendor, confirmed_scopes)
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
            await self._require_agent_binding_allowed(effective_agent_id, identity)

        # CAS ``created`` → ``polling`` BEFORE the vendor call: without
        # it, two concurrent confirms both pass the stale read above and
        # both fire the vendor's ``begin`` (TOCTOU). The loser sees
        # rowcount 0 and gets the same 409 as the stale-page case.
        cas_fields: dict[str, object] = {"agent_id": effective_agent_id} if late_bound else {}
        async with self._ctx.control_db.transaction() as cas_session:
            won = await ConnectSessionRepository.transition_state(
                cas_session,
                row.id,
                to_state="polling",
                from_states=("created",),
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
            challenge = await handler.begin(row, flow=flow, confirmed_scopes=confirmed_scopes)
        except Exception:
            # A vendor-side ``begin`` failure must leave the session
            # retryable — roll the CAS back to ``created`` (undoing a
            # late-bound agent too, so a retry can pick a different one).
            revert_fields: dict[str, object] = {"agent_id": None} if late_bound else {}
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
            after={
                "vendor": row.vendor,
                "resolved_flow": row.resolved_flow,
                "credential_id": row.credential_id,
                "agent_id": effective_agent_id,
                "confirmed_scopes": list(confirmed_scopes),
                "rules_count": len(permission_rules),
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

    async def _require_agent_binding_allowed(self, agent_id: str, identity: Identity) -> None:
        """The target agent must exist and be governable by the confirming caller.

        Cross-DB read (agents live in the admin DB) through the
        ``EffectsRepository`` seam. Owner-or-admin mirrors the
        query-scoping conventions: the confirm is about to write
        ``agent_permission_rules`` and an admin-DB binding in this
        agent's name, so the caller must own the agent or hold
        ``org:admin``.
        """
        async with self._ctx.admin_db.session() as admin_session:
            exists, owner_id = await EffectsRepository.get_agent_owner(admin_session, agent_id)
        if not exists:
            raise AgentNotFoundError(agent_id)
        if "org:admin" in identity.permissions:
            return
        # An ownerless agent (nullable ``owner_id``) has no owner to match —
        # only ``org:admin`` may bind in its name. Fail closed.
        if owner_id is not None and identity.sub == owner_id:
            return
        raise ConfirmationForbiddenError(f"agent {agent_id!r} is not owned by the caller")

    # ---- status --------------------------------------------------------

    async def get_status(
        self,
        session_id: str,
        *,
        poll_token: str,
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
            # Uniformly surface "missing session" as ``InvalidPollTokenError``
            # (403) rather than ``SessionNotFoundError`` (404). Anything else
            # would give an unauth'd caller a session-id enumeration oracle:
            # the ``credentials:connect`` scope guards the endpoint, but the
            # ``poll_token`` is the real capability — without it, 403 for
            # every id (missing or existing) is the only non-leaky answer.
            if row is None:
                raise InvalidPollTokenError("invalid poll_token")
            _verify_poll_token(row, poll_token)

        # Terminal states are immutable. bound_scopes comes off
        # ``oauth_token.scope`` — a single flow-agnostic column that
        # ``_finalise_connected`` populates from
        # ``SuccessTokens.granted_scopes`` for both flows.
        if row.state in ("connected", "expired", "failed"):
            return _terminal_status(row, await self._bound_scopes(row))

        # ``created`` = confirm not called yet; ``polling`` = advancement
        # in flight (scanner or callback route). Both surface as pending.
        return StatusResult(status="pending")

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

        # Session TTL guard (flow-agnostic outer clock).
        session_age = (datetime.now(UTC) - row.created_at).total_seconds()
        if session_age > _SESSION_TTL_SECONDS:
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

    async def _finalise_connected(
        self,
        row: ConnectSession,
        handler: AuthFlowHandler,
        tokens: SuccessTokens,
    ) -> StatusResult:
        """Session-mode finalise: identity echo → shared write → session close.

        The vendor's ``identity_probe`` comes off the vendor registry entry
        (session flows always know their vendor_key). A failed echo marks
        the session ``failed`` and returns without vaulting the token —
        we can't tie the credential back to a human without it.
        """
        entry = self._vendors.get(row.vendor)

        # Identity echo — outside the DB transaction (external HTTP).
        try:
            echo = await identity_echo.echo_identity(
                probe=entry.identity_probe,
                access_token=tokens.access_token,
            )
            connected_as = echo.display
        except identity_echo.IdentityEchoError as exc:
            _logger.warning(
                "connect_session.identity_echo_failed",
                session_id=row.id,
                error=str(exc),
            )
            await self._mark_terminal(row.id, "failed", str(exc), error_code="identity_echo_failed")
            return StatusResult(status="failed", error_code="identity_echo_failed")

        await self._write_finalise(
            credential_id=row.credential_id,
            handler=handler,
            tokens=tokens,
            connected_as=connected_as,
            created_by=row.initiator_actor_id,
            close_session_id=row.id,
        )

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
        await self._maybe_import_catalog(
            api_id=entry.vendor, initiator_actor_id=row.initiator_actor_id
        )
        return StatusResult(
            status="connected",
            connected_as=connected_as,
            credential_id=row.credential_id,
            bound_scopes=tokens.granted_scopes,
        )

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
                await ConnectSessionRepository.update_fields(
                    session,
                    close_session_id,
                    state="connected",
                    connected_as=connected_as,
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
        is still live (``created``/``polling``) at the moment of the
        UPDATE. Without the CAS, a replayed callback URL carrying
        ``error=access_denied`` — or a second scanner pod whose in-flight
        poll loses the race against a successful one — would delete an
        already-``connected`` credential, its vaulted token, and the
        admin binding. Returns True when this call won the transition.
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
                from_states=("created", "polling"),
                error_code=error_code,
            )
            if won:
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
        initiator_actor_type = actor_type_from_id(row.initiator_actor_id)
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.REVOKE,
            target_type=AuditTargetType.SESSION,
            target_id=session_id,
            actor_type=initiator_actor_type.value,
            actor_id=row.initiator_actor_id,
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
        if row.agent_id is not None:
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
        """Expire live sessions older than the session TTL (scanner tick).

        The device-flow scanner only ever sees sessions with an active
        device-code aux row, so a session whose initiator never called
        ``:confirm`` (state ``created``) or whose auth-code popup was
        abandoned (state ``polling``, callback never fires) has no other
        expiry driver — it, and the upfront ``pending`` credential row it
        minted, would leak forever. Each expiry goes through
        ``_mark_terminal`` (CAS-guarded), so a session that completes
        between the read and the sweep is left alone. Returns the number
        of sessions actually expired.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=_SESSION_TTL_SECONDS)
        async with self._ctx.control_db.session() as read_session:
            stale_ids = await ConnectSessionRepository.list_stale_live_ids(
                read_session, older_than=cutoff, limit=limit
            )
        expired = 0
        for session_id in stale_ids:
            if await self._mark_terminal(session_id, "expired", "session TTL exceeded"):
                expired += 1
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

    async def cancel_session(self, session_id: str, *, poll_token: str) -> None:
        """User-driven cancellation from the SPA (Cancel button or dialog dismiss).

        Gated by the session's ``poll_token`` — same capability the SPA
        already holds to poll ``/status``, so we don't force the caller
        to bring a heavier scope. Idempotent: already-terminal sessions
        are a no-op (the credential + session have already been cleaned
        up by ``_mark_terminal`` on a prior terminal transition).
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
        # ``get_status`` parity: no session-existence oracle. An attacker
        # without a valid ``poll_token`` gets 403 whether the session
        # exists or not; a legitimate caller with a poll_token that
        # predates cleanup also gets 403, which is harmless for the
        # fire-and-forget unmount cancel (the caller ``.catch``es it).
        if row is None:
            raise InvalidPollTokenError("invalid poll_token")
        _verify_poll_token(row, poll_token)
        if row.state in ("connected", "failed", "expired"):
            return
        await self._mark_terminal(session_id, "failed", "user cancelled", error_code="cancelled")

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
        except Exception as exc:
            # Any exchange failure ⇒ terminal-failed; the human's popup will
            # observe the transition on the next status poll.
            await self._mark_terminal(
                row.id, "failed", str(exc), error_code="token_exchange_failed"
            )
            return StatusResult(status="failed", error_code="token_exchange_failed")

        return await self._finalise_connected(row, handler, tokens)
