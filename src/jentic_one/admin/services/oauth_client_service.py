"""OAuth client service — manage registered third-party applications."""

from __future__ import annotations

from urllib.parse import urlparse

from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.admin.services.errors import InvalidInputError, NotFoundError
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.shared.audit import record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType


class OAuthClientNotFoundError(NotFoundError):
    """Raised when an OAuth client does not exist."""

    def __init__(self, id: str) -> None:
        super().__init__(f"OAuth client '{id}' not found")
        self.id = id


_MAX_REDIRECT_URIS = 20
_MAX_REDIRECT_URI_LENGTH = 2048


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
            raise InvalidInputError(
                f"redirect_uri must not contain a fragment component: {uri}"
            )
        if parsed.scheme not in ("https", "http"):
            raise InvalidInputError(f"redirect_uri must use https or http: {uri}")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise InvalidInputError(
                f"http redirect_uri only allowed for localhost: {uri}"
            )


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
        created_at=client.created_at,
        updated_at=client.updated_at,
        created_by=client.created_by,
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
        identity: Identity,
    ) -> OAuthClientView:
        """Register a new OAuth client."""
        _validate_redirect_uris(redirect_uris)

        async with self._ctx.admin_db.transaction() as session:
            client = await OAuthClientRepository.create(
                session,
                name=name,
                redirect_uris=redirect_uris,
                description=description,
                require_consent=require_consent,
                allowed_scopes=allowed_scopes,
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
                    "redirect_uris": redirect_uris,
                    "require_consent": require_consent,
                },
                origin=identity.origin.value,
            )
            return _to_view(client)

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

    async def list_all(self, *, include_inactive: bool = False) -> list[OAuthClientView]:
        """List all OAuth clients."""
        async with self._ctx.admin_db.session() as session:
            clients = await OAuthClientRepository.list_all(
                session, include_inactive=include_inactive
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
        """Update an OAuth client."""
        if redirect_uris is not None:
            _validate_redirect_uris(redirect_uris)

        async with self._ctx.admin_db.transaction() as session:
            client = await OAuthClientRepository.update(
                session,
                id,
                name=name,
                description=description,
                redirect_uris=redirect_uris,
                active=active,
                require_consent=require_consent,
                allowed_scopes=allowed_scopes,
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

            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after=changes,
                origin=identity.origin.value,
            )
            return _to_view(client)

    async def deactivate(self, id: str, *, identity: Identity) -> None:
        """Soft-delete an OAuth client by setting active=False."""
        async with self._ctx.admin_db.transaction() as session:
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
                after={"active": False},
                origin=identity.origin.value,
            )

    async def is_redirect_uri_allowed(self, client_id: str, redirect_uri: str) -> bool:
        """Check if a redirect_uri is allowed for a given client_id."""
        async with self._ctx.admin_db.session() as session:
            client = await OAuthClientRepository.get_by_client_id(session, client_id)
        if client is None or not client.active:
            return False
        return redirect_uri in client.redirect_uris
