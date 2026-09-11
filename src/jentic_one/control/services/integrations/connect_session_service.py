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
from jentic_one.control.repos import CredentialRepository, OAuthTokenRepository
from jentic_one.control.repos.agent_credential_permission_repo import (
    AgentCredentialPermissionRepository,
)
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.services.integrations import identity_echo
from jentic_one.control.services.integrations.errors import (
    ConfirmationForbiddenError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    NoOpForFlowError,
    ScopeValidationError,
    SessionNotFoundError,
)
from jentic_one.control.services.integrations.flow_handlers import (
    AuthCodeFlowHandler,
    AuthFlowHandler,
    DeviceFlowHandler,
    handler_for,
)
from jentic_one.control.services.integrations.flow_handlers.base import SuccessTokens
from jentic_one.control.services.vendors.service import (
    ResolvedScope,
    VendorRegistryService,
)
from jentic_one.shared.catalog import CatalogAutoImportProtocol
from jentic_one.shared.context import Context
from jentic_one.shared.models.api_identity import canonical_credential_scope

_logger = structlog.get_logger(__name__)


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


@dataclass(slots=True, frozen=True)
class DeviceFlowConfirmResult:
    """RFC 8628 confirm outcome — user_code + verification_uri."""

    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    poll_interval_seconds: int | None = None
    kind: str = "device_flow"


@dataclass(slots=True, frozen=True)
class AuthCodeConfirmResult:
    """Authorization-code confirm outcome — client redirects to authorize_url."""

    authorize_url: str
    kind: str = "authorization_code"


ConfirmResult = DeviceFlowConfirmResult | AuthCodeConfirmResult


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


