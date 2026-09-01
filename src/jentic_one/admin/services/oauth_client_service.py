"""OAuth client service — manage registered third-party applications."""

from __future__ import annotations

import asyncio
import functools
from functools import partial
from urllib.parse import urlparse

from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.admin.services._support.passwords import hash_password, verify_password
from jentic_one.admin.services._support.tokens import generate_client_id, generate_client_secret
from jentic_one.admin.services.errors import (
    ConflictError,
    InvalidInputError,
    OAuthClientNotFoundError,
)
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientCreateResult, OAuthClientView
from jentic_one.shared.audit import record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.oauth_clients import (
    OAuthClientApprovalStatus,
    OAuthConsentModel,
    TokenEndpointAuthMethod,
)

_MAX_REDIRECT_URIS = 20
_MAX_REDIRECT_URI_LENGTH = 2048


@functools.cache
def _dummy_argon2_hash() -> str:
    """A cached argon2id hash used as the timing-equalizer verify target.

    The first call computes a real argon2id hash (~64 MiB / tens of ms)
    synchronously on the event-loop thread; every later call returns the
    cached string. This is a deliberate trade-off: the cost is paid once per
    process, only when a rejection path first needs the equalizer, and
    keeping the computation lazy avoids taxing startup (and every test
    session) with an argon2 run. Callers verify *against* this hash via
    ``_verify_password_async``, which does run in the executor.
    """
    return hash_password("dummy-timing-equalizer")


def _validate_redirect_uris(uris: list[str]) -> None:
    """Validate that all redirect URIs are well-formed HTTPS URLs (http only for localhost)."""
    if not uris:
        raise InvalidInputError("at least one redirect_uri is required")
    if len(uris) > _MAX_REDIRECT_URIS:
        raise InvalidInputError(f"at most {_MAX_REDIRECT_URIS} redirect_uris allowed")

    for uri in uris:
        if len(uri) > _MAX_REDIRECT_URI_LENGTH:
            raise InvalidInputError(
                f"redirect_uri exceeds maximum length of {_MAX_REDIRECT_URI_LENGTH}"
            )
        parsed = urlparse(uri)
        if not parsed.scheme or not parsed.netloc:
            raise InvalidInputError(f"invalid redirect_uri: {uri}")
        if parsed.fragment:
            raise InvalidInputError(f"redirect_uri must not contain a fragment component: {uri}")
        if parsed.scheme not in ("https", "http"):
            raise InvalidInputError(f"redirect_uri must use https or http: {uri}")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise InvalidInputError(f"http redirect_uri only allowed for localhost: {uri}")


def _is_approved(client: OAuthClient) -> bool:
    """The D7 approval gate: only ``approved`` rows may enter OAuth flows."""
    return client.approval_status == OAuthClientApprovalStatus.APPROVED.value


def _snapshot(client: OAuthClient) -> dict[str, object]:
    """Capture the mutable fields of an OAuth client for audit ``before`` snapshots."""
    return {
        "name": client.name,
        "description": client.description,
        "redirect_uris": list(client.redirect_uris),
        "active": client.active,
        "require_consent": client.require_consent,
        "allowed_scopes": (
            list(client.allowed_scopes) if client.allowed_scopes is not None else None
        ),
        "approval_status": client.approval_status,
    }


def _to_view(client: OAuthClient) -> OAuthClientView:
    return OAuthClientView(
        id=client.id,
        client_id=client.client_id,
        name=client.name,
        description=client.description,
        redirect_uris=list(client.redirect_uris),
        allowed_scopes=list(client.allowed_scopes) if client.allowed_scopes else None,
        active=client.active,
        require_consent=client.require_consent,
        token_endpoint_auth_method=client.token_endpoint_auth_method,
        consent_model=client.consent_model,
        registration_source=client.registration_source,
        software_id=client.software_id,
        approval_status=client.approval_status,
        created_at=client.created_at,
        updated_at=client.updated_at,
        created_by=client.created_by,
    )


async def _hash_password_async(plain: str) -> str:
    """Run argon2id hashing off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, hash_password, plain)


async def _verify_password_async(plain: str, hashed: str) -> bool:
    """Run argon2id verification off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(
        None, partial(verify_password, plain, hashed)
    )


