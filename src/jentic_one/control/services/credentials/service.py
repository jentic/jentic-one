"""Credential service — CRUD with encrypt-on-write seam."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, NamedTuple
from urllib.parse import urlsplit

import structlog
from pydantic import SecretStr

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)
from jentic_one.control.repos import (
    AgentPermissionRuleRepository,
    BasicCredentialRepository,
    CredentialRepository,
    CustomerAPIKeyRepository,
    OAuthAppRegistrationRepository,
    OAuthClientCredentialRepository,
    OAuthTokenRepository,
    PermissionRuleSetRepository,
    Sigv4CredentialRepository,
    TokenValueCredentialRepository,
)
from jentic_one.control.repos.device_authorization_credential_repo import (
    DeviceAuthorizationCredentialRepository,
)
from jentic_one.control.repos.oauth_client_credential_repo import UNSET as SCOPE_UNSET
from jentic_one.control.repos.pending_import_lookup_repo import PendingImportLookupRepository
from jentic_one.control.repos.prerequisite_repo import (
    AgentCredentialBindingRow,
    AgentVisibility,
    CredentialBoundAgentRow,
    PrerequisiteRepository,
)
from jentic_one.control.repos.registry_api_lookup_repo import RegistryApiLookupRepository
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.credentials.errors import (
    AgentBindingNotFoundError,
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
    RuleSetAccessDeniedError,
    RuleSetAttachDeniedError,
    RuleSetInUseError,
    RuleSetNameConflictError,
    RuleSetNotFoundError,
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
    NoAuthFull,
    NoAuthRedacted,
    OAuth2Full,
    OAuth2Redacted,
    ProviderDiscoveryEntry,
    Sigv4Full,
    Sigv4Redacted,
)
from jentic_one.control.services.credentials.schemas.permission_test import PermissionTestResult
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.typed_secrets import (
    write_api_key_secret,
    write_basic_secret,
    write_bearer_token_secret,
    write_sigv4_secret,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import ORG_ADMIN, OWNER_AGENTS_READ
from jentic_one.shared.config import DirectOAuth2ProviderConfig, resolved_control_egress
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort, summary_label
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.api_identity import (
    CredentialScope,
    canonical_credential_scope,
    credential_covers,
    slugify_api_field,
)
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor
from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    first_matching_rule,
    normalize_methods,
)
from jentic_one.shared.permissions.matching import compile_matcher
from jentic_one.shared.url_path import has_ambiguous_traversal, normalize_path
from jentic_one.shared.url_validation import validate_upstream_url_async

logger = structlog.get_logger()

# Upper bound on the advisory registry probe at credential create (#1020). The
# probe is purely best-effort, so a hung registry DB (network blackhole, dying
# node) must not drag POST /credentials to the driver's connect timeout (~60s);
# on expiry the TimeoutError lands in the probe's own except-and-skip fallback.
_REGISTRY_PROBE_TIMEOUT_S = 2.0
# How many of the vendor's imported identities the unmatched-scope hint names.
_SIBLING_HINT_LIMIT = 5
# Update fields where "" is never a valid replacement (``None`` = unchanged).
_NON_EMPTY_UPDATE_FIELDS = (
    "name",
    "token",
    "key",
    "username",
    "password",
    "client_secret",
    "token_url",
    "access_key_id",
    "secret_access_key",
    "session_token",
    "aws_region",
    "aws_service",
)
# Secret update fields per credential type, and the boolean marker the audit
# ``after`` snapshot carries when one is replaced (the value never is).
_SECRET_ROTATION_MARKERS: dict[CredentialType, tuple[tuple[str, str], ...]] = {
    CredentialType.BEARER_TOKEN: (("token", "token_rotated"),),
    CredentialType.API_KEY: (("key", "key_rotated"),),
    CredentialType.BASIC: (("password", "password_rotated"),),
    CredentialType.OAUTH2: (("client_secret", "client_secret_rotated"),),
    CredentialType.SIGV4: (
        ("secret_access_key", "secret_access_key_rotated"),
        ("session_token", "session_token_rotated"),
    ),
}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_blank_str(value: str | None, *, missing: bool = False) -> bool:
    """True for an empty or whitespace-only string; ``None`` counts iff *missing*."""
    if value is None:
        return missing
    return not value.strip()


def _validate_scope_tokens(scopes: Sequence[str]) -> None:
    """Reject scope items that are not a single RFC 6749 scope-token.

    Scopes are stored and sent space-joined, so an empty item or one holding
    whitespace would silently vanish or split into several scopes.
    """
    for scope in scopes:
        if not scope or any(ch.isspace() for ch in scope):
            raise InvalidCredentialInputError(
                f"Invalid scope {scope!r}: each scope must be non-empty and contain no whitespace"
            )


def _url_origin(url: str) -> tuple[str, str, int | None]:
    """``(scheme, host, port)`` of *url*, with the scheme's default port filled in."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, (parts.hostname or "").lower(), port or _DEFAULT_PORTS.get(scheme)


def _same_origin(new_url: str, stored_url: str | None) -> bool:
    """Whether *new_url* keeps the origin of *stored_url* (no stored URL never matches)."""
    return bool(stored_url) and _url_origin(new_url) == _url_origin(stored_url or "")


def _no_client_config_error() -> InvalidCredentialInputError:
    # A managed-provider credential can exist without stored client config; a
    # 200 that persisted nothing would hide the dropped edit.
    return InvalidCredentialInputError("This oauth2 credential has no client configuration to edit")


class _UnmatchedScopeWarning(NamedTuple):
    """One advisory warning: the human message plus the scope it names."""

    reference: str
    message: str


def _scope_reference(scope: CredentialScope) -> str:
    """Render a credential scope as ``vendor/name/version`` with ``*`` for wildcards.

    Every axis is always shown so the position is unambiguous: a name-wildcard,
    version-pinned scope reads ``stripe-com/*/2024-04-10`` rather than collapsing
    to ``stripe-com/2024-04-10``, which reads as a name.
    """
    return f"{scope.vendor}/{scope.name or '*'}/{scope.version or '*'}"


