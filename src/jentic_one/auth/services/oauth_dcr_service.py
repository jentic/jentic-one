"""Anonymous OAuth-client Dynamic Client Registration service (RFC 7591 subset).

The anonymous-DCR design (D1/D6/D8/D9): the front door behind ``POST
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

#: RFC 7591 grant types this door accepts. Anything else is rejected —
#: notably ``client_credentials`` and the jwt-bearer grant, which belong to
#: confidential clients and agents respectively.
_ALLOWED_GRANT_TYPES: frozenset[str] = frozenset({"authorization_code", "refresh_token"})

#: The auth-code flow is the only supported response type (RFC 7591 default).
_ALLOWED_RESPONSE_TYPES: frozenset[str] = frozenset({"code"})

#: Audit/event attribution for anonymous registrations. The design says
#: "actor = system", but the "system" actor notion has been removed from the
#: codebase (tests/arch/test_no_system_actor.py) — mirror the agent DCR
#: endpoint (registration_service.py), which attributes anonymous
#: registrations to the DCR surface itself.
_DCR_ACTOR = "dcr"


@dataclass(frozen=True, slots=True)
class DcrRegisterResult:
    """Result of an anonymous OAuth-client registration.

    ``created`` is False when a dedupe key matched an existing DCR row —
    (``software_id`` + exact redirect-URI set), or for software_id-less
    registrations (``client_name`` + exact redirect-URI set) — and the router
    answers 200 rather than 201. Metadata fields echo the *request's*
    validated values, never the stored row's live (possibly admin-edited)
    state; only ``client_id`` and ``client_id_issued_at`` come from the row.
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
    """Cap the client-claimed ``scope`` to the MCP tool-scope set.

    A DCR client's ``allowed_scopes`` ceiling is never unrestricted: no
    ``scope`` claim means the full MCP tool-scope set, and a claim is
    intersected with it (unknown/privileged scopes are silently dropped, per
    RFC 7591's ignore-don't-reject posture for metadata the server curtails).

    A claim with **zero** overlap is rejected rather than stored: an empty
    ceiling ``[]`` is falsy, and the admin view layer collapses it to ``None``
    — the platform-client "no allowlist" sentinel — which would skip the
    /authorize scope check entirely and make the client *unrestricted*, the
    exact opposite of the scope ceiling. Never store ``[]``.
    """
    if scope is None or not scope.strip():
        return sorted(MCP_TOOL_SCOPES)
    requested = {s for s in scope.split() if s}
    capped = sorted(requested & MCP_TOOL_SCOPES)
    if not capped:
        raise InvalidClientMetadataError(
            "scope has no overlap with the scopes this server grants to "
            f"OAuth clients (supported: {' '.join(sorted(MCP_TOOL_SCOPES))})"
        )
    return capped


def _validate_metadata(
    *,
    redirect_uris: list[str],
    token_endpoint_auth_method: str | None,
    grant_types: list[str] | None,
    response_types: list[str] | None,
) -> None:
    """Reject unsupported RFC 7591 metadata (only the accepted subset passes)."""
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
        # RFC 8252 §7.1: native apps (this door's clientele) may claim a
        # private-use redirect scheme; PKCE (S256, mandatory on the public-
        # client flow) is the compensating control. The admin door keeps the
        # strict https-or-loopback-http default.
        _validate_redirect_uris(redirect_uris, allow_private_use_schemes=True)
    except InvalidInputError as exc:
        # The canonical validator is admin-tier; translate to the auth taxonomy
        # so the auth surface's error handler maps it (invalid_client_metadata).
        raise InvalidClientMetadataError(str(exc)) from exc
    if len(set(redirect_uris)) != len(redirect_uris):
        # The D8 dedupe key is the exact redirect-URI *set*; a duplicated
        # entry makes the claimed set ambiguous (and would let ["a","a"] vs
        # ["a"] mint two rows for one effective set), so it is malformed
        # metadata, not something to silently normalize.
        raise InvalidClientMetadataError("redirect_uris must not contain duplicates")


def _to_result(
    client: OAuthClient,
    *,
    created: bool,
    client_name: str,
    redirect_uris: list[str],
    grant_types: list[str] | None,
    allowed_scopes: list[str],
    software_id: str | None,
    software_version: str | None,
    application_type: str | None,
) -> DcrRegisterResult:
    """Build the RFC 7591 response from the *request's* validated metadata.

    Only ``client_id`` and ``client_id_issued_at`` come from the stored row.
    On a D8 dedupe hit the row may have been admin-edited since (renamed,
    scopes narrowed) — echoing its live state would leak those admin-side
    changes to any anonymous re-registrant, so the response discloses nothing
    beyond the ``client_id`` that the caller didn't already send.
    """
    return DcrRegisterResult(
        client_id=client.client_id,
        client_name=client_name,
        redirect_uris=list(redirect_uris),
        grant_types=sorted(set(grant_types)) if grant_types else sorted(_ALLOWED_GRANT_TYPES),
        scope=" ".join(allowed_scopes),
        software_id=software_id,
        software_version=software_version,
        application_type=application_type,
        client_id_issued_at=int(client.created_at.timestamp()),
        created=created,
    )


