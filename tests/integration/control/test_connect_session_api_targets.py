"""API-target connect sessions end to end over the real control, admin and registry DBs.

Specs are imported through the real registry ingest, the scheme/host lookup is
the real registry service, and every repository below the connect-session
service is real. Covers: scheme selection from the spec only (``auth_type``,
reserved headers, undeclared/absent schemes), host pinning, ``awaiting_app``
and its re-resolution, the review payload and digest, every confirm variant
(secret, own OAuth client, existing credential, re-authorize) with the
granted-scope check, scheme/host re-checks at confirm, the compensating unbind,
the archive race, dedupe and token rotation, open-session caps, ``:reject``
with its cooldown, and ``/status`` answering from recorded outcomes.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.control.core.schema.connect_session_outcomes import ConnectSessionOutcome
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.repos import CredentialRepository
from jentic_one.control.repos.connect_session_outcome_repo import (
    ConnectSessionOutcomeRepository,
)
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.oauth_client_credential_repo import (
    OAuthClientCredentialRepository,
)
from jentic_one.control.services.integrations.connect_session_service import (
    ApiTarget,
    AuthCodeConfirmResult,
    ConfirmChecks,
    ConnectedConfirmResult,
    ConnectSessionService,
    ExistingCredentialConfirm,
    OwnClientConfirm,
    ReauthorizeConfirmResult,
    SecretConfirm,
)
from jentic_one.control.services.integrations.errors import (
    AgentInactiveError,
    AuthTypeNotDeclaredError,
    AuthTypeRequiredError,
    ConfirmKindMismatchError,
    InsufficientGrantedScopesError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    NoDeclaredSchemeError,
    ReauthorizeUnavailableError,
    RecentlyRejectedError,
    ReservedAuthFieldError,
    ReviewStaleError,
    RulesRequiredError,
    SchemeChangedError,
    ServersChangedError,
    TooManyOpenSessionsError,
    UnknownApiError,
    UnpinnedServerHostError,
)
from jentic_one.control.services.integrations.flow_handlers.manual import (
    ApiKeySecret,
    BasicSecret,
    BearerSecret,
)
from jentic_one.control.services.oauth_app_registrations.service import (
    OAuthAppRegistrationService,
)
from jentic_one.control.services.vendors.service import AmbiguousVendorError
from jentic_one.control.web.app import get_exception_handlers
from jentic_one.control.web.routers import integrations as integrations_router
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.services.api_security_lookup_service import ApiSecurityLookupService
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import (
    DirectOAuth2ProviderConfig,
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventType
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.shared.web.errors import request_validation_error_handler

pytestmark = pytest.mark.integration

_VENDOR = "widgets-example"
# Shaped like a catalog import of ``widgets.example/api`` so a config entry or
# shared app for that catalog id covers it.
_CATALOG_ID = "widgets.example/api"
_NAME = "widgets-example-api"
_VERSION = "1.0.0"
_TARGET = ApiTarget(vendor=_VENDOR, name=_NAME, version=_VERSION)

_OWNER_ID = "usr_apitgt_owner"
_STRANGER_ID = "usr_apitgt_stranger"
_AGENT_ID = "agnt_apitgt_scout"
_OTHER_AGENT_ID = "agnt_apitgt_other"

_OWNER = Identity(sub=_OWNER_ID, permissions=["credentials:write", "agents:write"])
_OWNER_NO_AGENTS_WRITE = Identity(sub=_OWNER_ID, permissions=["credentials:write"])
_STRANGER = Identity(sub=_STRANGER_ID, permissions=["credentials:write", "agents:write"])
_ADMIN = Identity(sub="usr_apitgt_root", permissions=["org:admin"])
_AGENT = Identity(sub=_AGENT_ID, permissions=["credentials:write"], actor_type=ActorType.AGENT)

_RULES: list[dict[str, object]] = [
    {"effect": "allow", "methods": ["GET"], "path": "/widgets", "match_mode": "exact"}
]
_SECRET = "sk_live_" + "Z" * 24
_KEY_SCHEME = {"key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}
_OAUTH_SCHEME = {
    "oauth": {
        "type": "oauth2",
        "flows": {
            "authorizationCode": {
                "authorizationUrl": "https://auth.widgets.example/authorize",
                "tokenUrl": "https://auth.widgets.example/token",
                "scopes": {"read": "Read", "write": "Write"},
            }
        },
    }
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _source(
    schemes: dict[str, Any],
    *,
    servers: list[dict[str, Any]] | None = None,
    approved: bool = False,
    name: str = _NAME,
) -> dict[str, Any]:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Widgets", "version": _VERSION, "description": uuid.uuid4().hex},
        "servers": servers if servers is not None else [{"url": "https://api.widgets.example"}],
        "components": {"securitySchemes": schemes},
        "paths": {
            "/widgets": {
                "get": {"operationId": "list", "responses": {"200": {"description": "OK"}}}
            }
        },
    }
    source: dict[str, Any] = {
        "type": "inline",
        "content": json.dumps(spec),
        "filename": "openapi.json",
        "vendor": _VENDOR,
        "api_name": name,
        "version": _VERSION,
        "origin": "catalog",
        "submitted_by": "usr_writer",
    }
    if approved:
        source["host_change_approved"] = "true"
    return source


async def _import(ctx: Context, schemes: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        session=None,
        payload={"sources": [_source(schemes, **kwargs)]},
        created_by="usr_writer",
    )
    revision: dict[str, Any] = result.body["revisions"][0]
    return revision


def _svc(ctx: Context) -> ConnectSessionService:
    return ConnectSessionService(ctx, security_schemes_lookup=ApiSecurityLookupService(ctx))


async def _wipe(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
        for table in (
            OperationURLIndex,
            SecuritySchemeFlow,
            SecurityScheme,
            ServerVariable,
            Server,
            Operation,
            SpecFile,
        ):
            await session.execute(delete(table))
        await session.execute(update(Api).values(current_revision_id=None))
        await session.execute(delete(ApiRevision))
        await session.execute(delete(Api))
        await session.commit()
    async with ctx.control_db.session() as session:
        for control_table in (
            ConnectSessionOutcome,
            ConnectSession,
            OAuthToken,
            OAuthClientCredential,
            Credential,
            OAuthAppRegistration,
        ):
            await session.execute(delete(control_table))
        await session.commit()
    async with ctx.admin_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_apitgt_%'")
        )
        await session.execute(text("DELETE FROM agents WHERE id LIKE 'agnt_apitgt_%'"))
        await session.execute(text("DELETE FROM users WHERE id LIKE 'usr_apitgt_%'"))
        await session.execute(delete(AuditEntry))
        await session.execute(delete(Event).where(Event.type == EventType.CONNECT_SESSION_CREATED))
        await session.commit()


@pytest.fixture()
async def env(
    integration_context: Context, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[Context, None]:
    """Gate on, a clean slate, an owner and two of their agents."""
    ctx = integration_context
    monkeypatch.setattr(ctx.config.control.connect, "manual_flows_enabled", True)
    monkeypatch.setattr(ctx.config.vendors, "entries", {})
    ctx.config.credentials.providers.setdefault(
        "direct_oauth2",
        DirectOAuth2ProviderConfig(
            redirect_uri="https://app.example.com/credentials/oauth/callback"
        ),
    )
    await _wipe(ctx)
    async with ctx.admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name)"
                " VALUES (:id, 'owner@example.test', 'O', 'W')"
            ),
            {"id": _OWNER_ID},
        )
        for agent_id in (_AGENT_ID, _OTHER_AGENT_ID):
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, owner_id, status)"
                    " VALUES (:id, :id, :owner, :owner, 'approved')"
                ),
                {"id": agent_id, "owner": _OWNER_ID},
            )
        await session.commit()
    yield ctx
    await _wipe(ctx)


async def _connect(
    ctx: Context, *, agent_id: str = _AGENT_ID, auth_type: str | None = None, **kwargs: Any
) -> Any:
    return await _svc(ctx).create_session(
        vendor_key="",
        agent_id=agent_id,
        initiator_actor_id=agent_id,
        api_target=_TARGET,
        auth_type=auth_type,
        requested_permission_rules=_RULES,
        reason="needs widgets",
        **kwargs,
    )


async def _row(ctx: Context, session_id: str) -> ConnectSession | None:
    async with ctx.control_db.session() as session:
        return await ConnectSessionRepository.get_by_id(session, session_id)


async def _bound(ctx: Context, agent_id: str, credential_id: str) -> bool:
    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT 1 FROM agent_credential_bindings"
                    " WHERE agent_id = :a AND credential_id = :c"
                ),
                {"a": agent_id, "c": credential_id},
            )
        ).first()
    return row is not None


def _checks(digest: str, *, rules: list[dict[str, object]] | None = None) -> ConfirmChecks:
    return ConfirmChecks(
        permission_rules=_RULES if rules is None else rules,
        expected_agent_id=_AGENT_ID,
        digest=digest,
    )


async def _digest(ctx: Context, session_id: str) -> str:
    review = await _svc(ctx).get_review_data(session_id, poll_token=None, identity=_OWNER)
    return review.digest


async def _confirm_key(
    ctx: Context, session_id: str, *, secret: str = _SECRET, identity: Identity = _OWNER
) -> Any:
    return await _svc(ctx).confirm_variant(
        session_id,
        poll_token=None,
        variant=SecretConfirm(
            kind="api_key",
            secret=ApiKeySecret(key=SecretStr(secret)),
            checks=_checks(await _digest(ctx, session_id)),
        ),
        identity=identity,
    )


# ---------------------------------------------------------------------------
# :connect — the spec decides the scheme and the hosts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schemes", "flow", "stored_type", "location", "field_name"),
    [
        (_KEY_SCHEME, "manual_api_key", "API_KEY", "header", "X-Api-Key"),
        (
            {"b": {"type": "http", "scheme": "bearer"}},
            "manual_bearer",
            "STATIC_BEARER_TOKEN",
            "header",
            "Authorization",
        ),
        (
            {"b": {"type": "http", "scheme": "basic"}},
            "manual_basic",
            "BASIC_AUTH",
            "header",
            "Authorization",
        ),
    ],
)
async def test_connect_picks_the_declared_static_scheme(
    env: Context,
    schemes: dict[str, Any],
    flow: str,
    stored_type: str,
    location: str,
    field_name: str,
) -> None:
    await _import(env, schemes)
    created = await _connect(env)
    assert created.resolved_flow == flow
    assert created.approval_url.endswith(f"/app/agents?approve={created.session_id}")
    row = await _row(env, created.session_id)
    assert row is not None
    assert (row.target_kind, row.state, row.vendor, row.api_name, row.api_version) == (
        "api",
        "created",
        _VENDOR,
        _NAME,
        _VERSION,
    )
    assert (row.scheme_location, row.scheme_field_name) == (location, field_name)
    assert row.pinned_hosts == ["https://api.widgets.example"]
    async with env.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None
    # Name and version pinned: the credential never covers another API.
    assert (credential.type, credential.state, credential.provider) == (
        stored_type,
        "pending",
        "static",
    )
    assert (credential.api_vendor, credential.api_name, credential.api_version) == (
        _VENDOR,
        _NAME,
        _VERSION,
    )


async def test_agent_api_connect_emits_the_rail_event(env: Context) -> None:
    """An agent's API-target request is announced on the activity rail like a
    vendor connect: informational, the agent as subject, never the poll token."""
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    async with env.admin_db.session() as session:
        events = list(
            (
                await session.execute(
                    select(Event).where(Event.type == EventType.CONNECT_SESSION_CREATED)
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    (event,) = events
    assert event.requires_action is False
    assert event.created_by == _AGENT_ID
    assert event.summary == f"Agent '{_AGENT_ID}' asked to connect '{_VENDOR}/{_NAME}'"
    assert event.data is not None
    assert event.data["session_id"] == created.session_id
    assert created.poll_token not in f"{event.summary} {event.detail} {event.data}"


async def test_connect_needs_auth_type_when_several_schemes_are_declared(env: Context) -> None:
    await _import(
        env,
        {**_KEY_SCHEME, "bearer": {"type": "http", "scheme": "bearer"}, **_OAUTH_SCHEME},
    )
    with pytest.raises(AuthTypeRequiredError) as required:
        await _connect(env)
    assert required.value.options == ["bearer", "key", "oauth"]
    with pytest.raises(AuthTypeNotDeclaredError):
        await _connect(env, auth_type="basic")
    by_kind = await _connect(env, auth_type="bearer")
    assert by_kind.resolved_flow == "manual_bearer"


async def test_connect_auth_type_matches_a_scheme_name(env: Context) -> None:
    await _import(env, {**_KEY_SCHEME, "query_key": {"type": "apiKey", "in": "query", "name": "k"}})
    with pytest.raises(AuthTypeRequiredError):
        await _connect(env, auth_type="api_key")
    created = await _connect(env, auth_type="query_key")
    row = await _row(env, created.session_id)
    assert row is not None and (row.scheme_location, row.scheme_field_name) == ("query", "k")


@pytest.mark.parametrize(
    "schemes",
    [{}, {"oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://x.example/.well-known"}}],
)
async def test_connect_without_a_supported_scheme_is_refused(
    env: Context, schemes: dict[str, Any]
) -> None:
    await _import(env, schemes)
    with pytest.raises(NoDeclaredSchemeError):
        await _connect(env)


@pytest.mark.parametrize(
    "header", ["Authorization", "Host", "Cookie", "X-Forwarded-For", "Jentic-Agent", "traceparent"]
)
async def test_connect_refuses_a_key_in_a_reserved_header(env: Context, header: str) -> None:
    await _import(env, {"key": {"type": "apiKey", "in": "header", "name": header}})
    with pytest.raises(ReservedAuthFieldError):
        await _connect(env)


async def test_connect_refuses_a_host_variable_without_enum(env: Context) -> None:
    await _import(
        env,
        _KEY_SCHEME,
        servers=[
            {"url": "https://{tenant}.widgets.example", "variables": {"tenant": {"default": "a"}}}
        ],
    )
    with pytest.raises(UnpinnedServerHostError) as refused:
        await _connect(env)
    assert refused.value.variables == ["tenant"]


async def test_connect_pins_every_enum_host(env: Context) -> None:
    await _import(
        env,
        _KEY_SCHEME,
        servers=[
            {
                "url": "https://{region}.widgets.example",
                "variables": {"region": {"default": "eu", "enum": ["eu", "us"]}},
            }
        ],
    )
    row = await _row(env, (await _connect(env)).session_id)
    assert row is not None
    assert row.pinned_hosts == ["https://eu.widgets.example", "https://us.widgets.example"]


async def test_connect_unknown_api(env: Context) -> None:
    with pytest.raises(UnknownApiError):
        await _connect(env)


async def test_connect_oauth_api_without_an_app_awaits_one(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    created = await _connect(env, requested_scopes=["read"])
    assert created.resolved_flow == "awaiting_app"
    row = await _row(env, created.session_id)
    assert row is not None and row.state == "awaiting_app" and row.vendor_key is None
    async with env.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None and credential.type == "OAUTH2_AUTHORIZATION_CODE"
    status = await _svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert status.status == "pending"


def _covering_entry() -> VendorAuthConfig:
    return VendorAuthConfig(
        vendor=_CATALOG_ID,
        display_name="Widgets",
        flows=[
            VendorAuthorizationCodeFlowConfig(
                client_id="widgets-client",
                client_secret=SecretStr("widgets-client-secret"),
                authorize_url="https://auth.widgets.example/authorize",
                token_url="https://auth.widgets.example/token",
            )
        ],
        scopes=[VendorScopeConfig(name="read", classification="read", default=True)],
    )


async def test_connect_oauth_api_uses_a_covering_config_entry(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    env.config.vendors.entries["widgets"] = _covering_entry()
    created = await _connect(env, requested_scopes=["read"])
    assert created.resolved_flow == "authorization_code"
    row = await _row(env, created.session_id)
    assert row is not None and (row.state, row.vendor_key) == ("created", "widgets")
    review = await _svc(env).get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.vendor_key == "widgets"
    assert [s.name for s in review.scopes] == ["read"]


async def _register_app(ctx: Context, name: str = "Widgets app") -> str:
    view = await OAuthAppRegistrationService(ctx).create_authorization_code(
        name=name,
        api_vendor="widgets",
        catalog_api_id=_CATALOG_ID,
        display_name="Widgets",
        client_id=f"client-{name}",
        client_secret="registration-secret",
        authorize_url="https://auth.widgets.example/authorize",
        token_url="https://auth.widgets.example/token",
        default_scopes=["read"],
        identity=_ADMIN,
    )
    return view.id


async def test_connect_oauth_api_uses_the_one_covering_shared_app(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    registration_id = await _register_app(env)
    created = await _connect(env)
    row = await _row(env, created.session_id)
    assert row is not None and (row.state, row.vendor_key) == ("created", "widgets")
    async with env.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None and credential.oauth_app_registration_id == registration_id


async def test_connect_oauth_api_with_several_shared_apps_is_ambiguous(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    first = await _register_app(env, "one")
    await _register_app(env, "two")
    with pytest.raises(AmbiguousVendorError):
        await _connect(env)
    pinned = await _connect(env, oauth_app_registration_id=first)
    assert pinned.resolved_flow == "authorization_code"


async def test_awaiting_app_moves_to_created_when_a_shared_app_is_registered(
    env: Context,
) -> None:
    await _import(env, _OAUTH_SCHEME)
    created = await _connect(env, requested_scopes=["read"])
    svc = _svc(env)
    assert await svc.resolve_awaiting_app_sessions() == 0
    registration_id = await _register_app(env)
    # Two resolvers race; the CAS lets exactly one move it.
    moved = await asyncio.gather(
        svc.resolve_awaiting_app_sessions(), _svc(env).resolve_awaiting_app_sessions()
    )
    assert sum(moved) == 1
    row = await _row(env, created.session_id)
    assert row is not None
    assert (row.state, row.resolved_flow, row.vendor_key) == (
        "created",
        "authorization_code",
        "widgets",
    )
    async with env.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
    assert credential is not None and credential.oauth_app_registration_id == registration_id


async def test_awaiting_app_resolves_lazily_on_review(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    created = await _connect(env)
    before = await _svc(env).get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert before.state == "awaiting_app"
    env.config.vendors.entries["widgets"] = _covering_entry()
    after = await _svc(env).get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert (after.state, after.resolved_flow) == ("created", "authorization_code")
    # The digest is computed after the move.
    assert after.digest != before.digest
    row = await _row(env, created.session_id)
    assert row is not None
    async with env.control_db.session() as session:
        occ = await OAuthClientCredentialRepository.get_by_credential(session, row.credential_id)
    assert occ is not None and occ.client_id == "widgets-client"


# ---------------------------------------------------------------------------
# Review payload
# ---------------------------------------------------------------------------


async def test_review_carries_server_data_digest_and_can_confirm(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    svc = _svc(env)
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.target_kind == "api"
    assert review.provenance is not None and review.provenance.origin == "catalog"
    assert review.agent is not None
    assert (review.agent.agent_id, review.agent.owner_id) == (_AGENT_ID, _OWNER_ID)
    assert review.scheme is not None
    assert (review.scheme.type, review.scheme.field_name) == ("api_key", "X-Api-Key")
    assert review.pinned_hosts == ["https://api.widgets.example"]
    assert review.requested_permission_rules == _RULES
    assert review.reason == "needs widgets"
    assert review.can_confirm is True
    assert len(review.digest) == 64
    # Stable across reads; viewer-independent.
    again = await svc.get_review_data(created.session_id, poll_token=None, identity=_ADMIN)
    assert again.digest == review.digest

    # The agent holding the token may read the review but never confirm it;
    # an owner lacking agents:write gets no token-less access at all.
    as_agent = await svc.get_review_data(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert as_agent.can_confirm is False
    with pytest.raises(InvalidPollTokenError):
        await svc.get_review_data(
            created.session_id, poll_token=None, identity=_OWNER_NO_AGENTS_WRITE
        )
    stranger = await svc.get_review_data(
        created.session_id, poll_token=created.poll_token, identity=_STRANGER
    )
    assert stranger.can_confirm is False


# ---------------------------------------------------------------------------
# Confirm with a human-entered secret
# ---------------------------------------------------------------------------


async def test_confirm_api_key_connects_binds_and_never_leaks_the_secret(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    with structlog.testing.capture_logs() as logs:
        result = await _confirm_key(env, created.session_id)
    assert isinstance(result, ConnectedConfirmResult)
    assert _SECRET not in repr(result)
    row = await _row(env, created.session_id)
    assert row is not None and row.state == "connected"

    async with env.control_db.session() as session:
        credential = await CredentialRepository.get_by_id(session, row.credential_id)
        key_row = (
            await session.execute(
                select(CustomerAPIKey).where(CustomerAPIKey.credential_id == row.credential_id)
            )
        ).scalar_one()
        outcome = await ConnectSessionOutcomeRepository.get_by_session_id(session, row.id)
        rules = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_permission_rules"
                    " WHERE agent_id = :a AND credential_id = :c"
                ),
                {"a": _AGENT_ID, "c": row.credential_id},
            )
        ).scalar_one()
    assert credential is not None and credential.state == "connected"
    # Attributed to the approving human (the agent holds no credentials:write grant).
    assert credential.created_by == _OWNER_ID
    assert (key_row.location, key_row.field_name) == ("header", "X-Api-Key")
    assert key_row.encrypted_key != _SECRET
    assert env.encryption.decrypt(key_row.encrypted_key) == _SECRET
    assert outcome is not None and (outcome.outcome, outcome.credential_id) == (
        "connected",
        row.credential_id,
    )
    assert rules == 1
    assert await _bound(env, _AGENT_ID, row.credential_id)

    status = await _svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert (status.status, status.credential_id) == ("connected", row.credential_id)

    # Redaction: never in audit, events or logs.
    async with env.admin_db.session() as session:
        audit = (await session.execute(select(AuditEntry))).scalars().all()
        events = (await session.execute(text("SELECT * FROM events"))).all()
    assert audit
    for entry in audit:
        assert _SECRET not in json.dumps(
            {"before": entry.before, "after": entry.after, "reason": entry.reason}, default=str
        )
    confirm_audit = [e for e in audit if e.action == "confirm"]
    assert confirm_audit and confirm_audit[0].after is not None
    assert confirm_audit[0].after["confirm_kind"] == "api_key"
    assert all(_SECRET not in str(row) for row in events)
    assert all(_SECRET not in str(event) for event in logs)


@pytest.mark.parametrize(
    ("schemes", "kind", "secret"),
    [
        ({"b": {"type": "http", "scheme": "bearer"}}, "bearer", BearerSecret(SecretStr(_SECRET))),
        (
            {"b": {"type": "http", "scheme": "basic"}},
            "basic",
            BasicSecret(username="widget-user", password=SecretStr(_SECRET)),
        ),
    ],
)
async def test_confirm_bearer_and_basic(
    env: Context, schemes: dict[str, Any], kind: str, secret: Any
) -> None:
    await _import(env, schemes)
    created = await _connect(env)
    result = await _svc(env).confirm_variant(
        created.session_id,
        poll_token=None,
        variant=SecretConfirm(
            kind=kind, secret=secret, checks=_checks(await _digest(env, created.session_id))
        ),
        identity=_OWNER,
    )
    assert isinstance(result, ConnectedConfirmResult)
    async with env.control_db.session() as session:
        table = "token_value_credentials" if kind == "bearer" else "basic_credentials"
        count = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE credential_id = :c"),
                {"c": result.credential_id},
            )
        ).scalar_one()
    assert count == 1


async def test_confirm_refuses_a_mismatched_kind(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    digest = await _digest(env, created.session_id)
    with pytest.raises(ConfirmKindMismatchError) as mismatch:
        await _svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=SecretConfirm(
                kind="bearer", secret=BearerSecret(SecretStr(_SECRET)), checks=_checks(digest)
            ),
            identity=_OWNER,
        )
    assert mismatch.value.allowed == ["api_key", "existing_credential", "reauthorize"]
    with pytest.raises(ConfirmKindMismatchError):
        await _svc(env).confirm(
            created.session_id,
            poll_token=None,
            confirmed_scopes=[],
            permission_rules=_RULES,
            identity=_OWNER,
        )


async def test_confirm_refuses_a_stale_review_and_an_empty_rule_list(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    digest = await _digest(env, created.session_id)
    svc = _svc(env)

    def _variant(checks: ConfirmChecks) -> SecretConfirm:
        return SecretConfirm(kind="api_key", secret=ApiKeySecret(SecretStr(_SECRET)), checks=checks)

    with pytest.raises(ReviewStaleError):
        await svc.confirm_variant(
            created.session_id,
            poll_token=None,
            variant=_variant(_checks("0" * 64)),
            identity=_OWNER,
        )
    with pytest.raises(ReviewStaleError):
        await svc.confirm_variant(
            created.session_id,
            poll_token=None,
            variant=_variant(
                ConfirmChecks(
                    permission_rules=_RULES, expected_agent_id=_OTHER_AGENT_ID, digest=digest
                )
            ),
            identity=_OWNER,
        )
    with pytest.raises(RulesRequiredError):
        await svc.confirm_variant(
            created.session_id,
            poll_token=None,
            variant=_variant(_checks(digest, rules=[])),
            identity=_OWNER,
        )
    row = await _row(env, created.session_id)
    assert row is not None and row.state == "created"
    async with env.control_db.session() as session:
        assert (
            await session.execute(
                select(CustomerAPIKey).where(CustomerAPIKey.credential_id == row.credential_id)
            )
        ).first() is None


# ---------------------------------------------------------------------------
# Re-checks at confirm, compensation, archive race
# ---------------------------------------------------------------------------


async def test_scheme_change_ends_the_session(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    digest = await _digest(env, created.session_id)
    await _import(env, {"key": {"type": "apiKey", "in": "header", "name": "X-Other-Key"}})
    with pytest.raises(SchemeChangedError):
        await _svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=SecretConfirm(
                kind="api_key", secret=ApiKeySecret(SecretStr(_SECRET)), checks=_checks(digest)
            ),
            identity=_OWNER,
        )
    assert await _row(env, created.session_id) is None
    status = await _svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert (status.status, status.error_code) == ("failed", "scheme_changed")


async def test_host_change_ends_the_session(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    digest = await _digest(env, created.session_id)
    # An operator-approved re-import makes the new host current.
    await _import(env, _KEY_SCHEME, servers=[{"url": "https://evil.example"}], approved=True)
    with pytest.raises(ServersChangedError):
        await _svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=SecretConfirm(
                kind="api_key", secret=ApiKeySecret(SecretStr(_SECRET)), checks=_checks(digest)
            ),
            identity=_OWNER,
        )
    status = await _svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert (status.status, status.error_code) == ("failed", "servers_changed")


async def test_host_change_during_the_window_is_held(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    held = await _import(env, _KEY_SCHEME, servers=[{"url": "https://evil.example"}])
    assert held["held_for_review"] is True
    # The session still sees its pinned host, so it can be confirmed.
    assert isinstance(await _confirm_key(env, created.session_id), ConnectedConfirmResult)


async def test_binding_is_removed_when_a_cancel_wins(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    svc = _svc(env)
    digest = await _digest(env, created.session_id)
    original = ConnectSessionService._bind_for_confirm

    async def bind_then_cancel(self: ConnectSessionService, *args: Any, **kwargs: Any) -> bool:
        newly = await original(self, *args, **kwargs)
        await _svc(env).cancel_session(created.session_id, poll_token=None, identity=_ADMIN)
        return newly

    monkeypatch.setattr(ConnectSessionService, "_bind_for_confirm", bind_then_cancel)
    row = await _row(env, created.session_id)
    assert row is not None
    with pytest.raises(InvalidStateTransitionError):
        await svc.confirm_variant(
            created.session_id,
            poll_token=None,
            variant=SecretConfirm(
                kind="api_key", secret=ApiKeySecret(SecretStr(_SECRET)), checks=_checks(digest)
            ),
            identity=_OWNER,
        )
    assert not await _bound(env, _AGENT_ID, row.credential_id)


async def test_binding_is_kept_when_a_concurrent_confirm_wins(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    digest = await _digest(env, created.session_id)
    row = await _row(env, created.session_id)
    assert row is not None
    original = ConnectSessionService._bind_for_confirm
    raced = False

    async def bind_then_lose(self: ConnectSessionService, *args: Any, **kwargs: Any) -> bool:
        nonlocal raced
        newly = await original(self, *args, **kwargs)
        if not raced:
            raced = True
            # A second approver's confirm runs to completion first.
            await _confirm_key(env, created.session_id, identity=_ADMIN)
        return newly

    monkeypatch.setattr(ConnectSessionService, "_bind_for_confirm", bind_then_lose)
    with pytest.raises(InvalidStateTransitionError):
        await _svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=SecretConfirm(
                kind="api_key", secret=ApiKeySecret(SecretStr("other")), checks=_checks(digest)
            ),
            identity=_OWNER,
        )
    assert await _bound(env, _AGENT_ID, row.credential_id)
    async with env.control_db.session() as session:
        key_row = (
            await session.execute(
                select(CustomerAPIKey).where(CustomerAPIKey.credential_id == row.credential_id)
            )
        ).scalar_one()
    assert env.encryption.decrypt(key_row.encrypted_key) == _SECRET


async def test_agent_archived_between_check_and_bind_is_refused(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    digest = await _digest(env, created.session_id)
    row = await _row(env, created.session_id)
    assert row is not None
    original = ConnectSessionService._require_agent_binding_allowed

    async def check_then_archive(self: ConnectSessionService, *args: Any, **kwargs: Any) -> None:
        await original(self, *args, **kwargs)
        async with env.admin_db.transaction() as session:
            await session.execute(
                text("UPDATE agents SET status = 'archived' WHERE id = :id"), {"id": _AGENT_ID}
            )

    monkeypatch.setattr(ConnectSessionService, "_require_agent_binding_allowed", check_then_archive)
    with pytest.raises(AgentInactiveError):
        await _svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=SecretConfirm(
                kind="api_key", secret=ApiKeySecret(SecretStr(_SECRET)), checks=_checks(digest)
            ),
            identity=_OWNER,
        )
    assert not await _bound(env, _AGENT_ID, row.credential_id)
    current = await _row(env, created.session_id)
    assert current is not None and current.state == "created"


# ---------------------------------------------------------------------------
# Use an existing credential / re-authorize
# ---------------------------------------------------------------------------


async def _existing_credential(
    ctx: Context, *, oauth_scope: str | None = None, name: str = "Mine"
) -> str:
    """A connected credential of the owner's covering the API (OAuth when a scope is given)."""
    credential_id = generate_ksuid("cred")
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=credential_id,
                type="OAUTH2_AUTHORIZATION_CODE" if oauth_scope is not None else "API_KEY",
                provider="direct_oauth2" if oauth_scope is not None else "static",
                name=name,
                api_vendor=_VENDOR,
                api_name=_NAME,
                created_by=_OWNER_ID,
                state="connected",
            )
        )
        await session.flush()
        if oauth_scope is not None:
            session.add(
                OAuthClientCredential(
                    id=credential_id,
                    token_url="https://auth.widgets.example/token",
                    client_id="mine",
                    encrypted_client_secret=ctx.encryption.encrypt("mine-secret"),
                    authorize_url="https://auth.widgets.example/authorize",
                    created_by=_OWNER_ID,
                )
            )
            session.add(
                OAuthToken(
                    id=generate_ksuid("otok"),
                    credential_id=credential_id,
                    encrypted_access_token=ctx.encryption.encrypt("access"),
                    scope=oauth_scope,
                    created_by=_OWNER_ID,
                )
            )
    return credential_id


