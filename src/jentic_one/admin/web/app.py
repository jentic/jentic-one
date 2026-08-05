"""Admin application factory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from jentic.problem_details import Unauthorized

from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import AdminServiceError
from jentic_one.admin.web.errors import database_error_handler, service_error_handler
from jentic_one.admin.web.routers import (
    actors,
    audit,
    auth,
    config,
    events,
    executions,
    health,
    jobs,
    monitoring,
    permissions,
    users,
)
from jentic_one.shared.auth.api_key_resolver import (
    AGENT_API_KEY_PREFIX,
    SERVICE_ACCOUNT_API_KEY_PREFIX,
    ApiKeyResolver,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError, DatabaseUnavailableError
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web.app_factory import create_surface_app
from jentic_one.shared.web.static import mount_spa


def get_routers() -> list[tuple[APIRouter, str, list[str]]]:
    """Return routers with (router, prefix, tags) for combined-mode inclusion.

    Only ``/health`` keeps a surface-specific prefix in combined mode so it can
    coexist with the other surfaces' health endpoints (and the combined app's
    own root ``/health``). Every other admin route is mounted at the root, so
    the wire contract is identical between ``make start-admin`` (standalone)
    and ``make start-app`` (combined): clients hit ``/users``, ``/auth/login``,
    ``/jobs`` etc. either way.
    """
    # Tags are assigned centrally by the OpenAPI tag resolver (see
    # shared/web/openapi_meta.resolve_tag), so no coarse include-level tags here.
    return [
        (health.router, "/admin", []),
        (actors.router, "", []),
        (auth.router, "", []),
        (users.router, "", []),
        (permissions.router, "", []),
        (executions.router, "", []),
        (jobs.router, "", []),
        (events.router, "", []),
        (audit.router, "", []),
        (monitoring.router, "", []),
        (config.router, "", []),
    ]


def get_exception_handlers() -> list[tuple[type[Exception], Any]]:
    """Return surface-specific exception handlers to register on the combined app.

    The shared ``ProblemDetailException`` handler is registered centrally by the
    app factory, so only admin-specific handlers are returned here.
    """
    return [
        (AdminServiceError, service_error_handler),
        (InvalidCursorError, service_error_handler),
        (DatabaseIntegrityError, database_error_handler),
        (DatabaseUnavailableError, database_error_handler),
    ]


def install_on_app(app: FastAPI, ctx: Context) -> None:
    """Install the admin token verifier on the app for shared identity resolution."""
    if not hasattr(app.state, "verify_token"):
        app.state.verify_token = _make_verifier(ctx)


def _make_verifier(ctx: Context) -> Any:
    """Build the admin token verifier (API keys + JWT)."""
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
        return await AuthService(ctx).verify_token(token)

    return _verify


def create_app(ctx: Context) -> FastAPI:
    """Create the admin FastAPI application for standalone deployment."""
    app = create_surface_app(ctx, title="jentic-one-admin", routers=get_routers())
    install_on_app(app, ctx)
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    # Serve the SPA after all API routers are registered. ``app.frontend()``
    # routes are low-priority, so API routes always win; this only needs the
    # routers present so the bundle never answers a real API path. No-op when no
    # UI bundle is packaged (API-only). Standalone drops the surface prefix, so
    # admin health is served at /health.
    mount_spa(app, health_path="/health")
    return app


def mount_ui(app: FastAPI) -> None:
    """Mount the admin SPA on a combined app.

    Called by the combined app factory after all surfaces' routers are
    registered. ``app.frontend()`` routes are low-priority so they never shadow
    an API route. No-op when no UI bundle is packaged. In combined mode the admin
    health router keeps its ``/admin`` prefix (so surfaces don't collide), so the
    SPA must call ``/admin/health``.
    """
    mount_spa(app, health_path="/admin/health")
