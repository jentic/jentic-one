"""Tests for the shared ``/apis/{vendor}/{name}/{version}`` path parameters (#1958)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import AfterValidator

from jentic_one.registry.web.params import ApiNamePath, ApiVendorPath, ApiVersionPath
from jentic_one.registry.web.routers import apis, overlays
from jentic_one.shared.models import slugify_api_field


@pytest.fixture()
def echo_client() -> TestClient:
    app = FastAPI()

    @app.get("/apis/{vendor}/{name}/{version}")
    async def _echo(
        vendor: ApiVendorPath, name: ApiNamePath, version: ApiVersionPath
    ) -> dict[str, str]:
        return {"vendor": vendor, "name": name, "version": version}

    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/apis/Stripe.com/API/1", ("stripe-com", "api", "1")),
        ("/apis/stripe-com/api/1", ("stripe-com", "api", "1")),
        ("/apis/stripe.com/Payments API/1.1.4", ("stripe-com", "payments-api", "1.1.4")),
        ("/apis/acme/pets/V2", ("acme", "pets", "V2")),
    ],
)
def test_path_params_normalise_vendor_and_name_but_keep_version(
    echo_client: TestClient, path: str, expected: tuple[str, str, str]
) -> None:
    resp = echo_client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert (body["vendor"], body["name"], body["version"]) == expected


def _api_identity_routes(router: APIRouter) -> list[APIRoute]:
    return [r for r in router.routes if isinstance(r, APIRoute) and "{vendor}" in r.path]


@pytest.mark.parametrize("router", [apis.router, overlays.router], ids=["apis", "overlays"])
def test_every_api_identity_route_slugifies_vendor_and_name(router: APIRouter) -> None:
    """Pins #1958: every ``/apis/{vendor}/{name}/...`` route resolves the same
    spellings search does, so a route declaring a bare ``str`` vendor/name fails here."""
    routes = _api_identity_routes(router)
    assert routes
    for route in routes:
        params = {p.name: p for p in route.dependant.path_params}
        for field in ("vendor", "name"):
            metadata = params[field].field_info.metadata
            assert AfterValidator(slugify_api_field) in metadata, (
                f"{route.path}: {field} is not slug-normalised"
            )
        assert not any(isinstance(m, AfterValidator) for m in params["version"].field_info.metadata)