async def _confirm_existing(
    ctx: Context, session_id: str, credential_id: str, *, reauthorize: bool = False
) -> Any:
    return await _svc(ctx).confirm_variant(
        session_id,
        poll_token=None,
        variant=ExistingCredentialConfirm(
            credential_id=credential_id,
            reauthorize=reauthorize,
            checks=_checks(await _digest(ctx, session_id)),
        ),
        identity=_OWNER,
    )


async def test_bind_an_existing_static_credential(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    existing = await _existing_credential(env)
    review = await _svc(env).get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    (candidate,) = review.existing_credentials
    assert (candidate.credential_id, candidate.can_bind, candidate.granted_scopes) == (
        existing,
        True,
        None,
    )
    pending = (await _row(env, created.session_id)).credential_id  # type: ignore[union-attr]

    result = await _confirm_existing(env, created.session_id, existing)
    assert isinstance(result, ConnectedConfirmResult) and result.credential_id == existing
    assert await _bound(env, _AGENT_ID, existing)
    # The session's own pending credential (and the session row) are gone.
    assert await _row(env, created.session_id) is None
    async with env.control_db.session() as session:
        assert await CredentialRepository.get_by_id(session, pending) is None
    status = await _svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert (status.status, status.credential_id) == ("connected", existing)


async def test_granted_scope_check_for_an_existing_oauth_credential(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    created = await _connect(env, requested_scopes=["read", "write"])
    wide = await _existing_credential(env, oauth_scope="read write admin", name="wide")
    narrow = await _existing_credential(env, oauth_scope="read", name="narrow")
    review = await _svc(env).get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    views = {c.credential_id: c for c in review.existing_credentials}
    assert views[wide].can_bind and views[wide].missing_scopes == []
    assert not views[narrow].can_bind and views[narrow].missing_scopes == ["write"]
    assert views[narrow].can_reauthorize is True

    with pytest.raises(InsufficientGrantedScopesError) as narrower:
        await _confirm_existing(env, created.session_id, narrow)
    assert narrower.value.missing == ["write"]
    assert not await _bound(env, _AGENT_ID, narrow)
    result = await _confirm_existing(env, created.session_id, wide)
    assert isinstance(result, ConnectedConfirmResult)


async def test_reauthorize_only_when_no_other_agent_is_bound(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    created = await _connect(env, requested_scopes=["read", "write"])
    narrow = await _existing_credential(env, oauth_scope="read")
    async with env.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id, created_by)"
                " VALUES (:id, :a, :c, :u)"
            ),
            {"id": generate_ksuid("acb"), "a": _OTHER_AGENT_ID, "c": narrow, "u": _OWNER_ID},
        )
    review = await _svc(env).get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    (view,) = review.existing_credentials
    assert view.other_bound_agent_ids == [_OTHER_AGENT_ID]
    assert view.can_reauthorize is False
    with pytest.raises(ReauthorizeUnavailableError):
        await _confirm_existing(env, created.session_id, narrow, reauthorize=True)

    async with env.admin_db.transaction() as session:
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE agent_id = :a"),
            {"a": _OTHER_AGENT_ID},
        )
    result = await _confirm_existing(env, created.session_id, narrow, reauthorize=True)
    assert isinstance(result, ReauthorizeConfirmResult)
    assert result.credential_id == narrow
    assert result.authorize_url.startswith("https://auth.widgets.example/authorize?")
    assert "write" in result.authorize_url and "read" in result.authorize_url
    assert await _bound(env, _AGENT_ID, narrow)


