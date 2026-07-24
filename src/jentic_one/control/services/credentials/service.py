"""Credential service — CRUD with encrypt-on-write seam."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import (
    BasicCredentialRepository,
    CredentialRepository,
    CustomerAPIKeyRepository,
    OAuthClientCredentialRepository,
    TokenValueCredentialRepository,
)
from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.credentials.errors import (
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
    UnsupportedProviderForTypeError,
)
from jentic_one.control.services.credentials.mapping import to_stored, to_wire
from jentic_one.control.services.credentials.providers.base import UnknownProviderError
from jentic_one.control.services.credentials.schemas.credentials import (
    ApiKeyFull,
    ApiKeyRedacted,
    BasicAuthFull,
    BasicAuthRedacted,
    BearerTokenFull,
    BearerTokenRedacted,
    CredentialCreate,
    CredentialFullView,
    CredentialPage,
    CredentialRedactedView,
    CredentialUpdate,
    OAuth2Full,
    OAuth2Redacted,
    ProviderDiscoveryEntry,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import DirectOAuth2ProviderConfig
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models.api_identity import CredentialScope, canonical_credential_scope
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor
from jentic_one.shared.scopes import ORG_ADMIN
from jentic_one.shared.url_validation import validate_upstream_url

logger = structlog.get_logger()


class CredentialService:
    """Style A standalone service for credential CRUD operations."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def _bound_toolkit_ids(self, identity: Identity) -> list[str]:
        """Toolkit ids the caller is bound to, widening owner-scoped visibility.

        A credential bound to a toolkit the caller can see must itself be visible
        to that caller — including an orphaned agent that owns nothing (issues
        #665/#682). Bindings live in the admin DB, so resolve the ids there and
        feed them into the control-DB ``build_access_filters``. An ``org:admin``
        caller is unrestricted already, so skip the lookup.
        """
        if ORG_ADMIN in identity.permissions or not identity.sub:
            return []
        async with self._ctx.admin_db.session() as session:
            return await PrerequisiteRepository.list_toolkit_ids_for_agent(
                session, agent_id=identity.sub
            )

    def list_providers(self) -> list[ProviderDiscoveryEntry]:
        """Return discovery metadata for all configured providers."""
        provider_configs = self._ctx.config.credentials.providers
        entries: list[ProviderDiscoveryEntry] = []
        for provider_id, provider in self._ctx.providers.list_all().items():
            label = provider_id.replace("_", " ").title()
            callback_url: str | None = None
            pc = provider_configs.get(provider_id)
            if isinstance(pc, DirectOAuth2ProviderConfig):
                callback_url = pc.redirect_uri
            entries.append(
                ProviderDiscoveryEntry(
                    id=provider_id,
                    label=label,
                    managed=provider.managed,
                    types=provider.supported_types,
                    configured=True,
                    callback_url=callback_url,
                )
            )
        return entries

    async def create(self, payload: CredentialCreate, *, identity: Identity) -> CredentialFullView:
        """Create a credential and echo the secret once."""
        try:
            provider_obj = self._ctx.providers.get(payload.provider)
        except UnknownProviderError as exc:
            # Surface an unconfigured provider as a 400 via the standard
            # problem+json error pipeline rather than a 500/ad-hoc response.
            raise InvalidCredentialInputError(str(exc)) from exc
        if not provider_obj.supports(payload.type):
            raise UnsupportedProviderForTypeError(payload.provider, payload.type.value)

        self._validate_create_fields(payload, managed=provider_obj.managed)

        # Canonicalize the API identity at the service boundary: slug vendor/name,
        # coerce an unset name/version to NULL (the single wildcard sentinel), and
        # trim the version. This is what makes credential scoping (vendor /
        # vendor.name / vendor.name.version) resolve against a concrete operation
        # identity at execute time (#775), and stores github.com as github-com
        # rather than a dead-on-arrival identity (#746).
        api_scope = self._canonical_api_scope(payload.api)

        stored_type = to_stored(payload.type, grant_type=payload.grant_type)
        encryption = self._ctx.encryption

        async with self._ctx.control_db.transaction() as session:
            credential = await CredentialRepository.create(
                session,
                type=stored_type.value,
                name=payload.name,
                api_vendor=api_scope.vendor,
                api_name=api_scope.name,
                api_version=api_scope.version,
                provider=payload.provider,
                created_by=identity.sub,
                server_variables=payload.server_variables,
            )

            secret: ApiKeyFull | BearerTokenFull | BasicAuthFull | OAuth2Full

            if payload.type == CredentialType.BEARER_TOKEN:
                assert payload.token
                encrypted = encryption.encrypt(payload.token)
                preview = encryption.preview(payload.token)
                await TokenValueCredentialRepository.create(
                    session,
                    credential_id=credential.id,
                    encrypted_token_value=encrypted,
                    token_preview=preview,
                    created_by=identity.sub,
                )
                secret = BearerTokenFull(token=payload.token)

            elif payload.type == CredentialType.API_KEY:
                assert payload.key
                assert payload.location
                assert payload.field_name
                encrypted = encryption.encrypt(payload.key)
                preview = encryption.preview(payload.key)
                await CustomerAPIKeyRepository.create(
                    session,
                    credential_id=credential.id,
                    encrypted_key=encrypted,
                    key_preview=preview,
                    location=payload.location,
                    field_name=payload.field_name,
                    created_by=identity.sub,
                )
                secret = ApiKeyFull(
                    key=payload.key, location=payload.location, field_name=payload.field_name
                )

            elif payload.type == CredentialType.BASIC:
                assert payload.username
                assert payload.password
                encrypted_pw = encryption.encrypt(payload.password)
                await BasicCredentialRepository.create(
                    session,
                    credential_id=credential.id,
                    username=payload.username,
                    encrypted_password=encrypted_pw,
                    created_by=identity.sub,
                )
                secret = BasicAuthFull(username=payload.username, password=payload.password)

            elif payload.type == CredentialType.OAUTH2:
                validated_token_url: str | None = None
                if payload.token_url:
                    try:
                        validated_token_url = validate_upstream_url(payload.token_url)
                    except ValueError as exc:
                        raise InvalidCredentialInputError(f"Invalid token_url: {exc}") from exc
                validated_authorize_url: str | None = None
                if payload.authorize_url:
                    try:
                        validated_authorize_url = validate_upstream_url(payload.authorize_url)
                    except ValueError as exc:
                        raise InvalidCredentialInputError(f"Invalid authorize_url: {exc}") from exc
                grant = payload.grant_type or "client_credentials"

                encrypted_secret: str | None = None
                if payload.client_secret:
                    encrypted_secret = encryption.encrypt(payload.client_secret)

                if not provider_obj.managed or payload.client_id:
                    scope = " ".join(payload.scopes) if payload.scopes else None
                    await OAuthClientCredentialRepository.create(
                        session,
                        credential_id=credential.id,
                        token_url=validated_token_url or "",
                        client_id=payload.client_id or "",
                        encrypted_client_secret=encrypted_secret or "",
                        authorize_url=validated_authorize_url,
                        scope=scope,
                        created_by=identity.sub,
                    )

                secret = OAuth2Full(
                    client_id=payload.client_id or "",
                    client_secret=payload.client_secret or "",
                    token_url=payload.token_url or "",
                    grant_type=grant,
                    scopes=payload.scopes,
                )
            else:
                raise InvalidCredentialInputError(f"Unsupported credential type: {payload.type}")

            view = CredentialFullView(
                credential_id=credential.id,
                type=to_wire(stored_type),
                name=credential.name,
                api=APIReference(
                    vendor=credential.api_vendor,
                    name=credential.api_name or "",
                    version=credential.api_version or "",
                ),
                provider=credential.provider,
                active=credential.active,
                created_at=credential.created_at,
                server_variables=credential.server_variables,
                secret=secret,
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.CREDENTIAL,
            target_id=view.credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            after={
                "name": payload.name,
                "type": str(payload.type),
                "provider": payload.provider,
                "active": view.active,
            },
            origin=identity.origin.value,
        )
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CREDENTIAL_STORED,
                    severity=EventSeverity.INFO,
                    summary=f"Credential {view.credential_id} stored",
                    created_by=identity.sub,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )
        except Exception:
            logger.warning(
                "telemetry_emit_failed", event_type=EventType.CREDENTIAL_STORED, exc_info=True
            )
        return view

    async def get(self, credential_id: str, *, identity: Identity) -> CredentialRedactedView:
        """Get a credential by ID with redacted secrets."""
        access_filters = build_access_filters(
            identity, Credential, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                raise CredentialNotFoundError(credential_id)
            return self._to_redacted(credential)

    async def list_all(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        vendor: str | None = None,
        identity: Identity,
    ) -> CredentialPage:
        """List credentials with cursor pagination."""
        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)

        access_filters = build_access_filters(
            identity, Credential, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )

        async with self._ctx.control_db.session() as session:
            rows = await CredentialRepository.list_all(
                session, cursor=decoded_cursor, limit=limit, vendor=vendor, filters=access_filters
            )

            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]

            data = [self._to_redacted(r) for r in rows]
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)

        return CredentialPage(data=data, has_more=has_more, next_cursor=next_cursor)

    async def update(
        self, credential_id: str, payload: CredentialUpdate, *, identity: Identity
    ) -> CredentialRedactedView:
        """Update/rotate a credential."""
        access_filters = build_access_filters(identity, Credential)
        async with self._ctx.control_db.transaction() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                raise CredentialNotFoundError(credential_id)

            before_state = {"name": credential.name, "active": credential.active}

            wire_type = to_wire(StoredCredentialType(credential.type))
            if payload.type != wire_type:
                raise ImmutableFieldError("type")

            if (
                payload.name is not None
                or payload.active is not None
                or payload.server_variables is not None
            ):
                await CredentialRepository.update_header(
                    session,
                    credential_id,
                    name=payload.name,
                    active=payload.active,
                    server_variables=payload.server_variables,
                )

            encryption = self._ctx.encryption

            if payload.type == CredentialType.BEARER_TOKEN and payload.token is not None:
                encrypted = encryption.encrypt(payload.token)
                preview = encryption.preview(payload.token)
                await TokenValueCredentialRepository.update_token(
                    session,
                    credential_id,
                    encrypted_token_value=encrypted,
                    token_preview=preview,
                )

            elif payload.type == CredentialType.API_KEY and payload.key is not None:
                encrypted = encryption.encrypt(payload.key)
                preview = encryption.preview(payload.key)
                await CustomerAPIKeyRepository.update_key(
                    session,
                    credential_id,
                    encrypted_key=encrypted,
                    key_preview=preview,
                )

            elif payload.type == CredentialType.BASIC:
                if payload.username is not None or payload.password is not None:
                    encrypted_pw = (
                        encryption.encrypt(payload.password) if payload.password else None
                    )
                    await BasicCredentialRepository.update(
                        session,
                        credential_id,
                        username=payload.username,
                        encrypted_password=encrypted_pw,
                    )

            elif payload.type == CredentialType.OAUTH2 and payload.client_secret is not None:
                validated_token_url: str | None = None
                if payload.token_url:
                    try:
                        validated_token_url = validate_upstream_url(payload.token_url)
                    except ValueError as exc:
                        raise InvalidCredentialInputError(f"Invalid token_url: {exc}") from exc
                encrypted_secret = encryption.encrypt(payload.client_secret)
                scope = " ".join(payload.scopes) if payload.scopes else None
                await OAuthClientCredentialRepository.update(
                    session,
                    credential_id,
                    encrypted_client_secret=encrypted_secret,
                    token_url=validated_token_url,
                    scope=scope,
                )

            credential = await CredentialRepository.get_by_id(session, credential_id)
            assert credential is not None
            credential.updated_at = datetime.now(UTC)
            await session.flush()
            view = self._to_redacted(credential)
            after_state = {"name": credential.name, "active": credential.active}

        action = AuditAction.UPDATE
        if payload.active is not None and before_state["active"] != payload.active:
            action = AuditAction.ENABLE if payload.active else AuditAction.DISABLE
        await record_audit_best_effort(
            self._ctx,
            action=action,
            target_type=AuditTargetType.CREDENTIAL,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            before=before_state,
            after=after_state,
            origin=identity.origin.value,
        )
        return view

    async def delete(self, credential_id: str, *, identity: Identity) -> None:
        """Delete a credential by ID (cascade removes siblings)."""
        access_filters = build_access_filters(identity, Credential)
        async with self._ctx.control_db.transaction() as session:
            existing = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if existing is None:
                raise CredentialNotFoundError(credential_id)
            deleted = await CredentialRepository.delete(session, credential_id)
            if not deleted:
                raise CredentialNotFoundError(credential_id)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.CREDENTIAL,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            before={"name": existing.name, "active": existing.active},
            origin=identity.origin.value,
        )

    def _to_redacted(self, credential: Any) -> CredentialRedactedView:
        """Project an ORM Credential to a redacted view."""
        stored_type = StoredCredentialType(credential.type)
        wire_type = to_wire(stored_type)

        details: BearerTokenRedacted | ApiKeyRedacted | BasicAuthRedacted | OAuth2Redacted

        if wire_type == CredentialType.BEARER_TOKEN:
            tvc = credential.token_value_credential
            preview = tvc.token_preview if tvc else None
            details = BearerTokenRedacted(token_preview=preview)

        elif wire_type == CredentialType.API_KEY:
            cak = credential.customer_api_key
            preview = cak.key_preview if cak else None
            details = ApiKeyRedacted(
                key_preview=preview,
                location=cak.location if cak else None,
                field_name=cak.field_name if cak else None,
            )

        elif wire_type == CredentialType.BASIC:
            bc = credential.basic_credential
            username = bc.username if bc else ""
            details = BasicAuthRedacted(username=username)

        elif wire_type == CredentialType.OAUTH2:
            occ = credential.oauth_client_credential
            details = OAuth2Redacted(
                client_id=occ.client_id if occ else "",
                token_url=occ.token_url if occ else "",
                grant_type="client_credentials",
                scopes=occ.scope.split() if occ and occ.scope else None,
            )
        else:
            details = BearerTokenRedacted(token_preview=None)

        return CredentialRedactedView(
            credential_id=credential.id,
            type=wire_type,
            name=credential.name,
            api=APIReference(
                vendor=credential.api_vendor,
                name=credential.api_name or "",
                version=credential.api_version or "",
            ),
            provider=credential.provider,
            provider_account_ref=credential.provider_account_ref,
            active=credential.active,
            created_by=credential.created_by,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
            details=details,
            server_variables=credential.server_variables,
        )

    def _validate_create_fields(self, payload: CredentialCreate, *, managed: bool) -> None:
        """Validate required fields per type before touching encryption/DB."""
        if payload.type == CredentialType.BEARER_TOKEN:
            if not payload.token:
                raise InvalidCredentialInputError("Field 'token' is required for bearer_token")
        elif payload.type == CredentialType.API_KEY:
            if not payload.key:
                raise InvalidCredentialInputError("Field 'key' is required for api_key")
            if not payload.location:
                raise InvalidCredentialInputError("Field 'location' is required for api_key")
            if not payload.field_name:
                raise InvalidCredentialInputError("Field 'field_name' is required for api_key")
        elif payload.type == CredentialType.BASIC:
            if not payload.username:
                raise InvalidCredentialInputError("Field 'username' is required for basic")
            if not payload.password:
                raise InvalidCredentialInputError("Field 'password' is required for basic")
        elif payload.type == CredentialType.OAUTH2 and not managed:
            if not payload.token_url:
                raise InvalidCredentialInputError("Field 'token_url' is required for oauth2")
            if not payload.client_id:
                raise InvalidCredentialInputError("Field 'client_id' is required for oauth2")
            if not payload.client_secret:
                raise InvalidCredentialInputError("Field 'client_secret' is required for oauth2")

    @staticmethod
    def _canonical_api_scope(api: APIReference) -> CredentialScope:
        """Canonicalize the credential's API scope, rejecting a path-shaped identity.

        A ``name``/``version`` containing a path separator is a strong signal the
        caller sent a spec *file path* segment rather than an identity (e.g.
        ``api_version='api.github.com/main/1.1.4'``, #746). Reject it loudly as a
        400 rather than persisting a credential that can never resolve.
        """
        for axis, value in (("name", api.name), ("version", api.version)):
            if value and "/" in value:
                raise InvalidCredentialInputError(
                    f"api.{axis} '{value}' is not an identity — it looks like a spec path"
                )
        return canonical_credential_scope(vendor=api.vendor, name=api.name, version=api.version)
