"""Unit tests for the route permission guard in ``get_current_identity``.

These exercise the guard closure in isolation (no DB) by overriding
``resolve_identity`` with a fixed ``Identity`` and asserting the 200/403 outcome
of the ``required_permissions`` check — in particular that the guard expands the
static implication map so the advertised ``*:write ⇒ *:read`` semantics hold at
enforcement, matching production identity resolution.
"""

from __future__ import annotations

from typing import cast

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import resolve_identity


def _client_for(identity: Identity, *, required: list[str]) -> TestClient:
    app = FastAPI()
    guard = get_current_identity(required_permissions=required)

    @app.get("/guarded")
    async def _endpoint(_id: Identity = guard) -> dict[str, bool]:
        return {"ok": True}

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    return TestClient(app)


def _get(identity: Identity, *, required: list[str]) -> httpx.Response:
    return cast(httpx.Response, _client_for(identity, required=required).get("/guarded"))


def _identity(*permissions: str) -> Identity:
    return Identity(sub="usr_guard_test", email="guard@test.local", permissions=list(permissions))


def test_direct_scope_allows() -> None:
    assert _get(_identity("apis:read"), required=["apis:read"]).status_code == 200


def test_write_scope_implies_read() -> None:
    # apis:write ⇒ apis:read in the implication map; the guard must expand it.
    assert _get(_identity("apis:write"), required=["apis:read"]).status_code == 200


def test_unrelated_scope_denied() -> None:
    assert _get(_identity("events:read"), required=["apis:read"]).status_code == 403


def test_no_permissions_denied() -> None:
    assert _get(_identity(), required=["apis:read"]).status_code == 403


def test_org_admin_bypasses_required_permissions() -> None:
    assert _get(_identity("org:admin"), required=["apis:read"]).status_code == 200


def test_or_listed_owner_scope_allows() -> None:
    # A delegated agent holding only the owner-read scope passes a route that
    # OR-lists it alongside the bare read scope.
    identity = _identity("owner:credentials:read")
    resp = _get(identity, required=["credentials:read", "owner:credentials:read"])
    assert resp.status_code == 200


def test_overlays_confirm_scope_allows() -> None:
    # The purpose-scoped operator grant satisfies the confirm gate on its own.
    assert _get(_identity("overlays:confirm"), required=["overlays:confirm"]).status_code == 200


def test_apis_write_does_not_imply_overlays_confirm() -> None:
    # A contributor who can submit overlays (apis:write) must NOT be able to confirm
    # them: apis:write does not imply overlays:confirm in the implication map.
    assert _get(_identity("apis:write"), required=["overlays:confirm"]).status_code == 403


def test_org_admin_satisfies_overlays_confirm() -> None:
    # org:admin remains a superset (it implies overlays:confirm and short-circuits),
    # so the downgrade never locks existing admins out of confirm.
    assert _get(_identity("org:admin"), required=["overlays:confirm"]).status_code == 200
