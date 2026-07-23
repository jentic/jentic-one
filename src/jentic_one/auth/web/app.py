"""Auth application factory."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, FastAPI, Request
from jentic.problem_details import Unauthorized

from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.services.token_service import ACCESS_TOKEN_PREFIX, TokenService
from jentic_one.auth.web.errors import database_error_handler, service_error_handler
from jentic_one.auth.web.routers import (
    agents,
    authorize,
    discovery,
    identity,
    oauth,
    registration,
    service_accounts,
)
from jentic_one.shared.auth.api_key_resolver import (
    AGENT_API_KEY_PREFIX,
    SERVICE_ACCOUNT_API_KEY_PREFIX,
    ApiKeyResolver,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.verify import resolve_permissions_for_actor, verify_token
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.app_factory import create_surface_app
from jentic_one.shared.web.health import make_health_router

logger = structlog.get_logger(__name__)


def get_routers() -> list[tuple[APIRouter, str, list[str]]]:
    """Return routers with (router, prefix, tags) for combined-mode inclusion."""
    # Tags are assigned centrally by the OpenAPI tag resolver (see
    # shared/web/openapi_meta.resolve_tag), so no coarse include-level tags here.
    return [
        (make_health_router("auth"), "/auth", []),
        (discovery.router, "", []),
        (authorize.router, "", []),
        (identity.router, "", []),
        (agents.router, "", []),
        (service_accounts.router, "", []),
        (oauth.router, "", []),
        (registration.router, "", []),
    ]


def get_exception_handlers() -> list[tuple[type[Exception], Any]]:
    """Return auth-specific exception handlers for registration on the combined app."""
    return [
        (AuthServiceError, service_error_handler),
        (DatabaseUnavailableError, database_error_handler),
    ]


def install_on_app(app: FastAPI, ctx: Context) -> None:
    """Install the auth token verifier on the app for shared identity resolution."""
    app.state.verify_token = _make_auth_verifier(ctx)


def _make_auth_verifier(ctx: Context) -> Any:
    """Build the auth token verifier (API keys + opaque access tokens + JWT)."""
    api_key_resolver = ApiKeyResolver(ctx.admin_db)

    async def _verify(token: str, request: Request) -> Identity:
        if token.startswith(AGENT_API_KEY_PREFIX) or token.startswith(
            SERVICE_ACCOUNT_API_KEY_PREFIX
        ):
            resolved = await api_key_resolver.resolve(token)
            if resolved is None or not resolved.active:
                raise Unauthorized(
                    detail="Invalid or expired API key",
                    instance=request.url.path,
                    type="unauthorized",
                )
            return resolved

        if token.startswith(ACCESS_TOKEN_PREFIX):
            token_svc = TokenService(ctx)
            resolved = await token_svc.resolve_access_token(token)
            if resolved is None or not resolved.active:
                raise Unauthorized(
                    detail="Invalid or expired token",
                    instance=request.url.path,
                    type="unauthorized",
                )

            permissions, parent_permissions = await resolve_permissions_for_actor(
                ctx, resolved.actor_type, resolved.sub, resolved.parent_actor_id
            )
            if resolved.actor_type == ActorType.AGENT:
                # The access-token row carries the scopes minted from the agent's
                # live actor_scope_grants (TokenService.issue_pair via the
                # jwt-bearer exchange). Trust those as the agent's permissions:
                # the AGENT branch of resolve_permissions_for_actor is an
                # unimplemented stub that returns [], which silently drops every
                # granted scope — an approved capabilities:read then 403s and
                # `jentic access refresh` can never take effect. This mirrors the
                # broker's InProcessTokenResolver, which already reads row.scopes.
                # parent_permissions (owner inheritance) is still resolved above.
                permissions = list(resolved.permissions)

            return Identity(
                sub=resolved.sub,
                email="",
                permissions=permissions,
                parent_permissions=parent_permissions,
                actor_type=resolved.actor_type,
                parent_actor_id=resolved.parent_actor_id,
            )
        return await verify_token(
            token, secret=ctx.config.admin.auth.jwt_secret.get_secret_value(), ctx=ctx
        )

    return _verify


def create_app(ctx: Context) -> FastAPI:
    """Create the auth FastAPI application for standalone deployment."""
    app = create_surface_app(ctx, title="jentic-one-auth", routers=get_routers())
    install_on_app(app, ctx)
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    return app