async def test_own_oauth_client_resolves_awaiting_app(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    created = await _connect(env, requested_scopes=["read"])
    client_secret = "own-client-secret-" + "Q" * 16
    result = await _svc(env).confirm_variant(
        created.session_id,
        poll_token=None,
        variant=OwnClientConfirm(
            client_id="my-own-client",
            client_secret=SecretStr(client_secret),
            authorize_url="https://auth.widgets.example/authorize",
            token_url="https://auth.widgets.example/token",
            confirmed_scopes=["read"],
            checks=_checks(await _digest(env, created.session_id)),
        ),
        identity=_OWNER,
    )
    assert isinstance(result, AuthCodeConfirmResult)
    assert result.authorize_url.startswith("https://auth.widgets.example/authorize?")
    assert "client_id=my-own-client" in result.authorize_url
    row = await _row(env, created.session_id)
    assert row is not None and (row.state, row.resolved_flow) == ("polling", "authorization_code")
    async with env.control_db.session() as session:
        occ = await OAuthClientCredentialRepository.get_by_credential(session, row.credential_id)
    assert occ is not None and env.encryption.decrypt(occ.encrypted_client_secret) == client_secret
    async with env.admin_db.session() as session:
        audit = (await session.execute(select(AuditEntry))).scalars().all()
    assert all(client_secret not in json.dumps(e.after, default=str) for e in audit)


# ---------------------------------------------------------------------------
# Dedupe + rotation, caps, TTL
# ---------------------------------------------------------------------------


async def test_repeat_ask_returns_the_open_session_with_a_new_token(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    first = await _connect(env)
    second = await _connect(env, auth_type="key")
    assert (second.session_id, second.approval_url) == (first.session_id, first.approval_url)
    svc = _svc(env)
    with pytest.raises(InvalidPollTokenError):
        await svc.get_status(first.session_id, poll_token=first.poll_token, identity=_AGENT)
    assert (
        await svc.get_status(first.session_id, poll_token=second.poll_token, identity=_AGENT)
    ).status == "pending"

    # Concurrent repeats all land on the one session; exactly one token survives.
    racers = await asyncio.gather(*(_connect(env) for _ in range(3)))
    assert {r.session_id for r in racers} == {first.session_id}
    alive = 0
    for r in racers:
        try:
            await svc.get_status(r.session_id, poll_token=r.poll_token, identity=_AGENT)
            alive += 1
        except InvalidPollTokenError:
            pass
    assert alive == 1
    async with env.control_db.session() as session:
        count = (await session.execute(text("SELECT COUNT(*) FROM connect_sessions"))).scalar()
    assert count == 1


async def test_open_session_caps(env: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    await _import(env, _KEY_SCHEME)
    await _import(env, _KEY_SCHEME, name="other")
    monkeypatch.setattr(env.config.control.connect, "max_open_sessions_per_agent", 1)
    await _connect(env)
    with pytest.raises(TooManyOpenSessionsError) as per_agent:
        await _svc(env).create_session(
            vendor_key="",
            agent_id=_AGENT_ID,
            initiator_actor_id=_AGENT_ID,
            api_target=ApiTarget(vendor=_VENDOR, name="other", version=_VERSION),
        )
    assert (per_agent.value.scope, per_agent.value.limit) == ("agent", 1)
    # A repeat ask for the open target is not a new session, so the cap allows it.
    assert (await _connect(env)).resolved_flow == "manual_api_key"

    monkeypatch.setattr(env.config.control.connect, "max_open_sessions_per_agent", 10)
    monkeypatch.setattr(env.config.control.connect, "max_open_sessions_per_owner", 1)
    with pytest.raises(TooManyOpenSessionsError) as per_owner:
        await _connect(env, agent_id=_OTHER_AGENT_ID)
    assert per_owner.value.scope == "owner"


async def test_api_target_keeps_its_ttl_after_resolving_to_oauth(env: Context) -> None:
    await _import(env, _OAUTH_SCHEME)
    env.config.vendors.entries["widgets"] = _covering_entry()
    created = await _connect(env)
    async with env.control_db.transaction() as session:
        await session.execute(
            update(ConnectSession)
            .where(ConnectSession.id == created.session_id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=1))
        )
    # Past the 30-minute OAuth TTL but inside the manual one: still open.
    assert await _svc(env).expire_stale_sessions() == 0
    async with env.control_db.transaction() as session:
        await session.execute(
            update(ConnectSession)
            .where(ConnectSession.id == created.session_id)
            .values(created_at=datetime.now(UTC) - timedelta(hours=100))
        )
    assert await _svc(env).expire_stale_sessions() == 1


# ---------------------------------------------------------------------------
# :reject, cooldown, /status from outcomes
# ---------------------------------------------------------------------------


async def test_reject_ends_the_session_and_starts_the_cooldown(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    svc = _svc(env)
    for caller in (_STRANGER, _AGENT, _OWNER_NO_AGENTS_WRITE):
        with pytest.raises(InvalidPollTokenError):
            await svc.reject_session(created.session_id, identity=caller)
    await svc.reject_session(created.session_id, identity=_OWNER)
    # Idempotent once ended (the uniform 403 for a missing session stays).
    with pytest.raises(InvalidPollTokenError):
        await svc.reject_session(created.session_id, identity=_OWNER)

    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert (status.status, status.error_code) == ("failed", "rejected")
    # Owner reads the outcome without the token; a wrong token is refused.
    owner_view = await svc.get_status(created.session_id, poll_token=None, identity=_OWNER)
    assert owner_view.error_code == "rejected"
    with pytest.raises(InvalidPollTokenError):
        await svc.get_status(created.session_id, poll_token="wrong", identity=_AGENT)

    with pytest.raises(RecentlyRejectedError) as cooldown:
        await _connect(env)
    assert 0 < cooldown.value.retry_after_seconds <= 24 * 3600
    # Another agent, or the same agent for another target, is unaffected.
    assert (await _connect(env, agent_id=_OTHER_AGENT_ID)).resolved_flow == "manual_api_key"


async def test_cooldown_zero_disables_it(env: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    await _svc(env).reject_session(created.session_id, identity=_ADMIN)
    monkeypatch.setattr(env.config.control.connect, "rejection_cooldown_hours", 0)
    assert (await _connect(env)).session_id != created.session_id


async def test_cancel_stays_cancelled_and_never_cools_down(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    svc = _svc(env)
    await svc.cancel_session(created.session_id, poll_token=created.poll_token, identity=_AGENT)
    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT
    )
    assert (status.status, status.error_code) == ("failed", "cancelled")
    assert (await _connect(env)).session_id != created.session_id


# ---------------------------------------------------------------------------
# Over HTTP: the real router, error mapping and confirm body discriminator
# ---------------------------------------------------------------------------


def _app(ctx: Context, identity: Identity) -> FastAPI:
    app = FastAPI()
    app.include_router(integrations_router.router)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    app.state.ctx = ctx
    app.state.security_schemes_lookup = ApiSecurityLookupService(ctx)
    app.dependency_overrides[resolve_identity] = lambda: identity
    return app


def _client(ctx: Context, identity: Identity) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app(ctx, identity)), base_url="https://t")


_API_BODY = {"api": {"vendor": _VENDOR, "name": _NAME, "version": _VERSION}}


async def test_http_connect_api_target_and_error_bodies(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _import(env, {**_KEY_SCHEME, "bearer": {"type": "http", "scheme": "bearer"}})
    async with _client(env, _AGENT) as client:
        both = await client.post("/integrations:connect", json={"vendor": "x", **_API_BODY})
        assert both.status_code == 422
        neither = await client.post("/integrations:connect", json={})
        assert neither.status_code == 422
        several = await client.post("/integrations:connect", json=_API_BODY)
        assert several.status_code == 400
        assert several.json()["type"] == "auth_type_required"
        assert several.json()["options"] == ["bearer", "key"]
        undeclared = await client.post(
            "/integrations:connect", json={**_API_BODY, "auth_type": "basic"}
        )
        assert (undeclared.status_code, undeclared.json()["type"]) == (
            422,
            "auth_type_not_declared",
        )
        ok = await client.post("/integrations:connect", json={**_API_BODY, "auth_type": "key"})
        assert ok.status_code == 201
        assert ok.json()["resolved_flow"] == "manual_api_key"

        monkeypatch.setattr(env.config.control.connect, "manual_flows_enabled", False)
        off = await client.post("/integrations:connect", json=_API_BODY)
        assert (off.status_code, off.json()["type"]) == (404, "manual_flows_disabled")


async def test_http_confirm_secret_variant_and_hidden_input(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    async with _client(env, _OWNER) as client:
        review = (await client.get(f"/connect-sessions/{created.session_id}")).json()
        assert review["target_kind"] == "api"
        assert review["scheme"] == {
            "type": "api_key",
            "location": "header",
            "field_name": "X-Api-Key",
        }
        assert review["can_confirm"] is True
        body = {
            "kind": "api_key",
            "key": _SECRET,
            "permission_rules": _RULES,
            "expected_agent_id": _AGENT_ID,
        }
        # Missing digest: a 422 that never echoes the secret.
        invalid = await client.post(f"/connect-sessions/{created.session_id}:confirm", json=body)
        assert invalid.status_code == 422
        assert _SECRET not in invalid.text
        stale = await client.post(
            f"/connect-sessions/{created.session_id}:confirm", json={**body, "digest": "0" * 64}
        )
        assert (stale.status_code, stale.json()["type"]) == (409, "review_stale")
        assert _SECRET not in stale.text
        unknown_kind = await client.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={**body, "kind": "sigv4", "digest": review["digest"]},
        )
        assert unknown_kind.status_code == 422
        assert _SECRET not in unknown_kind.text
        # No ``kind`` is the OAuth variant, which does not fit a manual_* session.
        oauth = await client.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={"confirmed_scopes": [], "permission_rules": _RULES},
        )
        assert (oauth.status_code, oauth.json()["type"]) == (400, "confirm_kind_mismatch")
        assert oauth.json()["allowed_kinds"][0] == "api_key"
        ok = await client.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={**body, "digest": review["digest"]},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["kind"] == "connected"
        assert _SECRET not in ok.text


async def test_http_reject_status_and_cooldown(env: Context) -> None:
    await _import(env, _KEY_SCHEME)
    created = await _connect(env)
    async with _client(env, _AGENT) as agent_client:
        refused = await agent_client.post(f"/connect-sessions/{created.session_id}:reject")
        assert refused.status_code == 403
    async with _client(env, _STRANGER) as stranger:
        refused = await stranger.post(f"/connect-sessions/{created.session_id}:reject")
        assert (refused.status_code, refused.json()["type"]) == (403, "invalid_poll_token")
    async with _client(env, _OWNER) as owner:
        assert (
            await owner.post(f"/connect-sessions/{created.session_id}:reject")
        ).status_code == 204
    async with _client(env, _AGENT) as agent_client:
        status = await agent_client.get(
            f"/connect-sessions/{created.session_id}/status",
            params={"poll_token": created.poll_token},
        )
        # A terminal status an older CLI already understands.
        assert status.status_code == 200
        assert (status.json()["status"], status.json()["error_code"]) == ("failed", "rejected")
        again = await agent_client.post("/integrations:connect", json=_API_BODY)
        assert (again.status_code, again.json()["type"]) == (429, "recently_rejected")
        assert int(again.headers["Retry-After"]) > 0
