"""Anonymous OAuth-client Dynamic Client Registration service (RFC 7591 subset).

Phase-3a design §4.2 (D1/D6/D8/D9): the front door behind ``POST
/oauth-clients``. It writes **public** (secret-less, PKCE-only) rows into the
same ``oauth_clients`` registry the admin CRUD manages — always
``consent_model='agent'`` and ``registration_source='dcr'``. Rows land
``approval_status='pending'`` + ``active=false`` unless the D9 auto-approve
policy (``server.mcp.oauth.auto_approve_clients``) applies.

Distinct from :mod:`jentic_one.auth.services.registration_service`, which is
the *agent* DCR endpoint (``POST /register``) and stays byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos.oauth_client_repo import (
    OAuthClientRepository,
    redirect_uris_fingerprint,
)
from jentic_one.admin.services._support.tokens import generate_client_id
from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.oauth_client_service import _validate_redirect_uris
from jentic_one.auth.services.errors import InvalidClientMetadataError
from jentic_one.shared.audit import record_audit
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.oauth_clients import (
    OAuthClientApprovalStatus,
    OAuthConsentModel,
    OAuthRegistrationSource,
    TokenEndpointAuthMethod,
)
from jentic_one.shared.scopes import MCP_TOOL_SCOPES

#: RFC 7591 grant types this door accepts (§4.2). Anything else is rejected —
#: notably ``client_credentials`` and the jwt-bearer grant, which belong to
#: confidential clients and agents respectively.
_ALLOWED_GRANT_TYPES: frozenset[str] = frozenset({"authorization_code", "refresh_token"})

#: The auth-code flow is the only supported response type (RFC 7591 default).
_ALLOWED_RESPONSE_TYPES: frozenset[str] = frozenset({"code"})

#: Audit/event attribution for anonymous registrations. The design (§4.2) says
#: "actor = system", but the "system" actor notion has been removed from the
#: codebase (tests/arch/test_no_system_actor.py) — mirror the agent DCR
#: endpoint (registration_service.py), which attributes anonymous
#: registrations to the DCR surface itself.
_DCR_ACTOR = "dcr"


@dataclass(frozen=True, slots=True)
class DcrRegisterResult:
    """Result of an anonymous OAuth-client registration.

    ``created`` is False when the D8 dedupe key (``software_id`` + exact
    redirect-URI set) matched an existing row, whose metadata is returned
    instead — the router answers 200 rather than 201.
    """

    client_id: str
    client_name: str
    redirect_uris: list[str]
    grant_types: list[str]
    scope: str
    software_id: str | None
    software_version: str | None
    application_type: str | None
    client_id_issued_at: int
    created: bool


def _cap_scopes(scope: str | None) -> list[str]:
    """Cap the client-claimed ``scope`` to the MCP tool-scope set (§4.2).

    A DCR client's ``allowed_scopes`` ceiling is never unrestricted: no
    ``scope`` claim means the full MCP tool-scope set, and a claim is
    intersected with it (unknown/privileged scopes are silently dropped, per
    RFC 7591's ignore-don't-reject posture for metadata the server curtails).
    """
    if scope is None or not scope.strip():
        return sorted(MCP_TOOL_SCOPES)
    requested = {s for s in scope.split() if s}
    return sorted(requested & MCP_TOOL_SCOPES)


def _validate_metadata(
    *,
    redirect_uris: list[str],
    token_endpoint_auth_method: str | None,
    grant_types: list[str] | None,
    response_types: list[str] | None,
) -> None:
    """Reject unsupported RFC 7591 metadata (design §4.2 accepted subset)."""
    if (
        token_endpoint_auth_method is not None
        and token_endpoint_auth_method != TokenEndpointAuthMethod.NONE.value
    ):
        raise InvalidClientMetadataError(
            "this endpoint only registers public clients: "
            "token_endpoint_auth_method must be 'none' "
            "(confidential clients are admin-created)"
        )
    if grant_types:
        unsupported = set(grant_types) - _ALLOWED_GRANT_TYPES
        if unsupported:
            raise InvalidClientMetadataError(
                f"unsupported grant_types: {sorted(unsupported)} "
                f"(allowed: {sorted(_ALLOWED_GRANT_TYPES)})"
            )
    if response_types:
        unsupported = set(response_types) - _ALLOWED_RESPONSE_TYPES
        if unsupported:
            raise InvalidClientMetadataError(
                f"unsupported response_types: {sorted(unsupported)} (allowed: ['code'])"
            )
    try:
        _validate_redirect_uris(redirect_uris)
    except InvalidInputError as exc:
        # The canonical validator is admin-tier; translate to the auth taxonomy
        # so the auth surface's error handler maps it (invalid_client_metadata).
        raise InvalidClientMetadataError(str(exc)) from exc


def _to_result(
    client: OAuthClient,
    *,
    created: bool,
    software_version: str | None = None,
    application_type: str | None = None,
) -> DcrRegisterResult:
    return DcrRegisterResult(
        client_id=client.client_id,
        client_name=client.name,
        redirect_uris=list(client.redirect_uris),
        grant_types=sorted(_ALLOWED_GRANT_TYPES),
        scope=" ".join(client.allowed_scopes or []),
        software_id=client.software_id,
        software_version=software_version,
        application_type=application_type,
        client_id_issued_at=int(client.created_at.timestamp()),
        created=created,
    )


class OAuthDcrService:
    """Handles anonymous OAuth-client registration via the DCR front door."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def register(
        self,
        *,
        client_name: str,
        redirect_uris: list[str],
        token_endpoint_auth_method: str | None = None,
        grant_types: list[str] | None = None,
        response_types: list[str] | None = None,
        scope: str | None = None,
        software_id: str | None = None,
        software_version: str | None = None,
        application_type: str | None = None,
    ) -> DcrRegisterResult:
        """Register (or dedupe to) a public OAuth client row.

        Dedupe (D8): an exact (``software_id`` + redirect-URI set) match
        returns the existing row's ``client_id`` — idempotent re-register, so a
        cached registration or a double-register race never bricks a client.
        No ``software_id`` → no dedupe. Never dedupes on ``software_id`` alone.
        """
        _validate_metadata(
            redirect_uris=redirect_uris,
            token_endpoint_auth_method=token_endpoint_auth_method,
            grant_types=grant_types,
            response_types=response_types,
        )
        if not client_name.strip():
            raise InvalidClientMetadataError("client_name is required")

        allowed_scopes = _cap_scopes(scope)
        auto_approve = self._ctx.config.server.mcp.oauth.auto_approve_clients
        requested_set = set(redirect_uris)
        fingerprint = redirect_uris_fingerprint(redirect_uris)

        async def _write(session: AsyncSession) -> tuple[OAuthClient, bool]:
            if software_id:
                # D8 dedupe via the (software_id, redirect_uris_fingerprint)
                # index (§4.1); the fetched rows' exact URI sets are re-checked
                # because the fingerprint is a hash (collision guard).
                for candidate in await OAuthClientRepository.list_dcr_by_dedupe_key(
                    session, software_id, fingerprint
                ):
                    if set(candidate.redirect_uris) == requested_set:
                        return candidate, False

            approved = auto_approve
            client = await OAuthClientRepository.create(
                session,
                client_id=generate_client_id(),
                name=client_name.strip(),
                redirect_uris=redirect_uris,
                client_secret_hash=None,
                description=None,
                require_consent=True,
                allowed_scopes=allowed_scopes,
                token_endpoint_auth_method=TokenEndpointAuthMethod.NONE.value,
                consent_model=OAuthConsentModel.AGENT.value,
                registration_source=OAuthRegistrationSource.DCR.value,
                software_id=software_id,
                approval_status=(
                    OAuthClientApprovalStatus.APPROVED.value
                    if approved
                    else OAuthClientApprovalStatus.PENDING.value
                ),
                active=approved,
                created_by=_DCR_ACTOR,
            )
            await record_audit(
                session,
                action=AuditAction.REGISTER,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=client.id,
                actor_type=_DCR_ACTOR,
                actor_id=None,
                after={
                    "client_id": client.client_id,
                    "name": client.name,
                    "redirect_uris": redirect_uris,
                    "software_id": software_id,
                    "allowed_scopes": allowed_scopes,
                    "approval_status": client.approval_status,
                    "active": client.active,
                },
                reason="anonymous dynamic client registration",
                origin=Origin.MCP.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.OAUTH_CLIENT_REGISTERED,
                severity=EventSeverity.INFO,
                summary=(
                    f"OAuth client '{client.name}' self-registered"
                    + (
                        " and was auto-approved"
                        if approved
                        else " and awaits administrator approval"
                    )
                ),
                # Pending rows need an admin decision (approve/deny), so
                # surface an actionable alert; auto-approved rows (D9) are a
                # plain notification. `oauth_client_id` in data powers both the
                # UI deep-link and the settle-on-decision match.
                requires_action=not approved,
                data={
                    "oauth_client_id": client.id,
                    "client_id": client.client_id,
                    "client_name": client.name,
                    "approval_status": client.approval_status,
                    "software_id": software_id,
                },
                created_by=_DCR_ACTOR,
            )
            return client, True

        # run_in_transaction: like agent DCR, anonymous registration contends
        # on the admin DB with worker polls and token mints — retry transient
        # SQLite write-locks instead of surfacing a 500 on first contention.
        client, created = await self._ctx.admin_db.run_in_transaction(_write)
        # software_version/application_type are accepted (RFC 7591 native-app
        # fix, §2) but not persisted — echo them back only on a fresh create;
        # a dedupe hit returns the stored row's metadata.
        return _to_result(
            client,
            created=created,
            software_version=software_version if created else None,
            application_type=application_type if created else None,
        )