def _forbid_self_confirm(row: ConnectSession, caller_actor_type: str) -> None:
    """Agent-initiated sessions must be confirmed by a human on the review page.

    `caller_actor_type` is one of AGENT/USER/SERVICE_ACCOUNT — derived from the
    caller's identity, not from the payload.
    """
    initiator_is_agent = row.initiator_actor_id.startswith("agnt_")
    caller_is_agent = caller_actor_type.upper() == "AGENT"
    if initiator_is_agent and caller_is_agent:
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
        agent_id: str,
        initiator_actor_id: str,
        requested_scopes: list[str] | None = None,
        preferred_flow: str | None = None,
        reason: str | None = None,
    ) -> CreatedSession:
        """Create a pending session + upfront credential row.

        Actor-type checks live in the router (agent callers have `agent_id`
        forced to their own identity; user callers must supply it). The
        service takes both fields as-given.
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
        # per-operation identity check that fires under toolkit-mediated calls.
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
                name=f"{entry.display_name} ({agent_id})",
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

    async def get_review_data(self, session_id: str) -> ReviewData:
        """Return everything the review page needs to render.

        The scope list is the union of the vendor's catalog with the
        initiator's as-requested list — flagged so the UI can highlight
        write scopes the agent asked for. ``requested_scopes`` lives on the
        session row itself (flow-agnostic), so this method never needs to
        reach into a flow-specific aux table.
        """
        async with self._ctx.control_db.session() as session:
            row = await ConnectSessionRepository.get_by_id(session, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)

        entry = self._vendors.get(row.vendor)
        resolved = self._vendors.merge_scopes(row.vendor, row.requested_scopes or [])
        return ReviewData(
            session_id=row.id,
            state=row.state,
            vendor_key=row.vendor,
            vendor_display_name=entry.display_name,
            resolved_flow=row.resolved_flow,
            reason=row.reason,
            requested_by_actor_id=row.initiator_actor_id,
            scopes=[_scope_view(s) for s in resolved],
        )

    # ---- confirm ----------------------------------------------------------

    async def confirm(
        self,
        session_id: str,
        *,
        confirmed_scopes: list[str],
        permission_rules: list[dict[str, str]],
        caller_actor_id: str,
        caller_actor_type: str,
    ) -> ConfirmResult:
        """Confirm scopes + permissions and kick off the vendor-side flow."""
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)

        _forbid_self_confirm(row, caller_actor_type)
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

        # The handler owns the vendor conversation + any flow-specific
        # transient-state write (device_code + expires_at for RFC 8628; the
        # signed state token for auth-code). We only own the flow-agnostic
        # state machine + permission-rule capture below.
        challenge = await handler.begin(row, flow=flow, confirmed_scopes=confirmed_scopes)

        async with self._ctx.control_db.transaction() as session:
            # Persist proposed permission rules against the (agent, credential)
            # pair. Broker won't read this yet (theme-5 pending), but the row
            # is the durable capture of what the human approved.
            await AgentCredentialPermissionRepository.upsert(
                session,
                agent_id=row.agent_id,
                credential_id=row.credential_id,
                rules=permission_rules,
                created_by=caller_actor_id,
            )
            await ConnectSessionRepository.update_fields(session, row.id, state="polling")

        _logger.info(
            "connect_session.confirmed",
            session_id=row.id,
            vendor=row.vendor,
            resolved_flow=row.resolved_flow,
            confirmed_scopes=confirmed_scopes,
            rules_count=len(permission_rules),
        )
        if challenge.kind == "device_flow":
            return DeviceFlowConfirmResult(
                user_code=challenge.user_code,
                verification_uri=challenge.verification_uri,
                verification_uri_complete=challenge.verification_uri_complete,
                poll_interval_seconds=challenge.poll_interval_seconds,
            )
        return AuthCodeConfirmResult(authorize_url=challenge.authorize_url)

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
            if row is None:
                raise SessionNotFoundError(session_id)
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

        Two entrypoints write ``device_flow_credentials`` (the scanner's
        query target): the connect-session flow (has a wrapping
        ``ConnectSession``) and the raw-credential connect flow (no
        session). This method checks for a live session and dispatches to
        the matching advancement path — session mode updates the session
        state machine, credential mode advances ``credentials.state``
        directly. Both delegate the vendor conversation to
        ``DeviceFlowHandler.advance``.
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
        ``DeviceFlowHandler.advance``. Callback flows never reach here —
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
        if row.resolved_flow != DeviceFlowHandler.kind:
            # Callback flows advance via the OAuth callback route.
            return

        # Session TTL guard (flow-agnostic outer clock).
        session_age = (datetime.now(UTC) - row.created_at).total_seconds()
        if session_age > _SESSION_TTL_SECONDS:
            await self._mark_terminal(row.id, "expired", "session TTL exceeded")
            return

        handler = DeviceFlowHandler(self._ctx)
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
        row directly. Shares ``DeviceFlowHandler.advance`` verbatim with
        the session path.
        """
        async with self._ctx.control_db.session() as read_session:
            credential = await CredentialRepository.get_by_id(read_session, credential_id)
        if credential is None:
            return
        if credential.state != "pending":
            # Terminal (connected/failed) — nothing to advance.
            return

        handler = DeviceFlowHandler(self._ctx)
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
            handler = DeviceFlowHandler(self._ctx)
            await handler.on_finalise(session, credential_id=credential_id)
        _logger.info(
            "credential.device_flow.terminal",
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
        # invariant enforced by tests/arch).
        if credential.created_by is None:
            raise RuntimeError(
                f"credential {credential.id!r} has no created_by — "
                "cannot finalise a connect flow without an initiator identity"
            )
        await self._write_finalise(
            credential_id=credential.id,
            handler=handler,
            tokens=tokens,
            connected_as=None,
            created_by=credential.created_by,
            close_session_id=None,
        )

        _logger.info(
            "credential.device_flow.connected",
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
            await OAuthTokenRepository.create(
                session,
                credential_id=credential_id,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                expires_at=expires_at,
                scope=scope_to_persist,
                created_by=created_by,
            )
            await handler.on_finalise(session, credential_id=credential_id)
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is not None:
                credential.state = "connected"
                if connected_as is not None:
                    credential.provider_account_ref = connected_as
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
    ) -> None:
        async with self._ctx.control_db.transaction() as session:
            row = await ConnectSessionRepository.get_by_id(session, session_id)
            if row is None:
                return
            await ConnectSessionRepository.update_fields(
                session,
                session_id,
                state=state,
                error_code=error_code,
                error_detail=detail,
            )
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
            if credential is not None and credential.state == "pending":
                credential.state = "failed"
                await session.flush()

    # ---- redirect-based (auth-code / MCP) completion ----------------------

    async def mark_terminal_from_callback(self, session_id: str, error: str) -> None:
        """Mark a session ``failed`` from a callback landing that carried no
        usable code (vendor returned ``error`` or dropped ``code`` entirely).

        Public-surface wrapper around ``_mark_terminal`` so the callback
        router doesn't need to reach into a private method.
        """
        await self._mark_terminal(session_id, "failed", error, error_code="callback_error")

    async def complete_from_callback(
        self,
        *,
        session_id: str,
        code: str,
    ) -> StatusResult:
        """Complete a connect session from an OAuth callback landing.

        The callback route decodes + verifies the state JWT and consumes its
        nonce; it then hands us the session id + authorization code. We
        resolve the handler, exchange the code, and run the shared finalise.

        Raises ``NoOpForFlowError`` if the session's resolved flow doesn't
        support a callback path (device flow) — a defensive guard the router
        should never trip, since only auth-code state JWTs carry a ``sid``.
        """
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)

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
