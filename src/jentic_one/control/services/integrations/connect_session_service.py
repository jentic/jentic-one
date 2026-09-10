"""ConnectSessionService — orchestrates the agent-driven integration flow.

Owns the state machine on `connect_sessions`; delegates flow-specific vendor
calls to `device_flow.py` and identity resolution to `identity_echo.py`.

Phase 1 wires up device flow only. Authorization-code flow uses the existing
`DirectOAuth2Provider` path elsewhere.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_flow_credentials import DeviceFlowCredential
from jentic_one.control.repos import CredentialRepository, OAuthTokenRepository
from jentic_one.control.repos.agent_credential_permission_repo import (
    AgentCredentialPermissionRepository,
)
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.device_flow_credential_repo import (
    DeviceFlowCredentialRepository,
)
from jentic_one.control.services.integrations import device_flow, identity_echo
from jentic_one.control.services.integrations.errors import (
    ConfirmationForbiddenError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    NoOpForFlowError,
    ScopeValidationError,
    SessionNotFoundError,
)
from jentic_one.control.services.vendors.service import (
    ResolvedScope,
    VendorRegistryService,
)
from jentic_one.shared.catalog import CatalogAutoImportProtocol
from jentic_one.shared.config import VendorDeviceFlowConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models.api_identity import canonical_credential_scope
from jentic_one.shared.models.credentials import StoredCredentialType

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
class ConfirmResult:
    user_code: str | None
    verification_uri: str | None
    verification_uri_complete: str | None
    poll_interval_seconds: int | None


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


def _should_poll_now(dfc: DeviceFlowCredential | None) -> bool:
    """Rate-limit the vendor poll to at most once per `poll_interval_seconds`."""
    if dfc is None or dfc.poll_interval_seconds is None:
        return True
    if dfc.last_polled_at is None:
        return True
    elapsed = (datetime.now(UTC) - dfc.last_polled_at).total_seconds()
    return elapsed >= dfc.poll_interval_seconds


def _terminal_status(
    row: ConnectSession,
    credential: Credential | None,
    dfc: DeviceFlowCredential | None,
) -> StatusResult:
    """Serialise a terminal session back to a StatusResult."""
    if row.state == "connected":
        return StatusResult(
            status="connected",
            connected_as=row.connected_as,
            credential_id=row.credential_id,
            bound_scopes=(dfc.granted_scopes if dfc else None),
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

        if not isinstance(flow, VendorDeviceFlowConfig):
            # Phase 1 only wires device flow. Other flow kinds can still be
            # inspected via /vendors/{vendor}/auth-capabilities.
            raise NoOpForFlowError(flow.kind)

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
                type=StoredCredentialType.OAUTH2_DEVICE_CODE.value,
                name=f"{entry.display_name} ({agent_id})",
                api_vendor=api_scope.vendor,
                api_name=api_scope.name,
                catalog_api_id=entry.vendor,
                created_by=initiator_actor_id,
                provider="device_flow",
                state="pending",
            )
            await DeviceFlowCredentialRepository.create(
                session,
                credential_id=credential.id,
                client_id=flow.client_id,
                token_url=flow.token_endpoint,
                authorization_endpoint=flow.authorization_endpoint,
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

        The scope list is the union of the vendor's catalog with the initiator's
        as-requested list — flagged so the UI can highlight write scopes the
        agent asked for.
        """
        async with self._ctx.control_db.session() as session:
            row = await ConnectSessionRepository.get_by_id(session, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)
            dfc = await DeviceFlowCredentialRepository.get_by_credential(session, row.credential_id)

        entry = self._vendors.get(row.vendor)
        requested = dfc.requested_scopes if dfc else []
        resolved = self._vendors.merge_scopes(row.vendor, requested)
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
        if not isinstance(flow, VendorDeviceFlowConfig):
            raise NoOpForFlowError(flow.kind)

        unknown = self._vendors.validate_scopes(row.vendor, confirmed_scopes)
        if unknown:
            raise ScopeValidationError(unknown)

        # Talk to the vendor OUTSIDE the DB transaction — network latency has
        # no business holding a control-DB row lock.
        begin = await device_flow.begin_device_flow(
            authorization_endpoint=flow.authorization_endpoint,
            client_id=flow.client_id,
            scopes=confirmed_scopes,
        )
        encrypted_device_code = self._ctx.encryption.encrypt(begin.device_code)
        expires_at = datetime.now(UTC) + timedelta(seconds=begin.expires_in)

        async with self._ctx.control_db.transaction() as session:
            await DeviceFlowCredentialRepository.set_transient_state(
                session,
                row.credential_id,
                encrypted_device_code=encrypted_device_code,
                user_code=begin.user_code,
                verification_uri=begin.verification_uri,
                verification_uri_complete=begin.verification_uri_complete,
                poll_interval_seconds=begin.interval,
                device_code_expires_at=expires_at,
                granted_scopes=confirmed_scopes,
            )
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
            confirmed_scopes=confirmed_scopes,
            rules_count=len(permission_rules),
        )
        return ConfirmResult(
            user_code=begin.user_code,
            verification_uri=begin.verification_uri,
            verification_uri_complete=begin.verification_uri_complete,
            poll_interval_seconds=begin.interval,
        )

    # ---- status / poll ----------------------------------------------------

    async def poll_status(
        self,
        session_id: str,
        *,
        poll_token: str,
    ) -> StatusResult:
        """Return current status, lazy-polling the vendor at most once per interval."""
        async with self._ctx.control_db.session() as read_session:
            row = await ConnectSessionRepository.get_by_id(read_session, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)
            _verify_poll_token(row, poll_token)
            dfc = await DeviceFlowCredentialRepository.get_by_credential(
                read_session, row.credential_id
            )
            credential = await CredentialRepository.get_by_id(read_session, row.credential_id)

        # Terminal states are immutable — return them without touching the vendor.
        if row.state in ("connected", "expired", "failed"):
            return _terminal_status(row, credential, dfc)

        if row.state != "polling":
            # `created` = confirm not called yet; report as pending so the
            # agent knows to wait on the human.
            return StatusResult(status="pending")

        # Session TTL guard.
        session_age = (datetime.now(UTC) - row.created_at).total_seconds()
        if session_age > _SESSION_TTL_SECONDS:
            await self._mark_terminal(row.id, "expired", "session TTL exceeded")
            return StatusResult(status="expired", error_code="session_expired")

        # Device-code TTL guard (vendor-supplied).
        if (
            dfc is not None
            and dfc.device_code_expires_at is not None
            and datetime.now(UTC) >= dfc.device_code_expires_at
        ):
            await self._mark_terminal(row.id, "expired", "device_code expired")
            return StatusResult(status="expired", error_code="device_code_expired")

        # Lazy poll: skip the vendor call if we've polled within the interval.
        if not _should_poll_now(dfc):
            return StatusResult(status="pending")

        return await self._poll_vendor_and_advance(row, dfc)

    async def _poll_vendor_and_advance(
        self,
        row: ConnectSession,
        dfc: DeviceFlowCredential | None,
    ) -> StatusResult:
        """Do one vendor poll and advance the state machine accordingly."""
        assert dfc is not None
        assert dfc.encrypted_device_code is not None

        device_code = self._ctx.encryption.decrypt(dfc.encrypted_device_code)
        result = await device_flow.poll_device_flow(
            token_endpoint=dfc.token_url,
            client_id=dfc.client_id,
            device_code=device_code,
        )

        now = datetime.now(UTC)
        async with self._ctx.control_db.transaction() as session:
            await DeviceFlowCredentialRepository.mark_polled(session, row.credential_id, now)

        if result.status == "pending":
            return StatusResult(status="pending")
        if result.status == "slow_down":
            # RFC 8628 §3.5 — widen the interval by 5s for future polls.
            async with self._ctx.control_db.transaction() as session:
                await DeviceFlowCredentialRepository.update_fields(
                    session,
                    row.credential_id,
                    poll_interval_seconds=(dfc.poll_interval_seconds or 5) + 5,
                )
            return StatusResult(status="pending")
        if result.status == "denied":
            await self._mark_terminal(row.id, "failed", "access_denied", error_code="access_denied")
            return StatusResult(status="failed", error_code="access_denied")
        if result.status == "expired":
            await self._mark_terminal(
                row.id, "expired", "expired_token", error_code="expired_token"
            )
            return StatusResult(status="expired", error_code="expired_token")

        # success
        assert result.access_token is not None
        return await self._finalise_connected(row, dfc, result)

    async def _finalise_connected(
        self,
        row: ConnectSession,
        dfc: DeviceFlowCredential,
        result: device_flow.PollResult,
    ) -> StatusResult:
        """Vault token, echo identity, mark credential connected, close the session."""
        assert result.access_token is not None
        entry = self._vendors.get(row.vendor)

        # Identity echo — outside the DB transaction (external HTTP).
        try:
            echo = await identity_echo.echo_identity(
                probe=entry.identity_probe,
                access_token=result.access_token,
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

        expires_at = (
            datetime.now(UTC) + timedelta(seconds=result.expires_in) if result.expires_in else None
        )
        encrypted_access = self._ctx.encryption.encrypt(result.access_token)
        encrypted_refresh = (
            self._ctx.encryption.encrypt(result.refresh_token) if result.refresh_token else None
        )

        async with self._ctx.control_db.transaction() as session:
            await OAuthTokenRepository.create(
                session,
                credential_id=row.credential_id,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                expires_at=expires_at,
                scope=result.scope,
                created_by=row.initiator_actor_id,
            )
            await DeviceFlowCredentialRepository.clear_transient(session, row.credential_id)
            credential = await CredentialRepository.get_by_id(session, row.credential_id)
            if credential is not None:
                credential.state = "connected"
                credential.provider_account_ref = echo.display
                await session.flush()
            await ConnectSessionRepository.update_fields(
                session,
                row.id,
                state="connected",
                connected_as=connected_as,
            )

        _logger.info(
            "connect_session.connected",
            session_id=row.id,
            credential_id=row.credential_id,
            connected_as=connected_as,
        )

        # A connected credential is only useful to the broker once the vendor's
        # OpenAPI is registered — the broker's URL→operation discovery is a
        # registry-DB read and returns 404 for unregistered upstreams. Trigger a
        # best-effort import so the operator doesn't have to remember it as a
        # second step. The importer is idempotent (skips when already
        # registered), best-effort (never raises — the credential is still
        # valid without the import), and runs after the credential is committed
        # so a failed import can't roll it back.
        if self._catalog_auto_importer is not None:
            await self._catalog_auto_importer.ensure_imported(
                api_id=entry.vendor,
                initiator_actor_id=row.initiator_actor_id,
            )

        return StatusResult(
            status="connected",
            connected_as=connected_as,
            credential_id=row.credential_id,
            bound_scopes=dfc.granted_scopes or [],
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