class OAuthClientService:
    """Manages OAuth client registration for third-party applications."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create(
        self,
        *,
        name: str,
        redirect_uris: list[str],
        description: str | None = None,
        require_consent: bool = True,
        allowed_scopes: list[str] | None = None,
        token_endpoint_auth_method: str = TokenEndpointAuthMethod.CLIENT_SECRET_BASIC.value,
        consent_model: str = OAuthConsentModel.USER.value,
        identity: Identity,
    ) -> OAuthClientCreateResult:
        """Register a new OAuth client. Returns the one-time plaintext secret.

        ``token_endpoint_auth_method='none'`` creates a public (secret-less)
        client: no secret is generated or stored and ``client_secret`` in the
        result is None (D5). Admin-created rows always land ``approved`` +
        ``active`` — the approval queue only gates DCR registrations (D7).
        """
        _validate_redirect_uris(redirect_uris)
        if token_endpoint_auth_method not in (
            TokenEndpointAuthMethod.CLIENT_SECRET_BASIC.value,
            TokenEndpointAuthMethod.NONE.value,
        ):
            raise InvalidInputError(
                f"unsupported token_endpoint_auth_method: {token_endpoint_auth_method}"
            )
        if consent_model not in (
            OAuthConsentModel.USER.value,
            OAuthConsentModel.AGENT.value,
        ):
            raise InvalidInputError(f"unsupported consent_model: {consent_model}")

        is_public = token_endpoint_auth_method == TokenEndpointAuthMethod.NONE.value
        client_id = generate_client_id()
        client_secret: str | None = None
        secret_hash: str | None = None
        if not is_public:
            client_secret = generate_client_secret()
            secret_hash = await _hash_password_async(client_secret)

        async with self._ctx.admin_db.transaction() as session:
            client = await OAuthClientRepository.create(
                session,
                client_id=client_id,
                name=name,
                redirect_uris=redirect_uris,
                client_secret_hash=secret_hash,
                description=description,
                require_consent=require_consent,
                allowed_scopes=allowed_scopes,
                token_endpoint_auth_method=token_endpoint_auth_method,
                consent_model=consent_model,
                created_by=identity.sub,
            )
            await record_audit(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=client.id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={
                    "name": name,
                    "client_id": client.client_id,
                    "description": description,
                    "redirect_uris": redirect_uris,
                    "require_consent": require_consent,
                    "allowed_scopes": allowed_scopes,
                    "token_endpoint_auth_method": token_endpoint_auth_method,
                    "consent_model": consent_model,
                },
                origin=identity.origin.value,
            )
            view = _to_view(client)
            return OAuthClientCreateResult(**view.model_dump(), client_secret=client_secret)

    async def get(self, id: str) -> OAuthClientView:
        """Get an OAuth client by internal ID."""
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_id(session, id)
        if client is None:
            raise OAuthClientNotFoundError(id)
        return _to_view(client)

    async def get_by_client_id(self, client_id: str) -> OAuthClientView | None:
        """Get an OAuth client by its public client_id. Returns None if not found."""
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_client_id(session, client_id)
        if client is None:
            return None
        return _to_view(client)

    async def list_all(
        self,
        *,
        include_inactive: bool = False,
        approval_status: str | None = None,
    ) -> list[OAuthClientView]:
        """List all OAuth clients, optionally filtered by approval status.

        Filtering on ``pending`` or ``denied`` implies ``include_inactive``:
        those rows are ``active=false`` by construction (D7), so composing the
        default active-only filter with the approval-queue query would
        silently return ``[]``.
        """
        if approval_status is not None and approval_status not in (
            OAuthClientApprovalStatus.PENDING.value,
            OAuthClientApprovalStatus.APPROVED.value,
            OAuthClientApprovalStatus.DENIED.value,
        ):
            raise InvalidInputError(f"unsupported approval_status filter: {approval_status}")
        if approval_status in (
            OAuthClientApprovalStatus.PENDING.value,
            OAuthClientApprovalStatus.DENIED.value,
        ):
            include_inactive = True
        async with self._ctx.admin_db.session() as session:
            clients = await OAuthClientRepository.list_all(
                session,
                include_inactive=include_inactive,
                approval_status=approval_status,
            )
        return [_to_view(c) for c in clients]

    async def update(
        self,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        redirect_uris: list[str] | None = None,
        active: bool | None = None,
        require_consent: bool | None = None,
        allowed_scopes: list[str] | None = None,
        identity: Identity,
    ) -> OAuthClientView:
        """Update an OAuth client.

        ``allowed_scopes=["*"]`` is the reset sentinel — it clears the column
        to NULL (unrestricted). ``allowed_scopes=None`` means no change;
        ``allowed_scopes=[]`` denies all non-OIDC scopes.

        ``active=true`` is rejected on a row whose ``approval_status`` is not
        ``approved`` (D7): the ``:approve``/``:deny`` verbs are the only way
        to change approval state, and PATCH must not manufacture a
        ``denied+active`` (or ``pending+active``) row the state machine does
        not contain. ``active=false`` is always allowed (fail-closed).
        """
        if redirect_uris is not None:
            _validate_redirect_uris(redirect_uris)

        reset_allowed_scopes = allowed_scopes == ["*"]
        applied_allowed_scopes = None if reset_allowed_scopes else allowed_scopes

        async with self._ctx.admin_db.transaction() as session:
            existing = await OAuthClientRepository.get_by_id(session, id)
            if existing is None:
                raise OAuthClientNotFoundError(id)

            if active is True and not _is_approved(existing):
                raise ConflictError(
                    f"OAuth client '{id}' cannot be activated while its approval_status "
                    f"is '{existing.approval_status}' — use the :approve verb"
                )

            before_snapshot = _snapshot(existing)

            client = await OAuthClientRepository.update(
                session,
                id,
                name=name,
                description=description,
                redirect_uris=redirect_uris,
                active=active,
                require_consent=require_consent,
                allowed_scopes=applied_allowed_scopes,
                reset_allowed_scopes=reset_allowed_scopes,
            )
            if client is None:
                raise OAuthClientNotFoundError(id)

            changes: dict[str, object] = {}
            if name is not None:
                changes["name"] = name
            if description is not None:
                changes["description"] = description
            if redirect_uris is not None:
                changes["redirect_uris"] = redirect_uris
            if active is not None:
                changes["active"] = active
            if require_consent is not None:
                changes["require_consent"] = require_consent
            if reset_allowed_scopes:
                changes["allowed_scopes"] = None
            elif allowed_scopes is not None:
                changes["allowed_scopes"] = allowed_scopes

            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before=before_snapshot,
                after=changes,
                origin=identity.origin.value,
            )
            return _to_view(client)

    async def deactivate(self, id: str, *, identity: Identity) -> None:
        """Soft-delete an OAuth client by setting active=False."""
        async with self._ctx.admin_db.transaction() as session:
            existing = await OAuthClientRepository.get_by_id(session, id)
            if existing is None:
                raise OAuthClientNotFoundError(id)

            before_snapshot = _snapshot(existing)

            success = await OAuthClientRepository.deactivate(session, id)
            if not success:
                raise OAuthClientNotFoundError(id)

            await record_audit(
                session,
                action=AuditAction.DELETE,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before=before_snapshot,
                after={"active": False},
                origin=identity.origin.value,
            )

    async def approve(self, id: str, *, identity: Identity) -> OAuthClientView:
        """Approve an OAuth client: ``approval_status='approved'`` + ``active=true`` (D7).

        Also the recovery path for a denied client — deny is reversible and
        rows are never deleted, so a cached client_id becomes valid again.

        Note that ``:approve`` unconditionally re-arms ``active`` (D7's
        ":approve sets both"): approving an already-approved but deactivated
        client re-enables it. Don't use ``:approve`` as an idempotent no-op on
        a client that was deliberately killed via ``PATCH active=false`` /
        DELETE — that would flip the kill switch back on.
        """
        return await self._set_approval(
            id,
            approval_status=OAuthClientApprovalStatus.APPROVED.value,
            active=True,
            action=AuditAction.APPROVE,
            reason=None,
            identity=identity,
        )

    async def deny(
        self, id: str, *, reason: str | None = None, identity: Identity
    ) -> OAuthClientView:
        """Deny an OAuth client: ``approval_status='denied'`` + ``active=false`` (D7).

        The row is retained — a denied client's cached client_id stays
        inert-but-valid so a later approve un-bricks the client.
        """
        return await self._set_approval(
            id,
            approval_status=OAuthClientApprovalStatus.DENIED.value,
            active=False,
            action=AuditAction.DENY,
            reason=reason,
            identity=identity,
        )

    async def _set_approval(
        self,
        id: str,
        *,
        approval_status: str,
        active: bool,
        action: AuditAction,
        reason: str | None,
        identity: Identity,
    ) -> OAuthClientView:
        async with self._ctx.admin_db.transaction() as session:
            existing = await OAuthClientRepository.get_by_id(session, id)
            if existing is None:
                raise OAuthClientNotFoundError(id)

            before_snapshot = _snapshot(existing)

            client = await OAuthClientRepository.set_approval_status(
                session, id, approval_status=approval_status, active=active
            )
            if client is None:
                raise OAuthClientNotFoundError(id)

            await record_audit(
                session,
                action=action,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before=before_snapshot,
                after={"approval_status": approval_status, "active": active},
                reason=reason,
                origin=identity.origin.value,
            )
            return _to_view(client)

    async def rotate_secret(self, id: str, *, identity: Identity) -> str:
        """Generate a new client secret. Returns the one-time plaintext.

        Rejected for public clients — a secret-less client has nothing to
        rotate, and silently minting one would flip its auth method.
        """
        client_secret = generate_client_secret()
        secret_hash = await _hash_password_async(client_secret)

        async with self._ctx.admin_db.transaction() as session:
            existing = await OAuthClientRepository.get_by_id(session, id)
            if existing is None:
                raise OAuthClientNotFoundError(id)
            if existing.token_endpoint_auth_method == TokenEndpointAuthMethod.NONE.value:
                raise InvalidInputError("public clients have no secret to rotate")

            client = await OAuthClientRepository.update_secret_hash(session, id, secret_hash)
            if client is None:
                raise OAuthClientNotFoundError(id)

            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"secret_rotated": True},
                origin=identity.origin.value,
            )
        return client_secret

    async def is_redirect_uri_allowed(self, client_id: str, redirect_uri: str) -> bool:
        """Check if a redirect_uri is allowed for a given client_id."""
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_client_id(session, client_id)
        if client is None or not client.active or not _is_approved(client):
            return False
        return redirect_uri in client.redirect_uris

    async def verify_client_secret(self, client_id: str, client_secret: str) -> bool:
        """Verify a confidential client's secret.

        Returns False if not found, inactive, unapproved (D7), public, or the
        secret is wrong. Every failure path runs the argon2 dummy verify so
        unknown ids, unapproved rows, and NULL-hash (public) rows are not
        distinguishable from a wrong secret by timing (D5).
        """
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_client_id(session, client_id)
        if (
            client is None
            or not client.active
            or not _is_approved(client)
            or client.client_secret_hash is None
        ):
            await _verify_password_async(client_secret, _dummy_argon2_hash())
            return False
        return await _verify_password_async(client_secret, client.client_secret_hash)

    async def is_public_client(self, client_id: str) -> bool:
        """True when ``client_id`` is a usable public (secret-less) client.

        Usable means active AND approved: pending/denied rows fail closed at
        every OAuth entry point (D7).
        """
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_client_id(session, client_id)
        return (
            client is not None
            and client.active
            and _is_approved(client)
            and client.token_endpoint_auth_method == TokenEndpointAuthMethod.NONE.value
        )

    async def authenticate_for_token_endpoint(
        self, client_id: str, client_secret: str | None
    ) -> bool:
        """RFC 6749 token-endpoint client authentication for registered clients.

        - Confidential clients (``client_secret_basic``): the secret is
          required and argon2-verified.
        - Public clients (``token_endpoint_auth_method='none'``, D5): no
          secret is required — and none is accepted: a supplied secret on a
          public client is a loud misconfiguration → invalid_client.
        - Unknown, inactive, or unapproved (D7) clients fail closed.
        - Rows violating the none↔NULL-hash coupling invariant (§4.1) fail
          closed: a confidential-method row with a NULL hash must not be
          treated as a public client, and a ``none`` row carrying a stray
          hash must not authenticate either way.

        Whenever a secret is supplied, failure paths run the argon2 dummy
        verify so unknown/public/unapproved rows match the wrong-secret
        timing profile.
        """
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_client_id(session, client_id)

        if client is None or not client.active or not _is_approved(client):
            if client_secret:
                await _verify_password_async(client_secret, _dummy_argon2_hash())
            return False

        is_public = client.token_endpoint_auth_method == TokenEndpointAuthMethod.NONE.value
        if is_public and client.client_secret_hash is None:
            if client_secret:
                await _verify_password_async(client_secret, _dummy_argon2_hash())
                return False
            return True

        if is_public or client.client_secret_hash is None:
            # Invariant-violating row: 'none' and a NULL hash must always
            # travel together (enforced by this service's write paths). If a
            # row disagrees with itself, fail closed on both arms rather than
            # falling open into the no-secret path.
            if client_secret:
                await _verify_password_async(client_secret, _dummy_argon2_hash())
            return False

        if not client_secret:
            return False
        return await _verify_password_async(client_secret, client.client_secret_hash)

    async def get_allowed_scopes(self, client_id: str) -> frozenset[str] | None:
        """Return allowed scopes for a registered client, or None if unrestricted.

        An explicit empty list means "deny all non-OIDC scopes"; None means unrestricted.
        """
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_client_id(session, client_id)
        if client is None:
            return None
        if client.allowed_scopes is not None:
            return frozenset(client.allowed_scopes)
        return None
