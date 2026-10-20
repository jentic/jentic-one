"""Authenticated JSON from the real app factories is ``private, no-store`` (#1835).

Builds each standalone surface through its public ``create_app`` and the
combined control-plane app through :func:`create_combined_app`, without a
database (``Context`` is lazy and the lifespan is never entered). Then:

* walks :func:`build_operation_auth_map` and sends a credentialed request to
  every authenticated, parameter-free ``GET`` route. The identity dependency
  is stubbed to deny, so each route answers through the shared error path
  with a problem+json body, which must carry ``Cache-Control: private,
  no-store``. That pins the default on each surface's error responses; a
  route's success body rides the same middleware but is not exercised here,
  since producing one needs a database;
* checks a credentialed 500 (answered outside the user middleware stack) is
  marked and an anonymous one is not;
* checks ``HEAD``, the token endpoint's own ``no-store`` and the admin event
  stream's ``no-cache`` survive unchanged;
* exercises the broker's forward proxy, whose routes resolve identity outside
  the closures the auth map recognises, with a stub validator.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jentic.problem_details import Forbidden

from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.admin.web.deps import get_event_stream_service
from jentic_one.auth.web.app import create_app as create_auth_app
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.web.app import create_app as create_control_app
from jentic_one.registry.web.app import create_app as create_registry_app
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.cache_control import PRIVATE_NO_STORE
from jentic_one.shared.web.deps import (
    CredentialRecheckResult,
    get_credential_recheck,
    resolve_identity,
)
from jentic_one.shared.web.endpoint_permissions import build_operation_auth_map

_CREDENTIAL = {"Authorization": "Bearer test-token"}

_STANDALONE: dict[str, Callable[[Context], FastAPI]] = {
    "admin": create_admin_app,
    "control": create_control_app,
    "registry": create_registry_app,
    "auth": create_auth_app,
}
_COMBINED = ["admin", "control", "registry", "auth"]
_SHAPES = [*_STANDALONE, "combined"]


@pytest.fixture()
def ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


def _build(ctx: Context, shape: str) -> FastAPI:
    if shape == "combined":
        return create_combined_app(ctx, _COMBINED)
    return _STANDALONE[shape](ctx)


def _client(app: FastAPI) -> TestClient:
    # Never enter the lifespan: no DB connection is made.
    return TestClient(app, raise_server_exceptions=False)


def _prefix(shape: str, surface: str) -> str:
    """Path prefix of ``surface``'s health route in ``shape``."""
    return f"/{surface}" if shape == "combined" else ""


async def _deny(request: Request) -> Identity:
    raise Forbidden(detail="denied", instance=request.url.path, type="forbidden")


async def _explode() -> Identity:
    raise RuntimeError("identity backend exploded")


def _authenticated_get_paths(app: FastAPI) -> list[str]:
    return sorted(
        path
        for (method, path), entry in build_operation_auth_map(app).items()
        if method == "GET" and entry["authenticated"] and "{" not in path
    )


@pytest.mark.parametrize("shape", _SHAPES)
def test_authenticated_routes_are_private_no_store(ctx: Context, shape: str) -> None:
    app = _build(ctx, shape)
    app.dependency_overrides[resolve_identity] = _deny
    client = _client(app)

    paths = _authenticated_get_paths(app)
    assert paths, f"no authenticated parameter-free GET route found on {shape}"
    for path in paths:
        resp = client.get(path, headers=_CREDENTIAL)
        assert resp.status_code == 403, (path, resp.status_code, resp.text)
        assert resp.headers["content-type"].startswith("application/problem+json"), path
        assert resp.headers.get_list("cache-control") == [PRIVATE_NO_STORE], path


@pytest.mark.parametrize("shape", _SHAPES)
def test_unhandled_500_is_private_no_store_only_when_credentialed(ctx: Context, shape: str) -> None:
    """The catch-all 500 is answered by ``ServerErrorMiddleware``, outside the
    user middleware, so its handler marks a credentialed request's body itself."""
    app = _build(ctx, shape)
    app.dependency_overrides[resolve_identity] = _explode
    client = _client(app)
    path = _authenticated_get_paths(app)[0]

    credentialed = client.get(path, headers=_CREDENTIAL)
    assert credentialed.status_code == 500, credentialed.text
    assert credentialed.headers["content-type"].startswith("application/problem+json")
    assert credentialed.headers.get_list("cache-control") == [PRIVATE_NO_STORE]

    anonymous = client.get(path)
    assert anonymous.status_code == 500, anonymous.text
    assert "cache-control" not in anonymous.headers


@pytest.mark.parametrize("shape", _SHAPES)
def test_head_on_authenticated_route_is_private_no_store(ctx: Context, shape: str) -> None:
    """GET routes do not serve HEAD (405); that JSON answer is still marked."""
    app = _build(ctx, shape)
    app.dependency_overrides[resolve_identity] = _deny
    client = _client(app)
    path = _authenticated_get_paths(app)[0]

    resp = client.head(path, headers=_CREDENTIAL)
    assert resp.status_code == 405, (path, resp.status_code)
    assert resp.headers.get_list("cache-control") == [PRIVATE_NO_STORE]


@pytest.mark.parametrize("shape", _SHAPES)
def test_anonymous_health_has_no_cache_control(ctx: Context, shape: str) -> None:
    surface = "admin" if shape == "combined" else shape
    resp = _client(_build(ctx, shape)).get(f"{_prefix(shape, surface)}/health")
    assert resp.status_code == 200, resp.text
    assert "cache-control" not in resp.headers


@pytest.mark.parametrize("shape", ["auth", "combined"])
def test_token_endpoint_keeps_its_single_no_store(ctx: Context, shape: str) -> None:
    """``POST /oauth/token`` sets ``no-store`` itself (RFC 6749 §5.1/§5.2); the
    default must not add a second ``Cache-Control`` value."""
    client = _client(_build(ctx, shape))
    resp = client.post(
        "/oauth/token",
        headers={**_CREDENTIAL, "Content-Type": "application/json"},
        content=b"",
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_request"
    assert resp.headers.get_list("cache-control") == ["no-store"]


class _EmptyStream:
    async def stream(self, **_: Any) -> AsyncIterator[Any]:
        return
        yield


@pytest.mark.parametrize("shape", ["admin", "combined"])
def test_event_stream_keeps_no_cache(ctx: Context, shape: str) -> None:
    app = _build(ctx, shape)
    identity = Identity(sub="usr_test", permissions=["events:read"])
    app.dependency_overrides[resolve_identity] = lambda: identity
    app.dependency_overrides[get_event_stream_service] = lambda: _EmptyStream()

    async def _valid() -> CredentialRecheckResult:
        return CredentialRecheckResult.VALID

    app.dependency_overrides[get_credential_recheck] = lambda: _valid

    resp = _client(app).get("/events/stream", headers=_CREDENTIAL)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get_list("cache-control") == ["no-cache"]


class _RejectingValidator:
    async def validate(self, token: str) -> Identity:
        raise TokenValidationError("unknown_token")


def test_broker_forward_proxy_is_private_no_store(ctx: Context) -> None:
    app = create_broker_app(ctx)
    app.state.broker_token_validator = _RejectingValidator()
    client = _client(app)

    resp = client.get("/api.example.com/v1/items", headers=_CREDENTIAL)
    assert resp.status_code == 401, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers.get_list("cache-control") == [PRIVATE_NO_STORE]

    anonymous = client.get("/health")
    assert anonymous.status_code == 200
    assert "cache-control" not in anonymous.headers
