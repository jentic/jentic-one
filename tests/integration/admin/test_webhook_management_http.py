"""HTTP-level tests for the webhook management API.

These prove the things only a real request can: that the privileged operations are
actually **scope-gated** (not merely documented as such), that secrets appear in
exactly one response and never in reads, and that the AIP-style action verbs are
routed correctly.

Authentication is injected by replacing ``app.state.verify_token`` — the same seam
the real API-key/JWT verifier plugs into — so the permission dependency runs for
real rather than being stubbed out.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from jentic.problem_details import Unauthorized
from sqlalchemy import delete

from jentic_one.admin.core.permissions import WEBHOOKS_READ, WEBHOOKS_WRITE
from jentic_one.admin.core.schema.webhook_deliveries import WebhookDelivery
from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.core.schema.webhook_events import WebhookEvent
from jentic_one.admin.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.actors import ActorType

pytestmark = pytest.mark.integration

ENDPOINTS_PATH = "/webhooks/endpoints"


@pytest.fixture()
async def clean_webhooks(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(WebhookDelivery))
            await session.execute(delete(WebhookEvent))
            await session.execute(delete(WebhookEndpoint))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.fixture()
def client_factory(
    integration_context: Context,
) -> Callable[[list[str]], AsyncClient]:
    """Build a client whose bearer token resolves to the given permissions."""

    def _build(permissions: list[str]) -> AsyncClient:
        app: FastAPI = create_app(integration_context)

        async def _verify(token: str, request: Request) -> Identity:
            if token != "test-token":
                raise Unauthorized(detail="Invalid token")
            return Identity(
                sub="usr_operator",
                email="ops@test.local",
                actor_type=ActorType.USER,
                permissions=permissions,
            )

        app.state.verify_token = _verify
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://admin.test",
            headers={"authorization": "Bearer test-token"},
        )

    return _build


@pytest.fixture()
async def writer(
    client_factory: Callable[[list[str]], AsyncClient],
) -> AsyncGenerator[AsyncClient, None]:
    async with client_factory([WEBHOOKS_WRITE, WEBHOOKS_READ]) as client:
        yield client


@pytest.fixture()
async def reader(
    client_factory: Callable[[list[str]], AsyncClient],
) -> AsyncGenerator[AsyncClient, None]:
    async with client_factory([WEBHOOKS_READ]) as client:
        yield client


async def _create_notification(writer: AsyncClient, name: str = "ops-slack") -> dict:  # type: ignore[type-arg]
    response = await writer.post(
        ENDPOINTS_PATH,
        json={
            "name": name,
            "target_url": "https://receiver.test/hook",
            "event_types": ["credential.expired"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


# --- authorisation ------------------------------------------------------------


async def test_creating_an_endpoint_requires_webhooks_write(
    reader: AsyncClient, clean_webhooks: None
) -> None:
    """Read access must not be enough to mint a URL that spends authority."""
    response = await reader.post(
        ENDPOINTS_PATH,
        json={
            "name": "should-not-exist",
            "target_url": "https://receiver.test/hook",
        },
    )
    assert response.status_code == 403, response.text


async def test_deleting_an_endpoint_requires_webhooks_write(
    writer: AsyncClient, reader: AsyncClient, clean_webhooks: None
) -> None:
    created = await _create_notification(writer)
    response = await reader.delete(f"{ENDPOINTS_PATH}/{created['endpoint']['endpoint_id']}")
    assert response.status_code == 403


async def test_rotating_a_secret_requires_webhooks_write(
    writer: AsyncClient, reader: AsyncClient, clean_webhooks: None
) -> None:
    """Rotation is a secret-disclosing operation, so reads must not reach it."""
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]
    response = await reader.post(f"{ENDPOINTS_PATH}/{endpoint_id}:rotate-secret", json={})
    assert response.status_code == 403


async def test_unauthenticated_management_request_is_rejected(
    integration_context: Context, clean_webhooks: None
) -> None:
    """Management needs a platform identity."""
    app = create_app(integration_context)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://admin.test"
    ) as anonymous:
        response = await anonymous.get(ENDPOINTS_PATH)
    assert response.status_code == 401


async def test_reader_can_list(
    writer: AsyncClient, reader: AsyncClient, clean_webhooks: None
) -> None:
    await _create_notification(writer)
    response = await reader.get(ENDPOINTS_PATH)
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


# --- secret exposure ----------------------------------------------------------


async def test_secret_is_returned_once_on_create_and_never_again(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    """The single most important property of this API.

    If a read endpoint ever returned the secret, every reader would become a
    secret holder.
    """
    created = await _create_notification(writer)
    assert created["secret"].startswith("whsec_")
    endpoint_id = created["endpoint"]["endpoint_id"]

    fetched = await writer.get(f"{ENDPOINTS_PATH}/{endpoint_id}")
    listed = await writer.get(ENDPOINTS_PATH)

    for body in (fetched.text, listed.text):
        assert created["secret"] not in body, "plaintext secret leaked through a read"
        assert "secret_encrypted" not in body, "ciphertext must not be exposed either"
        assert "secret_hash" not in body


async def test_rotation_returns_the_new_secret_and_an_expiry(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]

    response = await writer.post(
        f"{ENDPOINTS_PATH}/{endpoint_id}:rotate-secret", json={"grace_seconds": 3600}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["secret"].startswith("whsec_")
    assert body["secret"] != created["secret"]
    assert body["previous_secret_expires_at"] is not None, "the old key stays valid for a while"


async def test_zero_grace_rotation_has_no_expiry(writer: AsyncClient, clean_webhooks: None) -> None:
    """Immediate revocation leaves no previous secret to expire."""
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]

    response = await writer.post(
        f"{ENDPOINTS_PATH}/{endpoint_id}:rotate-secret", json={"grace_seconds": 0}
    )
    assert response.status_code == 200
    assert response.json()["previous_secret_expires_at"] is None


# --- lifecycle ----------------------------------------------------------------


async def test_get_unknown_endpoint_is_404(writer: AsyncClient, clean_webhooks: None) -> None:
    response = await writer.get(f"{ENDPOINTS_PATH}/whep_missing")
    assert response.status_code == 404


async def test_delete_then_get_is_404(writer: AsyncClient, clean_webhooks: None) -> None:
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]

    deleted = await writer.delete(f"{ENDPOINTS_PATH}/{endpoint_id}")
    assert deleted.status_code == 204

    assert (await writer.get(f"{ENDPOINTS_PATH}/{endpoint_id}")).status_code == 404


# --- deliveries ---------------------------------------------------------------


async def test_test_event_then_delivery_log(writer: AsyncClient, clean_webhooks: None) -> None:
    """The operator's confirm-my-wiring loop, end to end over HTTP."""
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]

    queued = await writer.post(f"{ENDPOINTS_PATH}/{endpoint_id}:test")
    assert queued.status_code == 202, queued.text
    delivery_id = queued.json()["delivery_id"]

    log = await writer.get(f"{ENDPOINTS_PATH}/{endpoint_id}/deliveries")
    assert log.status_code == 200
    rows = log.json()["data"]
    assert [r["delivery_id"] for r in rows] == [delivery_id]
    assert rows[0]["status"] == "pending"
    assert rows[0]["attempt_count"] == 0


async def test_resend_requires_write_and_404s_when_unknown(
    writer: AsyncClient, reader: AsyncClient, clean_webhooks: None
) -> None:
    assert (await reader.post("/webhooks/deliveries/whdl_x:resend")).status_code == 403
    assert (await writer.post("/webhooks/deliveries/whdl_x:resend")).status_code == 404


async def test_resend_a_real_delivery(writer: AsyncClient, clean_webhooks: None) -> None:
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]
    queued = await writer.post(f"{ENDPOINTS_PATH}/{endpoint_id}:test")
    delivery_id = queued.json()["delivery_id"]

    response = await writer.post(f"/webhooks/deliveries/{delivery_id}:resend")
    assert response.status_code == 202, response.text


async def test_deliveries_for_unknown_endpoint_is_404(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    response = await writer.get(f"{ENDPOINTS_PATH}/whep_missing/deliveries")
    assert response.status_code == 404
