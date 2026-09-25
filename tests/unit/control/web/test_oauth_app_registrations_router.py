"""Unit tests for the OAuth app registrations router — HTTP contract, not state.

Pins the router's six endpoints (list, create, get, update, rotate-secret,
delete) to the response shapes, permission gating, and error → HTTP
status mapping the admin UI relies on. State-machine behaviour is
covered in the integration test at
``tests/integration/control/test_oauth_app_registration_service.py``;
this file mocks the service at the dependency boundary and exercises
only the router.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.control.services.oauth_app_registrations.errors import (
    OAuthAppRegistrationInUseError,
    OAuthAppRegistrationNotFoundError,
    SecretRotationNotSupportedError,
)
from jentic_one.control.services.oauth_app_registrations.schemas import (
    OAuthAppRegistrationFlowKind,
    OAuthAppRegistrationView,
)
from jentic_one.control.services.oauth_app_registrations.service import (
    OAuthAppRegistrationService,
)
from jentic_one.control.web.app import get_exception_handlers
from jentic_one.control.web.deps import get_oauth_app_registration_service
from jentic_one.control.web.routers import oauth_app_registrations as router_module
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import deps as shared_deps

_ADMIN_IDENTITY = Identity(
    sub="usr_admin",
    permissions=["org:admin", "credentials:read"],
)
_READER_IDENTITY = Identity(
    sub="usr_reader",
    permissions=["credentials:read"],
)


def _mk_view(
    *,
    registration_id: str = "oar_test",
    name: str = "MyOrg Prod Gmail",
    api_vendor: str = "googleapis-com",
    flow_kind: OAuthAppRegistrationFlowKind = OAuthAppRegistrationFlowKind.AUTHORIZATION_CODE,
    has_client_secret: bool = True,
    dependent_credential_count: int = 0,
) -> OAuthAppRegistrationView:
    """Factory for a plausible view — every field the router projects."""
    now = dt.datetime(2026, 9, 25, 12, 0, tzinfo=dt.UTC)
    is_auth_code = flow_kind is OAuthAppRegistrationFlowKind.AUTHORIZATION_CODE
    return OAuthAppRegistrationView(
        id=registration_id,
        name=name,
        api_vendor=api_vendor,
        catalog_api_id=f"{api_vendor}/gmail",
        display_name="Gmail",
        flow_kind=flow_kind,
        client_id="cid",
        is_active=True,
        has_client_secret=has_client_secret,
        secret_last_rotated_at=None,
        authorize_url="https://ex/a" if is_auth_code else None,
        token_url="https://ex/t" if is_auth_code else None,
        authorization_endpoint=None if is_auth_code else "https://ex/d",
        token_endpoint=None if is_auth_code else "https://ex/t",
        default_scopes=None,
        created_at=now,
        updated_at=now,
        created_by="usr_admin",
        dependent_credential_count=dependent_credential_count,
    )


def _build_app(*, svc: Any, identity: Identity = _ADMIN_IDENTITY) -> FastAPI:
    """Minimal FastAPI wired with just this router + full exception mapping.

    Service is an ``AsyncMock`` spec'd on ``OAuthAppRegistrationService`` —
    tests assert the router forwarded the right args and mapped the right
    exceptions. Identity is stubbed via the shared ``resolve_identity``
    dependency; permission gates on the route are honoured because they
    delegate through that dependency.
    """
    app = FastAPI()
    app.include_router(router_module.router)
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.dependency_overrides[get_oauth_app_registration_service] = lambda: svc
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: identity
    return app


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def _auth_code_body() -> dict[str, Any]:
    return {
        "name": "MyOrg Prod Gmail",
        "api_vendor": "googleapis-com",
        "catalog_api_id": "googleapis-com/gmail",
        "display_name": "Gmail",
        "flow_kind": "authorization_code",
        "client_id": "cid",
        "client_secret": "<paste>",  # pragma: allowlist secret
        "authorize_url": "https://ex/a",
        "token_url": "https://ex/t",
    }


def _device_body() -> dict[str, Any]:
    return {
        "name": "MyOrg Gmail Device",
        "api_vendor": "googleapis-com",
        "catalog_api_id": "googleapis-com/gmail",
        "display_name": "Gmail",
        "flow_kind": "device_authorization",
        "client_id": "cid",
        "authorization_endpoint": "https://ex/d",
        "token_endpoint": "https://ex/t",
    }


def test_create_auth_code_returns_view_without_leaking_secret() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.create_authorization_code = AsyncMock(return_value=_mk_view())
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.post("/oauth-app-registrations", json=_auth_code_body())
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_client_secret"] is True
    # No leaked secret anywhere in the response body.
    assert "client_secret" not in body
    assert "<paste>" not in resp.text
    # Router forwarded the create's kwargs verbatim.
    call = svc.create_authorization_code.await_args
    assert call is not None
    assert call.kwargs["name"] == "MyOrg Prod Gmail"
    assert call.kwargs["catalog_api_id"] == "googleapis-com/gmail"
    assert call.kwargs["client_secret"] == "<paste>"  # pragma: allowlist secret


def test_create_device_flow_reports_no_client_secret() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.create_device_authorization = AsyncMock(
        return_value=_mk_view(
            flow_kind=OAuthAppRegistrationFlowKind.DEVICE_AUTHORIZATION,
            has_client_secret=False,
        )
    )
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.post("/oauth-app-registrations", json=_device_body())
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_client_secret"] is False
    assert body["flow_kind"] == "device_authorization"
    assert "client_secret" not in body


def test_create_requires_catalog_api_id() -> None:
    """The refactor made ``catalog_api_id`` mandatory on new registrations."""
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    app = _build_app(svc=svc)
    body = _auth_code_body()
    del body["catalog_api_id"]
    with TestClient(app) as client:
        resp = client.post("/oauth-app-registrations", json=body)
    assert resp.status_code == 422  # pydantic missing-field
    svc.create_authorization_code.assert_not_called()


def test_create_requires_display_name() -> None:
    """``display_name`` is also mandatory — the picker needs a vendor label."""
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    app = _build_app(svc=svc)
    body = _auth_code_body()
    del body["display_name"]
    with TestClient(app) as client:
        resp = client.post("/oauth-app-registrations", json=body)
    assert resp.status_code == 422
    svc.create_authorization_code.assert_not_called()


def test_create_refused_for_non_admin() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    app = _build_app(svc=svc, identity=_READER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post("/oauth-app-registrations", json=_auth_code_body())
    assert resp.status_code == 403
    svc.create_authorization_code.assert_not_called()


# ---------------------------------------------------------------------------
# Get / list
# ---------------------------------------------------------------------------


def test_get_returns_view_never_secret() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.get = AsyncMock(return_value=_mk_view())
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.get("/oauth-app-registrations/oar_test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "oar_test"
    assert body["has_client_secret"] is True
    assert body["secret_last_rotated_at"] is None
    assert "client_secret" not in body
    assert "encrypted_client_secret" not in body


def test_get_maps_not_found_to_404() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.get = AsyncMock(side_effect=OAuthAppRegistrationNotFoundError("oar_missing"))
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.get("/oauth-app-registrations/oar_missing")
    assert resp.status_code == 404
    assert resp.json()["type"] == "oauth_app_registration_not_found"


def test_list_is_readable_by_credentials_read_only() -> None:
    """Reads gate on ``credentials:read`` — no ``org:admin`` needed."""
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.list_all = AsyncMock(return_value=[_mk_view()])
    app = _build_app(svc=svc, identity=_READER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/oauth-app-registrations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1


def test_get_is_readable_by_credentials_read_only() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.get = AsyncMock(return_value=_mk_view())
    app = _build_app(svc=svc, identity=_READER_IDENTITY)
    with TestClient(app) as client:
        resp = client.get("/oauth-app-registrations/oar_test")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Update / rotate-secret / delete
# ---------------------------------------------------------------------------


def test_update_refused_for_non_admin() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    app = _build_app(svc=svc, identity=_READER_IDENTITY)
    with TestClient(app) as client:
        resp = client.patch(
            "/oauth-app-registrations/oar_test",
            json={"name": "renamed"},
        )
    assert resp.status_code == 403
    svc.update.assert_not_called()


def test_rotate_secret_refused_on_device_flow() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.rotate_client_secret = AsyncMock(
        side_effect=SecretRotationNotSupportedError("oar_device"),
    )
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.post(
            "/oauth-app-registrations/oar_device:rotate-secret",
            json={"client_secret": "new"},  # pragma: allowlist secret
        )
    assert resp.status_code == 409
    assert resp.json()["type"] == "secret_rotation_not_supported"


def test_rotate_secret_refused_for_non_admin() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    app = _build_app(svc=svc, identity=_READER_IDENTITY)
    with TestClient(app) as client:
        resp = client.post(
            "/oauth-app-registrations/oar_test:rotate-secret",
            json={"client_secret": "new"},  # pragma: allowlist secret
        )
    assert resp.status_code == 403
    svc.rotate_client_secret.assert_not_called()


def test_delete_returns_204_on_success() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.delete = AsyncMock(return_value=None)
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.delete("/oauth-app-registrations/oar_test")
    assert resp.status_code == 204
    call = svc.delete.await_args
    assert call is not None
    assert call.args == ("oar_test",)


def test_delete_maps_in_use_conflict_to_409_with_slug() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    svc.delete = AsyncMock(
        side_effect=OAuthAppRegistrationInUseError("oar_test", credential_count=3),
    )
    app = _build_app(svc=svc)
    with TestClient(app) as client:
        resp = client.delete("/oauth-app-registrations/oar_test")
    assert resp.status_code == 409
    assert resp.json()["type"] == "oauth_app_registration_in_use"


def test_delete_refused_for_non_admin() -> None:
    svc = AsyncMock(spec=OAuthAppRegistrationService)
    app = _build_app(svc=svc, identity=_READER_IDENTITY)
    with TestClient(app) as client:
        resp = client.delete("/oauth-app-registrations/oar_test")
    assert resp.status_code == 403
    svc.delete.assert_not_called()
