"""Web tests for the GET /apis/{vendor}/{name}/{version} endpoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.security_schemes import SecurityScheme
from jentic_one.registry.core.schema.servers import Server
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


async def _seed_api(
    ctx: Context,
    *,
    vendor: str = "acme",
    name: str = "payments",
    version: str = "v1",
    display_name: str | None = "Acme Payments",
    description: str | None = "Payment processing API",
    icon_url: str | None = "https://acme.com/icon.png",
    with_revision: bool = False,
    server_url: str = "https://api.acme.com/v1",
    security_types: list[str] | None = None,
    updated_at: datetime | None = None,
) -> Api:
    """Seed an Api (and optionally a published revision with servers/schemes)."""
    async with ctx.registry_db.session() as session:
        api = Api(
            vendor=vendor,
            name=name,
            version=version,
            display_name=display_name,
            description=description,
            icon_url=icon_url,
            revision_count=1 if with_revision else 0,
            operation_count=5 if with_revision else 0,
        )
        session.add(api)
        await session.flush()

        if updated_at is not None:
            api.updated_at = updated_at
            await session.flush()

        if with_revision:
            revision = ApiRevision(
                api_id=api.id,
                state="published",
                spec_digest="sha256:abc123",
                operation_count=5,
            )
            session.add(revision)
            await session.flush()

            server = Server(
                revision_id=revision.id,
                url=server_url,
            )
            session.add(server)

            if security_types:
                for st in security_types:
                    scheme = SecurityScheme(
                        revision_id=revision.id,
                        name=f"{st}_scheme",
                        type=st,
                        raw_scheme={"type": st},
                    )
                    session.add(scheme)

            await session.flush()
            api.current_revision_id = revision.id
            await session.flush()

        await session.commit()
        return api


@pytest.fixture(autouse=True)
async def _cleanup_apis(web_context: Context) -> AsyncGenerator[None]:
    """Clean up seeded APIs after each test."""
    yield
    async with web_context.registry_db.session() as session:
        await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
        await session.execute(text("DELETE FROM registry.servers"))
        await session.execute(text("DELETE FROM registry.security_schemes"))
        await session.execute(text("DELETE FROM registry.api_revisions"))
        await session.execute(text("DELETE FROM registry.apis"))
        await session.commit()


async def test_get_api_returns_full_aggregate(
    authed_client: TestClient, web_context: Context
) -> None:
    await _seed_api(
        web_context,
        with_revision=True,
        security_types=["oauth2", "apiKey"],
    )
    resp = authed_client.get("/apis/acme/payments/v1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["api"]["vendor"] == "acme"
    assert data["api"]["name"] == "payments"
    assert data["api"]["version"] == "v1"
    assert data["api"]["host"] == "api.acme.com"
    assert data["display_name"] == "Acme Payments"
    assert data["description"] == "Payment processing API"
    assert data["icon_url"] == "https://acme.com/icon.png"
    assert data["current_revision_id"] is not None
    assert data["revision_count"] == 1
    assert data["operation_count"] == 5
    assert data["security_schemes"] == ["apiKey", "oauth2"]
    assert "created_at" in data
    assert "updated_at" in data
    assert "_links" in data
    assert "/apis/acme/payments/v1" in data["_links"]["self"]
    assert "/apis/acme/payments/v1/revisions" in data["_links"]["revisions"]
    assert data["_links"]["current_revision"] is not None


async def test_get_api_not_found_returns_404(authed_client: TestClient) -> None:
    resp = authed_client.get("/apis/unknown/api/v1")
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    data = resp.json()
    assert data["type"] == "api_not_found"
    assert "unknown/api/v1" in data["detail"]


async def test_get_api_no_current_revision(authed_client: TestClient, web_context: Context) -> None:
    await _seed_api(web_context, with_revision=False)
    resp = authed_client.get("/apis/acme/payments/v1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["api"]["host"] is None
    assert data["security_schemes"] == []
    assert data["current_revision_id"] is None
    assert data["_links"]["current_revision"] is None


async def test_get_api_without_auth_returns_401(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/apis/acme/payments/v1")
    assert resp.status_code == 401


async def test_get_api_updated_at_fallback(authed_client: TestClient, web_context: Context) -> None:
    await _seed_api(web_context, with_revision=False)
    resp = authed_client.get("/apis/acme/payments/v1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated_at"] == data["created_at"]


@pytest.mark.parametrize(
    "path",
    ["/apis/Stripe.com/API/1", "/apis/stripe-com/api/1", "/apis/stripe.com/Api/1"],
)
async def test_get_api_normalises_vendor_and_name_like_search(
    authed_client: TestClient, web_context: Context, path: str
) -> None:
    """Pins #1958: the path lookup accepts every vendor/name spelling the search
    ``api`` filter does, and answers with the stored slug identity."""
    await _seed_api(web_context, vendor="stripe-com", name="api", version="1", with_revision=True)

    resp = authed_client.get(path)
    assert resp.status_code == 200
    data = resp.json()
    assert data["api"] == {
        "vendor": "stripe-com",
        "name": "api",
        "version": "1",
        "host": "api.acme.com",
    }
    assert data["_links"]["self"].endswith("/apis/stripe-com/api/1")

    schemes = authed_client.get(f"{path}/security-schemes")
    assert schemes.status_code == 200


async def test_get_api_matches_version_verbatim(
    authed_client: TestClient, web_context: Context
) -> None:
    """The version segment is never slugified, so ``1.1.4`` stays ``1.1.4``."""
    await _seed_api(web_context, vendor="acme", name="payments", version="1.1.4")

    assert authed_client.get("/apis/Acme/Payments/1.1.4").status_code == 200
    assert authed_client.get("/apis/acme/payments/1-1-4").status_code == 404
