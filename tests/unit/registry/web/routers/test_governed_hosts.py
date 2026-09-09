"""Unit tests for the governed-hosts route's HTTP contract (#1278).

The pure-HTTP behaviours — ETag emission, ``If-None-Match`` comparison (quoted,
weak ``W/``, ``*``, bare-digest compatibility), 304 body-emptiness, and the
identity-scoped cache headers — run here against a stubbed service so they are
exercised on the unit leg of every PR, on every backend. The three-database
derivation itself is covered by the integration and web suites.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.registry.services.governed_hosts_service import (
    GovernedHostsView,
    compute_hosts_digest,
)
from jentic_one.registry.web.deps import get_governed_hosts_service
from jentic_one.registry.web.routers import governed_hosts
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web.deps import resolve_identity

_HOSTS = ("a.gvh.test", "b.gvh.test")
_DIGEST = compute_hosts_digest(_HOSTS)


class _StubService:
    async def get_governed_hosts(self, identity: Identity) -> GovernedHostsView:
        return GovernedHostsView(hosts=_HOSTS, digest=_DIGEST)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(governed_hosts.router)
    identity = Identity(sub="agt_test", permissions=["toolkits:read"])
    app.dependency_overrides[resolve_identity] = lambda: identity
    app.dependency_overrides[get_governed_hosts_service] = lambda: _StubService()
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def test_200_carries_etag_and_private_cache_headers(client: TestClient) -> None:
    resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert resp.json() == {"data": list(_HOSTS), "digest": _DIGEST}
    assert resp.headers["ETag"] == f'"{_DIGEST}"'
    # Identity-scoped body: a URL-keyed shared cache must never store it.
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert resp.headers["Vary"] == "Authorization"


@pytest.mark.parametrize(
    "if_none_match",
    [
        pytest.param(f'"{_DIGEST}"', id="quoted-strong"),
        pytest.param(f'W/"{_DIGEST}"', id="weak"),
        pytest.param("*", id="star"),
        pytest.param(_DIGEST, id="bare-digest-compat"),
        pytest.param(f'"other-etag", "{_DIGEST}"', id="list-member"),
    ],
)
def test_matching_if_none_match_yields_empty_304(client: TestClient, if_none_match: str) -> None:
    resp = client.get("/governed-hosts", headers={"If-None-Match": if_none_match})
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["ETag"] == f'"{_DIGEST}"'
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert resp.headers["Vary"] == "Authorization"


@pytest.mark.parametrize(
    "if_none_match",
    [
        pytest.param('"stale-digest"', id="stale"),
        pytest.param("garbage", id="garbage-no-400"),
        pytest.param("", id="empty-header"),
    ],
)
def test_non_matching_if_none_match_yields_full_200(client: TestClient, if_none_match: str) -> None:
    resp = client.get("/governed-hosts", headers={"If-None-Match": if_none_match})
    assert resp.status_code == 200
    assert resp.json()["digest"] == _DIGEST


def test_openapi_declares_the_poll_contract() -> None:
    """The 304/ETag/If-None-Match seam must be in the spec, not prose only —
    otherwise the generated Go/TS clients cannot express the endpoint's
    entire purpose."""
    app = FastAPI()
    app.include_router(governed_hosts.router)
    op = app.openapi()["paths"]["/governed-hosts"]["get"]
    assert "304" in op["responses"]
    assert "ETag" in op["responses"]["200"]["headers"]
    assert "ETag" in op["responses"]["304"]["headers"]
    header_params = [p["name"].lower() for p in op.get("parameters", []) if p["in"] == "header"]
    assert "if-none-match" in header_params
