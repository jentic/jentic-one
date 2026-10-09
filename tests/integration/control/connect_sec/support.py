"""Shared builders for the connect-session security regression suite.

Every test in this package asserts that one protection of the API-target
connect flow holds against a caller trying to get around it. Specs go through
the real registry ingest and every repository is real; ``conftest.py`` owns
the clean-slate fixture.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler
from pydantic import SecretStr
from sqlalchemy import text

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.services.integrations.connect_session_service import (
    ApiTarget,
    ConfirmChecks,
    ConnectSessionService,
    SecretConfirm,
)
from jentic_one.control.services.integrations.flow_handlers.manual import ApiKeySecret
from jentic_one.control.web.app import get_exception_handlers
from jentic_one.control.web.routers import integrations as integrations_router
from jentic_one.registry.services.api_security_lookup_service import ApiSecurityLookupService
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.shared.web.errors import request_validation_error_handler

VENDOR = "vault-example"
NAME = "vault-example-api"
VERSION = "1.0.0"
TARGET = ApiTarget(vendor=VENDOR, name=NAME, version=VERSION)
API_BODY: dict[str, Any] = {"api": {"vendor": VENDOR, "name": NAME, "version": VERSION}}

OWNER_ID = "usr_sec_owner"
OTHER_OWNER_ID = "usr_sec_other_owner"
AGENT_ID = "agnt_sec_scout"
SIBLING_AGENT_ID = "agnt_sec_sibling"
FOREIGN_AGENT_ID = "agnt_sec_foreign"

OWNER = Identity(sub=OWNER_ID, permissions=["credentials:write", "agents:write"])
OWNER_NO_AGENTS_WRITE = Identity(sub=OWNER_ID, permissions=["credentials:write"])
OTHER_OWNER = Identity(sub=OTHER_OWNER_ID, permissions=["credentials:write", "agents:write"])
ADMIN = Identity(sub="usr_sec_root", permissions=["org:admin"])
# The baseline every new agent is granted (``credentials:connect``, not ``:write``).
AGENT = Identity(
    sub=AGENT_ID, permissions=list(DEFAULT_AGENT_PERMISSIONS), actor_type=ActorType.AGENT
)

RULES: list[dict[str, object]] = [
    {"effect": "allow", "methods": ["GET"], "path": "/items", "match_mode": "exact"}
]
KEY_SCHEME: dict[str, Any] = {"key": {"type": "apiKey", "in": "header", "name": "X-Vault-Key"}}
OAUTH_SCHEME: dict[str, Any] = {
    "oauth": {
        "type": "oauth2",
        "flows": {
            "authorizationCode": {
                "authorizationUrl": "https://auth.vault.example/authorize",
                "tokenUrl": "https://auth.vault.example/token",
                "scopes": {"read": "Read", "write": "Write"},
            }
        },
    }
}


def spec_source(
    schemes: dict[str, Any],
    *,
    servers: list[dict[str, Any]] | None = None,
    approved: bool = False,
    name: str = NAME,
    origin: str = "catalog",
) -> dict[str, Any]:
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Vault", "version": VERSION, "description": uuid.uuid4().hex},
        "servers": servers if servers is not None else [{"url": "https://api.vault.example"}],
        "components": {"securitySchemes": schemes},
        "paths": {
            "/items": {"get": {"operationId": "list", "responses": {"200": {"description": "OK"}}}}
        },
    }
    source: dict[str, Any] = {
        "type": "inline",
        "content": json.dumps(spec),
        "filename": "openapi.json",
        "vendor": VENDOR,
        "api_name": name,
        "version": VERSION,
        "origin": origin,
        "submitted_by": "usr_sec_writer",
    }
    if approved:
        source["host_change_approved"] = "true"
    return source


async def import_spec(ctx: Context, schemes: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Import (or re-import) the API through the real registry ingest."""
    result = await ImportHandler(ctx).execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        session=None,
        payload={"sources": [spec_source(schemes, **kwargs)]},
        created_by="usr_sec_writer",
    )
    revision: dict[str, Any] = result.body["revisions"][0]
    return revision


def svc(ctx: Context) -> ConnectSessionService:
    return ConnectSessionService(ctx, security_schemes_lookup=ApiSecurityLookupService(ctx))


async def connect(
    ctx: Context,
    *,
    agent_id: str = AGENT_ID,
    target: ApiTarget = TARGET,
    auth_type: str | None = None,
    reason: str = "needs the vault",
    rules: list[dict[str, object]] | None = None,
    **kwargs: Any,
) -> Any:
    return await svc(ctx).create_session(
        vendor_key="",
        agent_id=agent_id,
        initiator_actor_id=agent_id,
        api_target=target,
        auth_type=auth_type,
        requested_permission_rules=RULES if rules is None else rules,
        reason=reason,
        **kwargs,
    )


async def session_row(ctx: Context, session_id: str) -> ConnectSession | None:
    async with ctx.control_db.session() as session:
        return await ConnectSessionRepository.get_by_id(session, session_id)


async def is_bound(ctx: Context, agent_id: str, credential_id: str) -> bool:
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


async def bind(ctx: Context, agent_id: str, credential_id: str) -> None:
    """Bind an agent directly, as the credentials bind route would."""
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id, created_by)"
                " VALUES (:id, :a, :c, :u)"
            ),
            {"id": generate_ksuid("acb"), "a": agent_id, "c": credential_id, "u": OWNER_ID},
        )


async def digest(ctx: Context, session_id: str, identity: Identity = OWNER) -> str:
    review = await svc(ctx).get_review_data(session_id, poll_token=None, identity=identity)
    return review.digest


def checks(
    review_digest: str,
    *,
    expected_agent_id: str | None = AGENT_ID,
    rules: list[dict[str, object]] | None = None,
) -> ConfirmChecks:
    return ConfirmChecks(
        permission_rules=RULES if rules is None else rules,
        expected_agent_id=expected_agent_id,
        digest=review_digest,
    )


def key_variant(secret: str, review_digest: str) -> SecretConfirm:
    return SecretConfirm(
        kind="api_key", secret=ApiKeySecret(key=SecretStr(secret)), checks=checks(review_digest)
    )


async def confirm_key(
    ctx: Context, session_id: str, secret: str, *, identity: Identity = OWNER
) -> Any:
    return await svc(ctx).confirm_variant(
        session_id,
        poll_token=None,
        variant=key_variant(secret, await digest(ctx, session_id)),
        identity=identity,
    )


def build_app(ctx: Context, identity: Identity) -> FastAPI:
    """The real integrations router with the control surface's error mapping."""
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


def client(ctx: Context, identity: Identity) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=build_app(ctx, identity)), base_url="https://t")