#: F6 dedupe-winner preference: a concurrent double-register (no unique
#: constraint on the D8 key) can leave multiple rows for one exact set, and
#: the admin may have approved the newer one. Prefer the row the client can
#: actually use; ties break oldest-first (the repo's stable ordering).
_APPROVAL_PREFERENCE: dict[str, int] = {
    OAuthClientApprovalStatus.APPROVED.value: 0,
    OAuthClientApprovalStatus.PENDING.value: 1,
    OAuthClientApprovalStatus.DENIED.value: 2,
}


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

        Dedupe (D8, extended per G13/#1251): an exact (``software_id`` +
        redirect-URI set) match returns the existing row's ``client_id`` —
        idempotent re-register, so a cached registration or a double-register
        race never bricks a client. Registrations *without* a ``software_id``
        fall back to the (``client_name`` + redirect-URI set) key against
        rows that also carry no ``software_id`` — otherwise clients that send
        no software identity (Cursor, mcp-remote) mint a fresh pending row on
        every awaiting-approval retry. Never dedupes on ``software_id`` or
        ``client_name`` alone, never across the two key spaces, and a dedupe
        hit never mutates the stored row (status, name, and redirect set are
        preserved verbatim) — it only writes an audit entry so anonymous
        re-attaches stay visible to admins.

        A falsy or whitespace-only ``software_id`` is normalized to ``None``
        *before* the key-space branch and the insert: ``""`` passes schema
        validation (empty-default serializers are common) but would otherwise
        take the fallback lookup (``IS NULL``) while storing ``""`` (NOT
        NULL) — a row in *neither* key space that re-opens the #1251 loop.
        Normalizing (rather than rejecting) follows RFC 7591's
        ignore-don't-reject posture for metadata the server curtails.
        """
        _validate_metadata(
            redirect_uris=redirect_uris,
            token_endpoint_auth_method=token_endpoint_auth_method,
            grant_types=grant_types,
            response_types=response_types,
        )
        if not client_name.strip():
            raise InvalidClientMetadataError("client_name is required")
        software_id = (software_id or "").strip() or None

        allowed_scopes = _cap_scopes(scope)
        auto_approve = self._ctx.config.server.mcp.oauth.auto_approve_clients
        requested_set = set(redirect_uris)
        fingerprint = redirect_uris_fingerprint(redirect_uris)
        name = client_name.strip()

        async def _write(session: AsyncSession) -> tuple[OAuthClient, bool]:
            # D8 dedupe via the (software_id, redirect_uris_fingerprint)
            # index; software_id-less registrations use the G13 fallback key
            # (name, fingerprint) against software_id-less rows only — the
            # two key spaces never cross-match. The fetched rows' exact URI
            # sets are re-checked because the fingerprint is a hash
            # (collision guard). Among multiple exact matches
            # (double-register race) prefer approved > pending > denied,
            # then oldest — `min` is stable and the repo returns rows
            # oldest-first.
            if software_id:
                candidates = await OAuthClientRepository.list_dcr_by_dedupe_key(
                    session, software_id, fingerprint
                )
            else:
                candidates = await OAuthClientRepository.list_dcr_by_name_dedupe_key(
                    session, name, fingerprint
                )
            matches = [
                candidate
                for candidate in candidates
                if set(candidate.redirect_uris) == requested_set
            ]
            if matches:
                winner = min(
                    matches,
                    key=lambda c: _APPROVAL_PREFERENCE.get(
                        c.approval_status, len(_APPROVAL_PREFERENCE)
                    ),
                )
                # F2: a re-attach must leave a forensic trace — the 200-dedupe
                # arm discloses an existing (possibly approved) client_id to
                # an anonymous caller, and a client bouncing off a denied row
                # retries with no other admin-visible signal. Audit row only,
                # deliberately no event: the whole point of the dedupe is
                # that retries stop spamming the approval queue.
                await record_audit(
                    session,
                    action=AuditAction.REGISTER,
                    target_type=AuditTargetType.OAUTH_CLIENT,
                    target_id=winner.id,
                    actor_type=_DCR_ACTOR,
                    actor_id=None,
                    after={
                        "client_id": winner.client_id,
                        "approval_status": winner.approval_status,
                    },
                    reason="anonymous DCR re-attach (dedupe hit)",
                    origin=Origin.MCP.value,
                )
                return winner, False

            approved = auto_approve
            client = await OAuthClientRepository.create(
                session,
                client_id=generate_client_id(),
                name=name,
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
        return _to_result(
            client,
            created=created,
            client_name=name,
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            allowed_scopes=allowed_scopes,
            software_id=software_id,
            software_version=software_version,
            application_type=application_type,
        )
