"""Unit tests for Wave 2 composition, introspect, wiring, and telemetry EventType seams."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from jentic_one.composition import (
    build_app,
    build_context,
    build_default_container,
    run_cli,
)
from jentic_one.mcp.installer import (
    install_mcp_challenge_placeholder,
    install_mcp_mount,
)
from jentic_one.mcp.spec import SERVED_TOOLS
from jentic_one.mcp.tools import HANDLERS
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import MCP_TOOL_SCOPES
from jentic_one.shared.config import load_config
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.telemetry import events as telemetry_events
from jentic_one.shared.telemetry.events import EventType
from jentic_one.shared.web.deps import get_current_identity
from jentic_one.testing.introspect import (
    McpToolFact,
    mcp_tool_facts,
    route_facts,
)
from jentic_one.testing.introspect import (
    build_app as introspect_build_app,
)
from jentic_one.wiring import (
    InProcessCatalogAutoImporter,
    install_credential_creator_lookup,
)

_LOCAL_CONFIG = Path(__file__).resolve().parents[3] / "config" / "local-sqlite.yaml"


def test_telemetry_events_reexports_event_type() -> None:
    """``EventType`` is explicitly exported on ``jentic_one.shared.telemetry.events``."""
    assert "EventType" in telemetry_events.__all__
    assert EventType.INSTANCE_BOOTED in telemetry_events.TELEMETRY_EVENTS


def test_build_default_container_honors_explicit_apps_override() -> None:
    """``build_default_container(ctx, apps=...)`` scopes installers to ``apps``."""
    config = load_config(_LOCAL_CONFIG)
    assert "control" in config.apps
    ctx = build_context(config, ["auth"])

    auth_container = build_default_container(ctx, apps=["auth"])
    assert install_mcp_challenge_placeholder in auth_container.extra_installers
    assert install_mcp_mount not in auth_container.extra_installers
    assert install_credential_creator_lookup not in auth_container.extra_installers

    registry_container = build_default_container(ctx, apps=["registry"])
    assert registry_container.extra_installers == ()
    assert registry_container.extra_lifespans == ()


@pytest.mark.parametrize("surface", ["control", "auth", "registry", "admin", "broker"])
def test_composition_build_app_threads_container_across_single_surfaces(surface: str) -> None:
    """``composition.build_app`` applies container extras across all single-surface shapes."""
    config = load_config(_LOCAL_CONFIG)
    ctx = build_context(config, [surface])
    extra_router = APIRouter()

    @extra_router.get("/overlay-probe")
    async def overlay_probe() -> dict[str, str]:
        return {"surface": surface}

    installed: list[str] = []
    base = build_default_container(ctx, apps=[surface])
    container = replace(
        base,
        extra_routers=(*base.extra_routers, (extra_router, "/ext", ["ext"])),
        extra_installers=(*base.extra_installers, lambda app, _ctx: installed.append(app.title)),
    )

    app = build_app(ctx, [surface], container=container)
    assert len(installed) == 1
    facts = route_facts(app)
    assert any(f.path == "/ext/overlay-probe" and f.method == "GET" for f in facts)


def test_composition_exports_run_cli() -> None:
    """``composition.run_cli`` is the CLI dispatcher entrypoint."""
    assert callable(run_cli)


@pytest.mark.asyncio
async def test_catalog_auto_importer_current_version_orders_by_created_at_and_id() -> None:
    """``current_version`` orders deterministically by ``(created_at, id)``."""
    captured_sql: list[str] = []

    async def fake_execute(stmt: object) -> MagicMock:
        captured_sql.append(str(stmt))
        res = MagicMock()
        res.scalar_one_or_none.return_value = "1.2.0"
        return res

    session = AsyncMock()
    session.execute.side_effect = fake_execute
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = False

    ctx = MagicMock()
    ctx.registry_db.session.return_value = session_cm

    importer = InProcessCatalogAutoImporter(ctx)
    version = await importer.current_version(api_id="github.com/rest")
    assert version == "1.2.0"
    assert len(captured_sql) == 1
    sql = captured_sql[0].lower()
    assert "order by" in sql
    assert "apis.created_at" in sql
    assert "apis.id" in sql


def test_introspect_build_app_scopes_auth_surface_and_attaches_cleanup() -> None:
    """``introspect.build_app(['auth'])`` scopes the container and attaches shutdown cleanup."""
    app = introspect_build_app(["auth"])
    assert list(app.state.ctx.config.apps) == ["auth"]
    assert len(app.router.on_shutdown) >= 1
    facts = route_facts(app)
    mcp_facts = [f for f in facts if f.path == "/mcp"]
    assert len(mcp_facts) == 1
    assert "McpChallengePlaceholder" in mcp_facts[0].module or "mcp" in mcp_facts[0].module


def test_route_facts_unions_stacked_identity_permissions_and_intersects_actors() -> None:
    """Stacked ``get_current_identity`` deps union permissions and intersect actor constraints."""
    app = FastAPI()
    router = APIRouter(
        dependencies=[
            get_current_identity(
                required_permissions=["apis:read"],
                require_actor_type=ActorType.USER,
            )
        ]
    )

    @router.get("/stacked")
    async def stacked(
        _identity: Annotated[
            Identity,
            get_current_identity(
                required_permissions=["credentials:read"],
                require_actor_type=ActorType.USER,
            ),
        ],
    ) -> dict[str, bool]:
        return {"ok": True}

    @router.get("/conflicting-actors")
    async def conflicting(
        _identity: Annotated[
            Identity,
            get_current_identity(
                required_permissions=["credentials:write"],
                require_actor_type=ActorType.AGENT,
            ),
        ],
    ) -> dict[str, bool]:
        return {"ok": True}

    app.include_router(router)
    by_path = {(f.method, f.path): f for f in route_facts(app) if not f.opaque}

    stacked_fact = by_path[("GET", "/stacked")]
    assert stacked_fact.authenticated is True
    assert stacked_fact.permissions == frozenset({"apis:read", "credentials:read"})
    assert stacked_fact.actor_types == frozenset({"user"})

    conflict_fact = by_path[("GET", "/conflicting-actors")]
    assert conflict_fact.authenticated is True
    assert conflict_fact.permissions == frozenset({"apis:read", "credentials:write"})
    assert conflict_fact.actor_types == frozenset()


def test_route_facts_preserves_distinct_methods_on_non_api_routes() -> None:
    """Starlette ``Route`` instances emit distinct per-method opaque ``RouteFact`` records."""
    app = FastAPI()

    async def get_raw(_request: object) -> PlainTextResponse:
        return PlainTextResponse("get")

    async def post_raw(_request: object) -> PlainTextResponse:
        return PlainTextResponse("post")

    app.routes.append(Route("/raw-endpoint", get_raw, methods=["GET", "HEAD"]))
    app.routes.append(Route("/raw-endpoint", post_raw, methods=["POST", "OPTIONS"]))

    raw_facts = [f for f in route_facts(app) if f.path == "/raw-endpoint"]
    assert [(f.method, f.opaque) for f in raw_facts] == [("GET", True), ("POST", True)]


def test_mcp_tool_facts_covers_served_tools_handlers_and_scopes() -> None:
    """``mcp_tool_facts()`` exposes every served MCP tool, handler status, and scopes."""
    facts = mcp_tool_facts()
    assert [f.name for f in facts] == list(SERVED_TOOLS)
    for fact in facts:
        assert isinstance(fact, McpToolFact)
        assert fact.has_handler is (fact.name in HANDLERS)
        assert fact.has_handler is True
        assert fact.module is not None
        assert fact.scopes <= fact.permissions
        assert fact.scopes <= MCP_TOOL_SCOPES
