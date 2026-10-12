"""Unit tests for the vendors router: the ``GET /vendors`` wire shape.

The service is mocked at the dependency boundary; the registry's merge of
config and DB entries is covered by the service tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.control.services.vendors.schemas import VendorEntry
from jentic_one.control.web.deps import get_vendor_registry_service
from jentic_one.control.web.routers import vendors as router_module
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import deps as shared_deps

_READER = Identity(sub="usr_reader", permissions=["capabilities:read"])


def _client(entries: list[VendorEntry]) -> TestClient:
    svc = MagicMock()
    svc.list_entries = AsyncMock(return_value=entries)
    svc._config.entries = {}
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_vendor_registry_service] = lambda: svc
    app.dependency_overrides[shared_deps.resolve_identity] = lambda: _READER
    return TestClient(app)


def test_list_vendors_carries_a_shared_apps_catalog_api_id() -> None:
    # The UI matches a shared app to the API a credential form is on by this
    # field, so it has to reach the wire, not just the service view.
    shared_app = VendorEntry(
        entry_id="oar_gmail",
        registration_id="oar_gmail",
        key="googleapis.com",
        display_name="Gmail",
        name="Acme Gmail",
        flow_kind="authorization_code",
        flow_kinds=["authorization_code"],
        client_id="client-1",
        has_client_secret=True,
        default_scopes=[],
        source="db",
        catalog_api_id="googleapis.com/gmail",
    )

    resp = _client([shared_app]).get("/vendors")

    assert resp.status_code == 200
    (row,) = resp.json()["data"]
    assert row["source"] == "db"
    assert row["catalog_api_id"] == "googleapis.com/gmail"
