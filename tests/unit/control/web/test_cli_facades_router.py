"""Unit tests for the CLI facades router: the ``GET /cli-facades`` wire shape and gate."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.control.services.cli_facades import CliFacadeService
from jentic_one.control.web.deps import get_cli_facade_service
from jentic_one.control.web.routers import cli_facades as router_module
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.web import deps as shared_deps

_AGENT = Identity(sub="agt_reader", actor_type=ActorType.AGENT, permissions=["capabilities:read"])
_NO_READ = Identity(sub="agt_bare", actor_type=ActorType.AGENT, permissions=["jobs:read"])


def _client(identity: Identity) -> TestClient:
    config = AppConfig.model_validate(
        {
            "databases": {
                "registry": {"name": "reg"},
                "admin": {"name": "admin"},
                "control": {"name": "ctrl"},
            },
            "cli_facades": {"enabled": True, "vendors": {"cloudflare": {"enabled": True}}},
            "server": {"mcp": {"broker_url": "http://127.0.0.1:8100"}},
        }
    )
    svc = CliFacadeService(cast(Context, SimpleNamespace(config=config)))
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.include_router(router_module.router)
    app.dependency_overrides[get_cli_facade_service] = lambda: svc
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: identity
    return TestClient(app)


def test_list_cli_facades_wire_shape() -> None:
    resp = _client(_AGENT).get("/cli-facades")

    assert resp.status_code == 200
    assert resp.json() == {
        "data": [
            {
                "vendor": "cloudflare",
                "cli": "cf",
                "mode": "path",
                "address": "http://127.0.0.1:8100/_facade/cloudflare",
                "protocols": ["rest"],
            }
        ]
    }


def test_list_cli_facades_requires_capabilities_read() -> None:
    resp = _client(_NO_READ).get("/cli-facades")

    assert resp.status_code == 403