class CredentialService:
    """Style A standalone service for credential CRUD operations."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def _bound_credential_ids(self, identity: Identity) -> list[str]:
        """Credential ids the caller is directly bound to (theme 5 phase 1).

        An agent must be able to read a credential it is actively bound to
        even when it owns nothing (issues #665/#682). Suspended bindings
        grant no visibility — a cut-off cuts reads of the credential too, while
        the binding row itself stays visible in ``/me``. ``org:admin`` is
        unrestricted already, so skip the lookup.
        """
        if ORG_ADMIN in identity.permissions or not identity.sub:
            return []
        async with self._ctx.admin_db.session() as session:
            return await PrerequisiteRepository.list_credential_ids_for_agent(
                session, agent_id=identity.sub
            )

    async def _owned_agent_ids(self, identity: Identity) -> list[str]:
        """Agent ids the caller owns, for the read-only owned-agent scoping clause.

        Lets a human read the pending credential of a connect session one of
        their agents opened (see ``build_access_filters``). Only humans own
        agents and ``org:admin`` is unrestricted already, so both skip the
        lookup.
        """
        if (
            ORG_ADMIN in identity.permissions
            or identity.actor_type != ActorType.USER
            or not identity.sub
        ):
            return []
        async with self._ctx.admin_db.session() as session:
            return await PrerequisiteRepository.list_agent_ids_owned_by(
                session, owner_id=identity.sub
            )

    def list_providers(
        self, *, default_callback_url: str | None = None
    ) -> list[ProviderDiscoveryEntry]:
        """Return discovery metadata for all configured providers.

        ``default_callback_url`` is the request-derived OAuth callback URL the
        web layer would use when a provider has no explicit ``redirect_uri``
        configured. Passing it keeps the discovery response in lockstep with
        what ``begin_connect`` actually sends to the IdP.
        """
        provider_configs = self._ctx.config.credentials.providers
        entries: list[ProviderDiscoveryEntry] = []
        for provider_id, provider in self._ctx.providers.list_all().items():
            label = provider_id.replace("_", " ").title()
            callback_url: str | None = None
            pc = provider_configs.get(provider_id)
            if isinstance(pc, DirectOAuth2ProviderConfig):
                callback_url = pc.redirect_uri or default_callback_url
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
        api_scope = await self._catalog_registered_scope(api_scope, payload.catalog_api_id)

        stored_type = to_stored(payload.type, grant_type=payload.grant_type)
        encryption = self._ctx.encryption

        # Resolve the OAuth2 endpoints before the write transaction opens: on
        # SQLite ``transaction()`` holds the process-wide writer lock, so a slow
        # resolver inside it would fail concurrent writers on ``busy_timeout``.
        validated_token_url: str | None = None
        validated_authorize_url: str | None = None
        if payload.type == CredentialType.OAUTH2:
            validated_token_url = await self._validated_oauth2_url("token_url", payload.token_url)
            validated_authorize_url = await self._validated_oauth2_url(
                "authorize_url", payload.authorize_url
            )

        async with self._ctx.control_db.transaction() as session:
            credential = await CredentialRepository.create(
                session,
                type=stored_type.value,
                name=payload.name,
                api_vendor=api_scope.vendor,
                api_name=api_scope.name,
                api_version=api_scope.version,
                # Verbatim, deliberately outside the canonicalisation above:
                # the slug is display-only provenance (`domain[/sub-api]`) and
                # slugifying it would destroy the separable structure it exists
                # to preserve.
                catalog_api_id=payload.catalog_api_id,
                provider=payload.provider,
                created_by=identity.sub,
                server_variables=payload.server_variables,
            )

            secret: (
                ApiKeyFull | BearerTokenFull | BasicAuthFull | OAuth2Full | NoAuthFull | Sigv4Full
            )

            if payload.type == CredentialType.BEARER_TOKEN:
                assert payload.token
                secret = await write_bearer_token_secret(
                    session,
                    encryption,
                    credential_id=credential.id,
                    token=SecretStr(payload.token),
                    created_by=identity.sub,
                )

            elif payload.type == CredentialType.API_KEY:
                assert payload.key
                assert payload.location
                assert payload.field_name
                secret = await write_api_key_secret(
                    session,
                    encryption,
                    credential_id=credential.id,
                    key=SecretStr(payload.key),
                    location=payload.location,
                    field_name=payload.field_name,
                    created_by=identity.sub,
                )

            elif payload.type == CredentialType.BASIC:
                assert payload.username
                assert payload.password
                secret = await write_basic_secret(
                    session,
                    encryption,
                    credential_id=credential.id,
                    username=payload.username,
                    password=SecretStr(payload.password),
                    created_by=identity.sub,
                )

            elif payload.type == CredentialType.OAUTH2:
                grant = payload.grant_type or "client_credentials"

                if grant == "device_code":
                    # RFC 8628 device flow — public client (no secret), the
                    # ``authorize_url`` field carries the vendor's
                    # ``device_authorization_endpoint`` (OpenAPI 3.2's
                    # ``deviceAuthorizationUrl``). Row lives on
                    # ``device_authorization_credentials`` alongside the credentials
                    # written by the connect-session flow so refresh /
                    # redaction / broker resolution are all uniform.
                    await DeviceAuthorizationCredentialRepository.create(
                        session,
                        credential_id=credential.id,
                        client_id=payload.client_id or "",
                        token_url=validated_token_url or "",
                        authorization_endpoint=validated_authorize_url or "",
                        requested_scopes=payload.scopes or [],
                        created_by=identity.sub,
                    )
                    secret = OAuth2Full(
                        client_id=payload.client_id or "",
                        client_secret="",
                        token_url=payload.token_url or "",
                        grant_type=grant,
                        scopes=payload.scopes,
                    )
                else:
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
            elif payload.type == CredentialType.NO_AUTH:
                # A no-auth credential is a marker that the API needs no secret
                # (e.g. open-meteo). No sub-table row and no secret are stored;
                # the broker injects nothing for it. This lets a provisioning
                # plan reach first execution without a credential secret (#603).
                secret = NoAuthFull()
            elif payload.type == CredentialType.SIGV4:
                assert payload.access_key_id
                assert payload.secret_access_key
                assert payload.aws_region
                assert payload.aws_service
                secret = await write_sigv4_secret(
                    session,
                    encryption,
                    credential_id=credential.id,
                    access_key_id=payload.access_key_id,
                    secret_access_key=SecretStr(payload.secret_access_key),
                    session_token=(
                        SecretStr(payload.session_token)
                        if payload.session_token is not None
                        else None
                    ),
                    region=payload.aws_region,
                    service=payload.aws_service,
                    created_by=identity.sub,
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
                catalog_api_id=credential.catalog_api_id,
                provider=credential.provider,
                active=credential.active,
                created_by=credential.created_by,
                created_at=credential.created_at,
                server_variables=credential.server_variables,
                secret=secret,
            )

        # Advisory check (#1020), run only once the create has committed: a scope
        # that covers no imported API is legal (the API may be imported later) but
        # every execute through it would 403 — surface the mismatch now instead of
        # at execute time.
        unmatched = await self._unmatched_scope_warning(api_scope)
        if unmatched is not None:
            view = view.model_copy(update={"warnings": [unmatched.message]})

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
                # Each best-effort emit runs in its own SAVEPOINT: a failed INSERT
                # aborts the enclosing Postgres transaction, so without it one
                # emit's swallowed failure would make the commit raise and drop
                # every other event in this transaction with it.
                async with session.begin_nested():
                    await emit_event_best_effort(
                        session,
                        type=EventType.CREDENTIAL_STORED,
                        severity=EventSeverity.INFO,
                        summary=f"Credential {summary_label(view.name, view.credential_id)} stored",
                        data={"credential_id": view.credential_id},
                        created_by=identity.sub,
                        actor_id=identity.sub,
                        actor_type=identity.actor_type.value,
                    )
                if unmatched is not None:
                    async with session.begin_nested():
                        await emit_event_best_effort(
                            session,
                            type=EventType.CREDENTIAL_UNMATCHED_API,
                            severity=EventSeverity.WARNING,
                            # Keep the summary short and bounded (the events column
                            # caps at 512 chars and every UI surface truncates to
                            # one line); the actionable remedy + sibling-identity
                            # hint travel in `detail`, which the events page renders
                            # in full. The reference is caller-shaped (version is
                            # free-text), so clamp it defensively.
                            summary=(
                                f"Credential {view.credential_id}: API scope "
                                f"'{unmatched.reference[:200]}' matches no imported API"
                            ),
                            detail=unmatched.message,
                            # The canonical scope rides along for consumers that
                            # correlate the warning with a later import.
                            data={
                                "credential_id": view.credential_id,
                                "api_vendor": api_scope.vendor,
                                "api_name": api_scope.name,
                                "api_version": api_scope.version,
                            },
                            created_by=identity.sub,
                            actor_id=identity.sub,
                            actor_type=identity.actor_type.value,
                        )
        except Exception:
            # Per-emit failures are swallowed inside emit_event_best_effort; what
            # lands here is the shared admin transaction itself failing, which
            # can't be attributed to a single event type.
            logger.warning(
                "credential_event_emit_failed",
                credential_id=view.credential_id,
                exc_info=True,
            )
        return view

    async def get(self, credential_id: str, *, identity: Identity) -> CredentialRedactedView:
        """Get a credential by ID with redacted secrets."""
        access_filters = build_access_filters(
            identity,
            Credential,
            bound_credential_ids=await self._bound_credential_ids(identity),
            owned_agent_ids=await self._owned_agent_ids(identity),
            include_shared=True,
        )
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                raise CredentialNotFoundError(credential_id)
            names = await OAuthAppRegistrationRepository.get_names_by_ids(
                session, _registration_ids([credential])
            )
            return self._to_redacted(credential, registration_names=names)

    async def visible_creator_ids(
        self, actor_ids: Sequence[str], *, identity: Identity
    ) -> set[str]:
        """Return the ids in ``actor_ids`` that created a credential the caller can read.

        Uses the same read filters as :meth:`get`, extension-registered
        visibility (``include_shared``) included, so a credential shared with
        the caller names its creator too. The credential read is one query,
        bounded by ``actor_ids``.
        """
        if not actor_ids:
            return set()
        access_filters = build_access_filters(
            identity,
            Credential,
            bound_credential_ids=await self._bound_credential_ids(identity),
            owned_agent_ids=await self._owned_agent_ids(identity),
            include_shared=True,
        )
        async with self._ctx.control_db.session() as session:
            return await CredentialRepository.get_creators_among(
                session, actor_ids, filters=access_filters
            )

    async def list_agents(
        self,
        credential_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        identity: Identity,
    ) -> tuple[list[CredentialBoundAgentRow], bool, str | None]:
        """List agents directly bound to a credential. Returns (data, has_more, next_cursor).

        The reverse lookup for the credential-detail "Agents" view (theme 5
        phase 1). Visibility is the same gate as ``get``: the caller must be
        able to see the credential itself before enumerating who is bound to
        it (hard problem 7/9 owner-gating; existence never leaks past the
        filters).

        ``org:admin`` and the credential's creator see every bound agent. Any
        other caller who can see the credential (a bound agent, a delegated
        agent, a shared-read grant) sees only the agents it can see itself:
        see :meth:`_bound_agent_visibility`.
        """
        access_filters = build_access_filters(
            identity,
            Credential,
            bound_credential_ids=await self._bound_credential_ids(identity),
            owned_agent_ids=await self._owned_agent_ids(identity),
            include_shared=True,
        )
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                raise CredentialNotFoundError(credential_id)

        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)

        async with self._ctx.admin_db.session() as session:
            rows = await PrerequisiteRepository.list_agents_for_credential(
                session,
                credential_id=credential_id,
                cursor=decoded_cursor,
                limit=limit + 1,
                visible_to=self._bound_agent_visibility(credential, identity),
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.bound_at, last.binding_id)

        return rows, has_more, next_cursor

    @staticmethod
    def _bound_agent_visibility(
        credential: Credential, identity: Identity
    ) -> AgentVisibility | None:
        """Bound agents the caller may enumerate; ``None`` means all of them.

        ``org:admin`` and the credential's creator see every bound agent.
        Anyone else sees the agents it owns and itself (when the caller is an
        agent), plus its owner's agents when it holds ``owner:agents:read`` —
        the same agent visibility the admin surface applies.
        """
        if ORG_ADMIN in identity.permissions:
            return None
        if credential.created_by is not None and credential.created_by == identity.sub:
            return None
        owner_ids = [identity.sub]
        if OWNER_AGENTS_READ in identity.permissions and identity.parent_actor_id is not None:
            owner_ids.append(identity.parent_actor_id)
        return AgentVisibility(self_id=identity.sub, owner_ids=tuple(owner_ids))

    # --- Per-binding permission rules (theme 5 phase 1) ---

    @staticmethod
    def _may_write_binding_rules(credential: Credential, agent_id: str, identity: Identity) -> bool:
        """Owner-or-admin write gate for a binding's permission rules.

        The rules bound what an agent may do with the credential, so changing
        them is the credential owner's call: ``org:admin`` or the identity
        that created the credential. Read visibility is deliberately not
        enough — neither being the bound agent itself, nor an owner-delegation
        read scope, nor an extension's shared-read grant widens this gate.

        The bound agent never edits its own binding's rules, even when it is
        the credential's ``created_by`` (an agent-initiated connect records
        the agent as creator): the rules exist to constrain that agent, and
        the human-approved set is what it runs under.
        """
        if ORG_ADMIN in identity.permissions:
            return True
        return (
            credential.created_by is not None
            and credential.created_by == identity.sub
            and agent_id != identity.sub
        )

    async def _require_visible_binding(
        self,
        credential_id: str,
        agent_id: str,
        *,
        identity: Identity,
        for_write: bool = False,
    ) -> AgentCredentialBindingRow:
        """Gate the per-binding rules endpoints on both axes (hard problems 7/9).

        The caller must see the credential (same filters as ``get`` — a miss
        is a uniform 404 that never confirms existence), and the direct
        ``(agent, credential)`` binding must exist (admin-DB row; the rules
        themselves live control-side, so this is the cross-DB seam). Returns
        the binding row so callers can see its attached ``rule_set_id``.

        The bound agent must also be one the caller may see
        (:meth:`_bound_agent_visibility`, the same rule as ``list_agents``):
        a binding of an agent outside that set answers the same 404 as a
        binding that does not exist.

        ``for_write`` additionally requires :meth:`_may_write_binding_rules`;
        a caller who can see the credential but not write its rules gets the
        same 404 as one who cannot see it at all. Writes are therefore
        owner-or-admin only, and both of those callers see every bound agent.
        """
        access_filters = build_access_filters(
            identity,
            Credential,
            bound_credential_ids=await self._bound_credential_ids(identity),
            owned_agent_ids=await self._owned_agent_ids(identity),
            include_shared=True,
        )
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None or (
                for_write and not self._may_write_binding_rules(credential, agent_id, identity)
            ):
                raise CredentialNotFoundError(credential_id)
        async with self._ctx.admin_db.session() as session:
            binding = await PrerequisiteRepository.get_agent_credential_binding(
                session,
                agent_id=agent_id,
                credential_id=credential_id,
                visible_to=self._bound_agent_visibility(credential, identity),
            )
        if binding is None:
            raise AgentBindingNotFoundError(credential_id, agent_id)
        return binding._replace(credential_name=credential.name)

    async def _record_rules_change(
        self,
        credential_id: str,
        agent_id: str,
        binding: AgentCredentialBindingRow,
        *,
        identity: Identity,
        reason: str,
    ) -> None:
        """Audit + telemetry for a rules mutation (mirrors the toolkit path).

        ``binding`` is the row :meth:`_require_visible_binding` returned; its
        agent and credential names label the event summary.
        """
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=agent_id,
            reason=reason,
            origin=identity.origin.value,
        )
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                    severity=EventSeverity.INFO,
                    summary=(
                        "Permission rules set on agent "
                        f"{summary_label(binding.agent_name, agent_id)} for credential "
                        f"{summary_label(binding.credential_name, credential_id)}"
                    ),
                    data={"agent_id": agent_id, "credential_id": credential_id},
                    # Subject is the agent, so its owner sees the rules change.
                    created_by=agent_id,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )
        except Exception:
            logger.warning(
                "telemetry_emit_failed",
                event_type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                exc_info=True,
            )

    async def list_agent_permissions(
        self, credential_id: str, agent_id: str, *, identity: Identity
    ) -> list[AgentPermissionRule]:
        """List the ordered PBAC rules for a direct ``(agent, credential)`` binding."""
        await self._require_visible_binding(credential_id, agent_id, identity=identity)
        async with self._ctx.control_db.session() as session:
            return await AgentPermissionRuleRepository.list_rules(session, agent_id, credential_id)

    async def replace_agent_permissions(
        self,
        credential_id: str,
        agent_id: str,
        rules: list[dict[str, object]],
        *,
        identity: Identity,
    ) -> list[AgentPermissionRule]:
        """Replace the full user-rule list for a binding (idempotent PUT)."""
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        async with self._ctx.control_db.transaction() as session:
            result = await AgentPermissionRuleRepository.replace_user_rules(
                session, agent_id, credential_id, rules, created_by=identity.sub
            )
        await self._record_rules_change(
            credential_id, agent_id, binding, identity=identity, reason="replace permission rules"
        )
        return result

    async def patch_agent_permissions(
        self,
        credential_id: str,
        agent_id: str,
        *,
        identity: Identity,
        add: list[dict[str, object]] | None = None,
        remove: list[int] | None = None,
    ) -> list[AgentPermissionRule]:
        """Additively add and/or remove user rules on a binding."""
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        async with self._ctx.control_db.transaction() as session:
            result = await AgentPermissionRuleRepository.patch_rules(
                session, agent_id, credential_id, add=add, remove=remove, created_by=identity.sub
            )
        await self._record_rules_change(
            credential_id, agent_id, binding, identity=identity, reason="patch permission rules"
        )
        return result

    async def test_agent_permissions(
        self,
        credential_id: str,
        agent_id: str,
        *,
        method: str,
        path: str,
        operation_id: str | None,
        identity: Identity,
    ) -> PermissionTestResult:
        """Dry-run permission evaluation for a ``(method, path, operation_id)`` triple.

        Unlike the toolkit ``:test`` there is **no vendor pooling**: the direct
        binding's rules are a single ordered first-match-wins list, which is
        the point of the per-binding model. When the binding points at a
        shared ``permission_rule_set`` the set's list is what gets evaluated —
        inline rules are dormant while a set is attached, and the dry-run must
        not lie about that. Default-deny when nothing matches. The broker's
        condition-less skip is honoured so a bare ``allow`` or
        ``require-approval`` with no constraints matches a dry-run no more than
        it matches a real request.
        """
        binding = await self._require_visible_binding(credential_id, agent_id, identity=identity)
        async with self._ctx.control_db.session() as session:
            if binding.rule_set_id is not None:
                rules: Sequence[
                    AgentPermissionRule | PermissionRuleSetRule
                ] = await PermissionRuleSetRepository.list_rules(session, binding.rule_set_id)
            else:
                rules = await AgentPermissionRuleRepository.list_rules(
                    session, agent_id, credential_id
                )

        # The broker refuses a path whose dot segments are spelled ambiguously
        # (``ambiguous_path``) before any rule runs; the dry-run must not
        # report such a path as allowed.
        if has_ambiguous_traversal(path):
            return PermissionTestResult(
                allowed=False,
                matched=False,
                effect=None,
                rule_index=None,
                credential_id=None,
                is_system=None,
            )
        compiled = [
            PermissionRule(
                effect=rule.effect,
                methods=normalize_methods(rule.methods),
                path=compile_matcher(rule.path, str(rule.match_mode or "regex")),
                operations=tuple(rule.operations) if rule.operations is not None else None,
            )
            for rule in rules
        ]
        # The broker enforces on the normalized server-relative path; normalize
        # the dry-run input the same way so ``/%61dmin`` previews as ``/admin``.
        idx = first_matching_rule(
            compiled,
            method=method,
            path=normalize_path(path),
            operation_id=operation_id,
            binding=f"{agent_id}:{credential_id}",
        )
        if idx is not None:
            rule = rules[idx]
            return PermissionTestResult(
                allowed=rule.effect.lower() == "allow",
                matched=True,
                effect=rule.effect,
                rule_index=idx,
                credential_id=credential_id,
                is_system=rule.is_system,
            )
        return PermissionTestResult(
            allowed=False,
            matched=False,
            effect=None,
            rule_index=None,
            credential_id=None,
            is_system=None,
        )

    # --- Shared permission rule sets (theme 5 phase 1, Q-04) ---

    async def attach_binding_rule_set(
        self,
        credential_id: str,
        agent_id: str,
        rule_set_id: str,
        *,
        identity: Identity,
    ) -> None:
        """Point a direct binding at a shared rule set.

        While attached, the set's ordered list is the binding's effective
        policy and its inline rules are dormant (they survive untouched for
        when the set is detached). The set must exist — the pointer is FK-less
        across the DB seam, so this check plus the delete-time
        ``rule_set_in_use`` refusal are the integrity guard.

        The caller must also be allowed to attach the set
        (:meth:`_may_attach_rule_set`), because whoever may edit the set then
        decides what the agent may do. Re-attaching the set a binding already
        points at is an idempotent no-op that skips that check, so an existing
        attachment keeps working for the binding's owner.
        """
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        async with self._ctx.control_db.session() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
        if rule_set is None:
            raise RuleSetNotFoundError(rule_set_id)
        if binding.rule_set_id == rule_set_id:
            return
        if not self._may_attach_rule_set(rule_set, identity):
            raise RuleSetAttachDeniedError(rule_set_id)
        async with self._ctx.admin_db.transaction() as session:
            await PrerequisiteRepository.set_binding_rule_set(
                session, agent_id=agent_id, credential_id=credential_id, rule_set_id=rule_set_id
            )
        await self._record_rules_change(
            credential_id,
            agent_id,
            binding,
            identity=identity,
            reason=f"attach rule set {rule_set_id}",
        )

    async def detach_binding_rule_set(
        self, credential_id: str, agent_id: str, *, identity: Identity
    ) -> None:
        """Detach the binding's shared rule set — inline rules apply again.

        Idempotent: detaching a binding that already runs on inline rules is
        a no-op, not an error.
        """
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        if binding.rule_set_id is None:
            return
        async with self._ctx.admin_db.transaction() as session:
            await PrerequisiteRepository.set_binding_rule_set(
                session, agent_id=agent_id, credential_id=credential_id, rule_set_id=None
            )
        await self._record_rules_change(
            credential_id,
            agent_id,
            binding,
            identity=identity,
            reason=f"detach rule set {binding.rule_set_id}",
        )

    @staticmethod
    def _may_mutate_rule_set(rule_set: PermissionRuleSet, identity: Identity) -> bool:
        """Write gate for a shared rule set: ``org:admin``, or the creator of a non-curated set.

        Everyone passing the route's read permission may *see* a shared set —
        it carries policy, not secrets, and a binding pointing at it makes
        its contents the binding owner's business. Editing a set changes the
        policy of every binding attached to it, so a curated set (which any
        ``credentials:write`` holder may attach) is editable only by
        ``org:admin``, including after its creator loses that permission.
        """
        if ORG_ADMIN in identity.permissions:
            return True
        return not rule_set.curated and rule_set.created_by == identity.sub

    @staticmethod
    def _may_attach_rule_set(rule_set: PermissionRuleSet, identity: Identity) -> bool:
        """Attach gate for a shared rule set: ``org:admin``, the creator, or any caller if curated.

        The caller already holds ``credentials:write`` (the route requires it)
        and may write the binding's rules. Attaching another user's
        non-curated set is refused: its creator could later rewrite the
        attached agent's policy.
        """
        if ORG_ADMIN in identity.permissions or rule_set.curated:
            return True
        return rule_set.created_by is not None and rule_set.created_by == identity.sub

    async def create_rule_set(
        self,
        *,
        name: str,
        description: str | None,
        rules: list[dict[str, object]],
        identity: Identity,
    ) -> tuple[PermissionRuleSet, list[PermissionRuleSetRule]]:
        """Create a named shared rule set, optionally with its initial ordered rules.

        A set an ``org:admin`` creates is recorded as curated (see
        :meth:`_may_attach_rule_set`).
        """
        async with self._ctx.control_db.transaction() as session:
            if await PermissionRuleSetRepository.get_by_name(session, name) is not None:
                raise RuleSetNameConflictError(name)
            rule_set = await PermissionRuleSetRepository.create(
                session,
                name=name,
                description=description,
                created_by=identity.sub,
                curated=ORG_ADMIN in identity.permissions,
            )
            set_rules = await PermissionRuleSetRepository.replace_user_rules(
                session, rule_set.id, rules, created_by=identity.sub
            )
        await self._record_rule_set_change(rule_set.id, identity=identity, reason="create rule set")
        return rule_set, set_rules

    async def get_rule_set(
        self, rule_set_id: str, *, identity: Identity
    ) -> tuple[PermissionRuleSet, list[PermissionRuleSetRule], int]:
        """Return a rule set, its ordered rules, and its referencing-binding count."""
        async with self._ctx.control_db.session() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            rules = await PermissionRuleSetRepository.list_rules(session, rule_set_id)
        async with self._ctx.admin_db.session() as session:
            binding_count = await PrerequisiteRepository.count_bindings_for_rule_set(
                session, rule_set_id
            )
        return rule_set, rules, binding_count

    async def list_rule_sets(
        self, *, cursor: str | None = None, limit: int = 50, identity: Identity
    ) -> tuple[list[tuple[PermissionRuleSet, int]], bool, str | None]:
        """List rule sets with per-set rule counts. Returns (data, has_more, next_cursor)."""
        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)
        async with self._ctx.control_db.session() as session:
            rows = await PermissionRuleSetRepository.list_page(
                session, cursor=decoded_cursor, limit=limit + 1
            )
            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]
            counts = await PermissionRuleSetRepository.rule_counts(session, [r.id for r in rows])
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return [(r, counts.get(r.id, 0)) for r in rows], has_more, next_cursor

    async def update_rule_set(
        self,
        rule_set_id: str,
        *,
        identity: Identity,
        name: str | None = None,
        description: str | None = None,
    ) -> PermissionRuleSet:
        """Rename or re-describe a rule set (see :meth:`_may_mutate_rule_set`)."""
        async with self._ctx.control_db.transaction() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            if not self._may_mutate_rule_set(rule_set, identity):
                raise RuleSetAccessDeniedError(rule_set_id, curated=rule_set.curated)
            if name is not None and name != rule_set.name:
                if await PermissionRuleSetRepository.get_by_name(session, name) is not None:
                    raise RuleSetNameConflictError(name)
                rule_set.name = name
            if description is not None:
                rule_set.description = description
            await session.flush()
        await self._record_rule_set_change(rule_set_id, identity=identity, reason="update rule set")
        return rule_set

    async def replace_rule_set_rules(
        self,
        rule_set_id: str,
        rules: list[dict[str, object]],
        *,
        identity: Identity,
    ) -> list[PermissionRuleSetRule]:
        """Replace a set's ordered user-rule list (idempotent PUT).

        This is the single-place edit rule grouping exists for: every binding
        pointing at the set picks the new list up at once.
        """
        async with self._ctx.control_db.transaction() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            if not self._may_mutate_rule_set(rule_set, identity):
                raise RuleSetAccessDeniedError(rule_set_id, curated=rule_set.curated)
            result = await PermissionRuleSetRepository.replace_user_rules(
                session, rule_set_id, rules, created_by=identity.sub
            )
        await self._record_rule_set_change(
            rule_set_id, identity=identity, reason="replace rule set rules"
        )
        return result

    async def delete_rule_set(self, rule_set_id: str, *, identity: Identity) -> None:
        """Delete a rule set nothing references (409 rule_set_in_use otherwise).

        The binding's ``rule_set_id`` pointer is FK-less across the DB seam,
        so this application-level check is what keeps a set from vanishing
        under bindings that still evaluate through it.
        """
        async with self._ctx.control_db.session() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            if not self._may_mutate_rule_set(rule_set, identity):
                raise RuleSetAccessDeniedError(rule_set_id, curated=rule_set.curated)
        async with self._ctx.admin_db.session() as session:
            binding_count = await PrerequisiteRepository.count_bindings_for_rule_set(
                session, rule_set_id
            )
        if binding_count:
            raise RuleSetInUseError(rule_set_id, binding_count)
        async with self._ctx.control_db.transaction() as session:
            deleted = await PermissionRuleSetRepository.delete_by_id(session, rule_set_id)
        if not deleted:
            raise RuleSetNotFoundError(rule_set_id)
        await self._record_rule_set_change(rule_set_id, identity=identity, reason="delete rule set")

    async def _record_rule_set_change(
        self, rule_set_id: str, *, identity: Identity, reason: str
    ) -> None:
        """Audit + telemetry for a rule-set mutation."""
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.PERMISSION_RULE_SET,
            target_id=rule_set_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            reason=reason,
            origin=identity.origin.value,
        )
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                    severity=EventSeverity.INFO,
                    summary=f"Permission rule set {rule_set_id}: {reason}",
                    created_by=identity.sub,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )
        except Exception:
            logger.warning(
                "telemetry_emit_failed",
                event_type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                exc_info=True,
            )

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
            identity,
            Credential,
            bound_credential_ids=await self._bound_credential_ids(identity),
            owned_agent_ids=await self._owned_agent_ids(identity),
            include_shared=True,
        )

        async with self._ctx.control_db.session() as session:
            rows = await CredentialRepository.list_all(
                session, cursor=decoded_cursor, limit=limit, vendor=vendor, filters=access_filters
            )

            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]

            names = await OAuthAppRegistrationRepository.get_names_by_ids(
                session, _registration_ids(rows)
            )
            data = [self._to_redacted(r, registration_names=names) for r in rows]
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = encode_cursor(last.created_at, last.id)

        return CredentialPage(data=data, has_more=has_more, next_cursor=next_cursor)

    async def _validated_oauth2_url(self, field: str, url: str | None) -> str | None:
        """Check an OAuth2 endpoint against ``control.egress``; ``None``/empty passes through.

        Resolves DNS, so callers run it before opening a write transaction.
        """
        if not url:
            return None
        try:
            return await validate_upstream_url_async(url, resolved_control_egress(self._ctx.config))
        except ValueError as exc:
            raise InvalidCredentialInputError(f"Invalid {field}: {exc}") from exc

    @staticmethod
    async def _get_updatable(
        session: Any,
        credential_id: str,
        payload: CredentialUpdate,
        access_filters: Sequence[Any],
    ) -> Credential:
        """Load a credential the caller can see, rejecting a change of ``type``."""
        credential = await CredentialRepository.get_by_id(
            session, credential_id, filters=access_filters
        )
        if credential is None:
            raise CredentialNotFoundError(credential_id)
        if payload.type != to_wire(StoredCredentialType(credential.type)):
            raise ImmutableFieldError("type")
        return credential

    async def update(
        self, credential_id: str, payload: CredentialUpdate, *, identity: Identity
    ) -> CredentialRedactedView:
        """Update/rotate a credential.

        Payload-only checks run first. The DNS-resolving ``token_url``
        validation runs before the write transaction opens, so no resolver
        call holds a DB write lock, and after a read-only existence and type
        check, so not-found and type-mismatch errors take precedence over an
        invalid ``token_url``. The transaction repeats that check against the
        row it writes.
        """
        self._validate_update_fields(payload)
        access_filters = build_access_filters(identity, Credential, for_write=True)
        validated_token_url: str | None = None
        if payload.type == CredentialType.OAUTH2 and payload.token_url is not None:
            async with self._ctx.control_db.session() as session:
                await self._get_updatable(session, credential_id, payload, access_filters)
            validated_token_url = await self._validate_update_token_url(payload)
        oauth_token_dropped = False
        async with self._ctx.control_db.transaction() as session:
            credential = await self._get_updatable(session, credential_id, payload, access_filters)

            before_state = self._audit_snapshot(credential)

            # The api_key parameter binding (field_name/location) is derived from
            # the API spec at create time and must not drift afterwards — a wrong
            # binding silently mis-injects the key (e.g. ?Default=<key>, #589).
            # The SPA still echoes the stored values on every PATCH, so only an
            # actual *change* is a violation; matching echoes are a no-op.
            if payload.type == CredentialType.API_KEY and (
                payload.field_name is not None or payload.location is not None
            ):
                cak = await CustomerAPIKeyRepository.get_by_credential(session, credential_id)
                if cak is not None:
                    if payload.field_name is not None and payload.field_name != cak.field_name:
                        raise ImmutableFieldError("field_name")
                    if payload.location is not None and str(payload.location) != cak.location:
                        raise ImmutableFieldError("location")

            # Track whether a mutating field was provided so `updated_at` moves
            # iff the PATCH could persist a change — an unconditional bump makes
            # the timestamp a lying signal (#739): a PATCH that only echoes the
            # stored field_name/location (or provides nothing) must leave it
            # frozen. Note this keys off "a mutating field was provided", not a
            # value diff: re-sending the current name/active still counts as
            # changed. The SPA omits unchanged fields, so this is a no-op in
            # practice; value-comparing every branch isn't worth the complexity.
            changed = False

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
                changed = True

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
                changed = True

            elif payload.type == CredentialType.API_KEY and payload.key is not None:
                encrypted = encryption.encrypt(payload.key)
                preview = encryption.preview(payload.key)
                await CustomerAPIKeyRepository.update_key(
                    session,
                    credential_id,
                    encrypted_key=encrypted,
                    key_preview=preview,
                )
                changed = True

            elif payload.type == CredentialType.BASIC:
                if payload.username is not None or payload.password is not None:
                    encrypted_pw = (
                        encryption.encrypt(payload.password)
                        if payload.password is not None
                        else None
                    )
                    await BasicCredentialRepository.update(
                        session,
                        credential_id,
                        username=payload.username,
                        encrypted_password=encrypted_pw,
                    )
                    changed = True

            elif payload.type == CredentialType.OAUTH2 and (
                payload.client_secret is not None
                or payload.token_url is not None
                or payload.scopes is not None
            ):
                oauth_token_dropped = await self._apply_oauth2_update(
                    session, credential, payload, validated_token_url=validated_token_url
                )
                changed = True

            elif payload.type == CredentialType.SIGV4:
                # A keypair rotation must supply both halves together; the
                # access_key_id alone is meaningless without its secret.
                if (payload.access_key_id is None) != (payload.secret_access_key is None):
                    raise InvalidCredentialInputError(
                        "access_key_id and secret_access_key must be rotated together"
                    )
                encrypted_sig = (
                    encryption.encrypt(payload.secret_access_key)
                    if payload.secret_access_key is not None
                    else None
                )
                sig_preview = (
                    encryption.preview(payload.secret_access_key)
                    if payload.secret_access_key is not None
                    else None
                )
                encrypted_session = (
                    encryption.encrypt(payload.session_token)
                    if payload.session_token is not None
                    else None
                )
                if (
                    payload.access_key_id is not None
                    or encrypted_sig is not None
                    or encrypted_session is not None
                    or payload.clear_session_token
                    or payload.aws_region is not None
                    or payload.aws_service is not None
                ):
                    await Sigv4CredentialRepository.update(
                        session,
                        credential_id,
                        access_key_id=payload.access_key_id,
                        encrypted_secret_access_key=encrypted_sig,
                        secret_preview=sig_preview,
                        encrypted_session_token=encrypted_session,
                        clear_session_token=payload.clear_session_token,
                        region=payload.aws_region,
                        service=payload.aws_service,
                    )
                    changed = True

            updated = await CredentialRepository.get_by_id(session, credential_id)
            assert updated is not None
            if changed:
                updated.updated_at = datetime.now(UTC)
            await session.flush()
            names = await OAuthAppRegistrationRepository.get_names_by_ids(
                session, _registration_ids([updated])
            )
            view = self._to_redacted(updated, registration_names=names)
            after_state = self._audit_snapshot(updated)
            after_state.update(self._rotation_markers(payload))
            if oauth_token_dropped:
                after_state["oauth_token_dropped"] = True

        # A PATCH that persisted nothing (e.g. only echoed field_name/location)
        # is a no-op — no timestamp bump and no audit noise.
        if changed:
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
        access_filters = build_access_filters(identity, Credential, for_write=True)
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

    def _to_redacted(
        self, credential: Any, *, registration_names: dict[str, str]
    ) -> CredentialRedactedView:
        """Project an ORM Credential to a redacted view."""
        stored_type = StoredCredentialType(credential.type)
        wire_type = to_wire(stored_type)

        details: (
            BearerTokenRedacted
            | ApiKeyRedacted
            | BasicAuthRedacted
            | OAuth2Redacted
            | NoAuthRedacted
            | Sigv4Redacted
        )

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
            is_auth_code = stored_type == StoredCredentialType.OAUTH2_AUTHORIZATION_CODE
            is_device_code = stored_type == StoredCredentialType.OAUTH2_DEVICE_CODE
            # Device-flow credentials live on ``device_authorization_credentials``;
            # every other OAuth2 variant lives on ``oauth_client_credentials``.
            # Read from the right relation so the redacted view doesn't
            # report an empty client_id / a misleading grant_type
            # (handover follow-up #5).
            dfc = credential.device_authorization_credential if is_device_code else None
            occ = None if is_device_code else credential.oauth_client_credential
            connected: bool | None = None
            if is_auth_code or is_device_code:
                # A provider that connects as an account reference (e.g.
                # Pipedream) holds no local token row — the ref alone means
                # the sign-in finished. On any other provider the ref is a
                # display name only, and the token row decides.
                if credential.provider_account_ref and self._ctx.providers.mints_from_account_ref(
                    credential.provider
                ):
                    connected = True
                else:
                    # oauth_token is selectin-eager on the ORM, so this is free.
                    token = credential.oauth_token
                    if token is None or token.revoked_at is not None:
                        connected = False
                    else:
                        # A live row that has expired with no refresh token
                        # cannot mint again — the sign-in must be redone.
                        connected = (
                            token.encrypted_refresh_token is not None
                            or token.expires_at is None
                            or token.expires_at > datetime.now(UTC)
                        )
            grant_type = (
                "device_code"
                if is_device_code
                else "authorization_code"
                if is_auth_code
                else "client_credentials"
            )
            details = OAuth2Redacted(
                client_id=(dfc.client_id if dfc else "") or (occ.client_id if occ else ""),
                token_url=(dfc.token_url if dfc else "") or (occ.token_url if occ else ""),
                grant_type=grant_type,
                scopes=(
                    (dfc.granted_scopes or dfc.requested_scopes or None)
                    if dfc
                    else (occ.scope.split() if occ and occ.scope else None)
                ),
                connected=connected,
            )
        elif wire_type == CredentialType.NO_AUTH:
            details = NoAuthRedacted()
        elif wire_type == CredentialType.SIGV4:
            sig = credential.sigv4_credential
            details = Sigv4Redacted(
                access_key_id=sig.access_key_id if sig else "",
                secret_preview=sig.secret_preview if sig else None,
                has_session_token=bool(sig and sig.encrypted_session_token),
                aws_region=sig.region if sig else "",
                aws_service=sig.service if sig else "",
            )
        else:
            details = BearerTokenRedacted(token_preview=None)

        # Shared-registration provenance. ``registration_names`` is batch-
        # loaded by the caller: ``Credential.oauth_app_registration`` is
        # ``lazy="raise"`` so credential reads never drag in the registration
        # and its sealed client secret. ``None`` when the credential wasn't
        # minted through a shared registration.
        oar_id = credential.oauth_app_registration_id
        oar_name = registration_names.get(oar_id) if oar_id is not None else None

        return CredentialRedactedView(
            credential_id=credential.id,
            type=wire_type,
            name=credential.name,
            api=APIReference(
                vendor=credential.api_vendor,
                name=credential.api_name or "",
                version=credential.api_version or "",
            ),
            catalog_api_id=credential.catalog_api_id,
            provider=credential.provider,
            provider_account_ref=credential.provider_account_ref,
            active=credential.active,
            created_by=credential.created_by,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
            details=details,
            server_variables=credential.server_variables,
            oauth_app_registration_id=oar_id,
            oauth_app_registration_name=oar_name,
        )

    async def _apply_oauth2_update(
        self,
        # ``Any`` because control services must not import sqlalchemy
        # (tests/arch/test_no_direct_db.py); the repos type it as AsyncSession.
        session: Any,
        credential: Credential,
        payload: CredentialUpdate,
        *,
        validated_token_url: str | None,
    ) -> bool:
        """Apply each provided OAuth2 client field independently.

        A token_url or scopes edit does not depend on the caller re-sending
        the client secret; ``validated_token_url`` is the already
        SSRF-validated ``payload.token_url``. ``scopes=[]`` clears the stored
        scopes; omitting it keeps them.

        A token_url whose origin (scheme, host, port) differs from the stored
        one redirects where the stored client secret and refresh token are
        sent, and neither can be read back by the caller. So a cross-origin
        change requires ``client_secret`` in the same PATCH, and it drops the
        live ``oauth_token`` row so the old grant is never presented to the
        new origin; the credential must be reconnected. Same-origin path edits
        apply on their own.

        Raises ``InvalidCredentialInputError`` (400) when the credential uses
        a shared app registration, when it has no stored client config (a
        managed provider), on a cross-origin token_url without
        ``client_secret``, and for device_code credentials on a
        ``client_secret``, a cross-origin token_url, or a token_url/scopes edit
        while a device flow is pending.

        Returns ``True`` when the live ``oauth_token`` row was dropped.
        """
        credential_id = credential.id
        if credential.oauth_app_registration_id is not None:
            # Shared-registration credentials resolve their client config
            # (token endpoint, client id/secret, scopes) from the registration
            # at connect/refresh time, so a per-credential edit would be dead.
            raise InvalidCredentialInputError(
                "This oauth2 credential uses a shared app registration; "
                "edit the registration instead"
            )

        if StoredCredentialType(credential.type) == StoredCredentialType.OAUTH2_DEVICE_CODE:
            await self._apply_device_code_update(
                session, credential_id, payload, validated_token_url=validated_token_url
            )
            return False

        occ = await OAuthClientCredentialRepository.get_by_credential(session, credential_id)
        if occ is None:
            raise _no_client_config_error()
        cross_origin = validated_token_url is not None and not _same_origin(
            validated_token_url, occ.token_url
        )
        if cross_origin and payload.client_secret is None:
            raise InvalidCredentialInputError(
                "Changing the token_url origin (scheme, host or port) requires "
                "re-entering client_secret in the same request"
            )
        if cross_origin:
            # Serialise with the broker's single-flight refresh before any
            # write: a refresh already holding the lock finishes against the
            # stored origin, and the next one finds the token row gone (and
            # fails as needs-reconnect) rather than presenting the old refresh
            # token to the new origin.
            await OAuthTokenRepository.acquire_refresh_lock(session, credential_id)
        await OAuthClientCredentialRepository.update(
            session,
            credential_id,
            encrypted_client_secret=(
                self._ctx.encryption.encrypt(payload.client_secret)
                if payload.client_secret is not None
                else None
            ),
            token_url=validated_token_url,
            scope=" ".join(payload.scopes) if payload.scopes is not None else SCOPE_UNSET,
        )
        if not cross_origin:
            return False
        dropped = await OAuthTokenRepository.delete_by_credential(session, credential_id)
        if dropped:
            # Re-load the eager relationship so the returned view reports the
            # credential as disconnected.
            await session.refresh(credential, attribute_names=["oauth_token"])
        return dropped

    @staticmethod
    async def _apply_device_code_update(
        session: Any,
        credential_id: str,
        payload: CredentialUpdate,
        *,
        validated_token_url: str | None,
    ) -> None:
        """Apply a device_code (RFC 8628) client-config edit; see ``_apply_oauth2_update``.

        Device flow is a public client: there is no secret to rotate or to
        re-enter as proof of ownership, so a cross-origin token_url is refused
        outright. The device_code minted at the (unchanged) authorization
        endpoint would otherwise be polled at, and redeemable by, the new
        origin. A pending flow polls with the stored token_url and stamps the
        confirmed scopes on success, so both stay frozen until it ends.
        """
        if payload.client_secret is not None:
            raise InvalidCredentialInputError(
                "client_secret is not used by device_code credentials"
            )
        dfc = await DeviceAuthorizationCredentialRepository.get_by_credential(
            session, credential_id
        )
        if dfc is None:
            raise _no_client_config_error()
        # A flow past its expiry can no longer complete; it only lingers until
        # the poll scanner retires it, so it must not keep the edit blocked.
        if dfc.encrypted_device_code is not None and (
            dfc.device_code_expires_at is None or dfc.device_code_expires_at > datetime.now(UTC)
        ):
            raise InvalidCredentialInputError(
                "A device authorization flow is in progress for this credential; "
                "retry once it completes or expires"
            )
        if validated_token_url is not None and not _same_origin(validated_token_url, dfc.token_url):
            raise InvalidCredentialInputError(
                "device_code credentials cannot move token_url to a different origin "
                "(scheme, host or port); create a new credential instead"
            )
        device_fields: dict[str, Any] = {}
        if validated_token_url is not None:
            device_fields["token_url"] = validated_token_url
        if payload.scopes is not None:
            device_fields["requested_scopes"] = payload.scopes or None
            # ``granted_scopes`` records what the last connect asked for and
            # takes precedence in the redacted view; the next connect stamps
            # it again, so drop it or the edit is masked. The live token keeps
            # its own granted ``scope``.
            device_fields["granted_scopes"] = None
        await DeviceAuthorizationCredentialRepository.update_fields(
            session, credential_id, **device_fields
        )

    async def _validate_update_token_url(self, payload: CredentialUpdate) -> str | None:
        """SSRF-validate an OAuth2 ``token_url`` edit; ``None`` when none is sent.

        The single update-path call site for upstream-URL validation, under
        the control-plane egress allowlist. It resolves DNS off the event
        loop, so callers run it before opening the write transaction.
        """
        if payload.type != CredentialType.OAUTH2 or payload.token_url is None:
            return None
        try:
            return await validate_upstream_url_async(
                payload.token_url, resolved_control_egress(self._ctx.config)
            )
        except ValueError as exc:
            raise InvalidCredentialInputError(f"Invalid token_url: {exc}") from exc

    @staticmethod
    def _audit_snapshot(credential: Credential) -> dict[str, Any]:
        """Non-secret state of a credential for the audit before/after diff.

        Values are copied so a later in-place ORM mutation can't rewrite the
        ``before`` snapshot. Secret material never appears here; rotations are
        recorded as boolean markers by ``_rotation_markers``.
        """
        state: dict[str, Any] = {
            "name": credential.name,
            "active": credential.active,
            "server_variables": (
                dict(credential.server_variables) if credential.server_variables else None
            ),
        }
        stored_type = StoredCredentialType(credential.type)
        wire_type = to_wire(stored_type)
        if wire_type == CredentialType.BASIC:
            bc = credential.basic_credential
            state["username"] = bc.username if bc else None
        elif wire_type == CredentialType.OAUTH2:
            if stored_type == StoredCredentialType.OAUTH2_DEVICE_CODE:
                dfc = credential.device_authorization_credential
                state["token_url"] = dfc.token_url if dfc else None
                state["scopes"] = list(dfc.requested_scopes or []) if dfc else []
                state["granted_scopes"] = list(dfc.granted_scopes or []) if dfc else []
            else:
                occ = credential.oauth_client_credential
                state["token_url"] = occ.token_url if occ else None
                state["scopes"] = occ.scope.split() if occ and occ.scope else []
        elif wire_type == CredentialType.SIGV4:
            sig = credential.sigv4_credential
            state["access_key_id"] = sig.access_key_id if sig else None
            state["aws_region"] = sig.region if sig else None
            state["aws_service"] = sig.service if sig else None
            state["has_session_token"] = bool(sig and sig.encrypted_session_token)
        return state

    @staticmethod
    def _rotation_markers(payload: CredentialUpdate) -> dict[str, bool]:
        """``{"<secret>_rotated": True}`` for each secret the update replaced."""
        return {
            marker: True
            for field, marker in _SECRET_ROTATION_MARKERS.get(payload.type, ())
            if getattr(payload, field) is not None
        }

    @staticmethod
    def _validate_update_fields(payload: CredentialUpdate) -> None:
        """Reject blank name/secret/signing fields and malformed scope items.

        ``None`` means "leave unchanged"; a blank string would otherwise be
        stored and injected as e.g. ``X-Api-Key:  `` or ``Basic :pw``.
        """
        for field in _NON_EMPTY_UPDATE_FIELDS:
            if _is_blank_str(getattr(payload, field)):
                raise InvalidCredentialInputError(f"Field '{field}' must not be empty")
        if payload.scopes is not None:
            _validate_scope_tokens(payload.scopes)

    def _validate_create_fields(self, payload: CredentialCreate, *, managed: bool) -> None:
        """Validate required fields per type before touching encryption/DB.

        A whitespace-only value counts as missing, as on update.
        """
        if payload.scopes is not None:
            _validate_scope_tokens(payload.scopes)
        if payload.type == CredentialType.BEARER_TOKEN:
            if _is_blank_str(payload.token, missing=True):
                raise InvalidCredentialInputError("Field 'token' is required for bearer_token")
        elif payload.type == CredentialType.API_KEY:
            if _is_blank_str(payload.key, missing=True):
                raise InvalidCredentialInputError("Field 'key' is required for api_key")
            if _is_blank_str(payload.location, missing=True):
                raise InvalidCredentialInputError("Field 'location' is required for api_key")
            if _is_blank_str(payload.field_name, missing=True):
                raise InvalidCredentialInputError("Field 'field_name' is required for api_key")
        elif payload.type == CredentialType.BASIC:
            if _is_blank_str(payload.username, missing=True):
                raise InvalidCredentialInputError("Field 'username' is required for basic")
            if _is_blank_str(payload.password, missing=True):
                raise InvalidCredentialInputError("Field 'password' is required for basic")
        elif payload.type == CredentialType.OAUTH2 and not managed:
            if _is_blank_str(payload.token_url, missing=True):
                raise InvalidCredentialInputError("Field 'token_url' is required for oauth2")
            if _is_blank_str(payload.client_id, missing=True):
                raise InvalidCredentialInputError("Field 'client_id' is required for oauth2")
            # Device flow (RFC 8628) is a public-client flow — no secret.
            if payload.grant_type != "device_code" and _is_blank_str(
                payload.client_secret, missing=True
            ):
                raise InvalidCredentialInputError("Field 'client_secret' is required for oauth2")
        elif payload.type == CredentialType.SIGV4:
            if _is_blank_str(payload.access_key_id, missing=True):
                raise InvalidCredentialInputError("Field 'access_key_id' is required for sigv4")
            if _is_blank_str(payload.secret_access_key, missing=True):
                raise InvalidCredentialInputError("Field 'secret_access_key' is required for sigv4")
            if _is_blank_str(payload.aws_region, missing=True):
                raise InvalidCredentialInputError("Field 'aws_region' is required for sigv4")
            if _is_blank_str(payload.aws_service, missing=True):
                raise InvalidCredentialInputError("Field 'aws_service' is required for sigv4")

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

    async def _catalog_registered_scope(
        self, scope: CredentialScope, catalog_api_id: str | None
    ) -> CredentialScope:
        """The scope naming the API actually imported from ``catalog_api_id``.

        A client picking a catalog entry derives the API name from its id, and
        that derivation is not the importer's: a ``domain/sub`` entry registers
        under its sub segment (or its whole id when sub segments collide), and an
        API imported by an older release keeps the name it was created with. The
        imported row is the authority, so when one carries this catalog id and
        the same vendor, its name replaces the client's. A vendor-wide scope (no
        name) and a scope on another vendor are the caller's choice and stay.
        Best effort, like the coverage check: no registry DB, a slow one, or no
        imported row leaves the scope as given.
        """
        if not catalog_api_id or scope.name is None or not self._ctx.has_db("registry"):
            return scope
        try:
            async with asyncio.timeout(_REGISTRY_PROBE_TIMEOUT_S):
                async with self._ctx.registry_db.session() as session:
                    registered = await RegistryApiLookupRepository.identity_for_catalog_api_id(
                        session, catalog_api_id
                    )
        except Exception:
            logger.warning(
                "credential_catalog_identity_lookup_failed",
                catalog_api_id=catalog_api_id,
                exc_info=True,
            )
            return scope
        if registered is None:
            return scope
        vendor, name = registered
        if vendor != scope.vendor or name == scope.name:
            return scope
        logger.info(
            "credential_catalog_identity_resolved",
            catalog_api_id=catalog_api_id,
            requested_name=scope.name,
            registered_name=name,
        )
        return CredentialScope(vendor=vendor, name=name, version=scope.version)

    async def _unmatched_scope_warning(
        self, scope: CredentialScope
    ) -> _UnmatchedScopeWarning | None:
        """Advisory check: does the canonical scope cover any imported API? (#1020)

        Creating a credential before importing its API is a legitimate order of
        operations (and a standalone control process is not granted the registry
        DB), so this never blocks or fails the create — best-effort only, with a
        hard time bound so a hung registry DB can't stall the create either.
        One registry read fetches the vendor's identities; coverage is evaluated
        here with the broker's ``credential_covers`` semantics, and when nothing is
        covered the same rows name the vendor's imported identities so a near-miss
        (e.g. a #1020 vendor-doubled workspace name) is visible at create time
        rather than as an execute-time 403.

        Events are append-only, so a warning about an API whose import is
        already queued (the UI and ``ensure_imported`` enqueue the import and
        create the credential back to back) would stay false forever; the
        check is skipped when a pending import job may land a covered identity.
        """
        if not self._ctx.has_db("registry"):
            return None
        try:
            async with asyncio.timeout(_REGISTRY_PROBE_TIMEOUT_S):
                async with self._ctx.registry_db.session() as session:
                    identities = await RegistryApiLookupRepository.identities_for_vendor(
                        session, scope.vendor
                    )
        except Exception:
            logger.warning(
                "credential_api_match_check_failed", api_vendor=scope.vendor, exc_info=True
            )
            return None
        if any(
            credential_covers(scope, vendor=scope.vendor, name=name, version=version)
            for name, version in identities
        ):
            return None
        if await self._pending_import_may_cover(scope):
            return None
        reference = _scope_reference(scope)
        # The scope is immutable after create, so "import the API" is only half
        # the remedy — a mis-scoped credential must be re-created.
        message = (
            f"API scope '{reference}' matches no imported API — executions using this "
            "credential will fail. Import a matching API, or delete this credential "
            "and re-create it scoped to an imported identity"
        )
        if identities:
            listed = ", ".join(
                f"{scope.vendor}/{name} ({version})"
                for name, version in identities[:_SIBLING_HINT_LIMIT]
            )
            message += f"; imported APIs for this vendor: {listed}"
        logger.warning(
            "credential_unmatched_api",
            api_vendor=scope.vendor,
            api_name=scope.name,
            api_version=scope.version,
        )
        return _UnmatchedScopeWarning(reference=reference, message=message)

    async def _pending_import_may_cover(self, scope: CredentialScope) -> bool:
        """Whether a queued/running import job may land an identity ``scope`` covers.

        Matches the job source's ``vendor`` / ``api_name`` seeds against the
        scope's vendor and name axes (the version is only known once the spec
        is fetched, so it is not compared). Best-effort and time-bounded like
        the registry read: on failure it answers ``False`` so the advisory
        still fires.
        """
        try:
            async with asyncio.timeout(_REGISTRY_PROBE_TIMEOUT_S):
                async with self._ctx.admin_db.session() as session:
                    sources = await PendingImportLookupRepository.pending_import_sources(session)
        except Exception:
            logger.warning(
                "credential_pending_import_check_failed", api_vendor=scope.vendor, exc_info=True
            )
            return False
        for source in sources:
            vendor = source.get("vendor")
            if not isinstance(vendor, str) or slugify_api_field(vendor) != scope.vendor:
                continue
            if scope.name is None:
                return True
            name = source.get("api_name")
            if isinstance(name, str) and slugify_api_field(name) == scope.name:
                return True
        return False


def _registration_ids(credentials: Sequence[Credential]) -> list[str]:
    """Distinct ``oauth_app_registration_id`` values across ``credentials``."""
    return sorted({c.oauth_app_registration_id for c in credentials if c.oauth_app_registration_id})
