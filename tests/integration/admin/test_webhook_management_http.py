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


async def test_updating_an_endpoint_requires_webhooks_write(
    writer: AsyncClient, reader: AsyncClient, clean_webhooks: None
) -> None:
    """Editing an endpoint is a mutation, so read access must not reach it."""
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]
    response = await reader.patch(f"{ENDPOINTS_PATH}/{endpoint_id}", json={"name": "nope"})
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


# --- update ------------------------------------------------------------------


async def test_update_changes_fields_and_returns_no_secret(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    """A happy-path edit returns the new shape and never any secret material."""
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]

    response = await writer.patch(
        f"{ENDPOINTS_PATH}/{endpoint_id}",
        json={
            "name": "renamed-ops",
            "target_url": "https://receiver.test/new-hook",
            "event_types": ["execution.failed"],
            "active": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "renamed-ops"
    assert body["target_url"] == "https://receiver.test/new-hook"
    assert body["event_types"] == ["execution.failed"]
    assert body["active"] is False

    # The update response is a plain endpoint projection — no secret in any form.
    assert created["secret"] not in response.text
    assert "secret_encrypted" not in response.text
    assert "secret_hash" not in response.text
    assert "secret" not in body


async def test_update_rejects_a_bad_target_url(writer: AsyncClient, clean_webhooks: None) -> None:
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]

    response = await writer.patch(
        f"{ENDPOINTS_PATH}/{endpoint_id}",
        json={"target_url": "file:///etc/passwd"},
    )
    assert response.status_code == 400, response.text


async def test_update_unknown_endpoint_is_404(writer: AsyncClient, clean_webhooks: None) -> None:
    response = await writer.patch(f"{ENDPOINTS_PATH}/whep_missing", json={"name": "ghost"})
    assert response.status_code == 404, response.text


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


# --- allowed_cidrs round-trip (Phase 3) --------------------------------------


async def test_create_and_read_allowed_cidrs(writer: AsyncClient, clean_webhooks: None) -> None:
    """The per-endpoint allowlist round-trips through create + read, canonicalised."""
    response = await writer.post(
        ENDPOINTS_PATH,
        json={
            "name": "cidr-endpoint",
            "target_url": "https://receiver.test/hook",
            "allowed_cidrs": ["10.0.0.5", "10.0.0.0/8"],
        },
    )
    assert response.status_code == 201, response.text
    endpoint_id = response.json()["endpoint"]["endpoint_id"]
    assert response.json()["endpoint"]["allowed_cidrs"] == ["10.0.0.5/32", "10.0.0.0/8"]

    fetched = await writer.get(f"{ENDPOINTS_PATH}/{endpoint_id}")
    assert fetched.json()["allowed_cidrs"] == ["10.0.0.5/32", "10.0.0.0/8"]


async def test_create_rejects_malformed_allowed_cidr(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    response = await writer.post(
        ENDPOINTS_PATH,
        json={
            "name": "cidr-bad",
            "target_url": "https://receiver.test/hook",
            "allowed_cidrs": ["nonsense"],
        },
    )
    assert response.status_code == 400, response.text


async def test_update_replaces_allowed_cidrs_over_http(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]

    updated = await writer.patch(
        f"{ENDPOINTS_PATH}/{endpoint_id}", json={"allowed_cidrs": ["192.168.0.0/16"]}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["allowed_cidrs"] == ["192.168.0.0/16"]


# --- aggregate stats / per-attempt history / event catalog (Phase 3) ---------


async def test_stats_endpoint_returns_aggregate_shape(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]
    await writer.post(f"{ENDPOINTS_PATH}/{endpoint_id}:test")

    response = await writer.get(f"{ENDPOINTS_PATH}/{endpoint_id}/stats")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["counts_by_status"]["pending"] == 1


async def test_stats_endpoint_requires_read(reader: AsyncClient, writer: AsyncClient) -> None:
    # A reader *can* read stats; an unauthenticated request cannot (covered
    # elsewhere). Here we simply assert the read scope is sufficient.
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]
    response = await reader.get(f"{ENDPOINTS_PATH}/{endpoint_id}/stats")
    assert response.status_code == 200


async def test_attempts_endpoint_lists_per_attempt_history(
    writer: AsyncClient, clean_webhooks: None
) -> None:
    created = await _create_notification(writer)
    endpoint_id = created["endpoint"]["endpoint_id"]
    queued = await writer.post(f"{ENDPOINTS_PATH}/{endpoint_id}:test")
    delivery_id = queued.json()["delivery_id"]

    # No sends have run in this test, so the history is empty but well-formed.
    response = await writer.get(f"/webhooks/deliveries/{delivery_id}/attempts")
    assert response.status_code == 200, response.text
    assert response.json()["data"] == []


async def test_event_catalog_endpoint_lists_subscribable_types(
    reader: AsyncClient, clean_webhooks: None
) -> None:
    """The catalog the picker consumes is served by the backend (no drift)."""
    response = await reader.get("/webhooks/event-catalog")
    assert response.status_code == 200, response.text
    entries = response.json()["data"]
    types = {e["event_type"] for e in entries}
    # A representative subscribable type is present; the synthetic + never-relayed
    # ones are not.
    assert "credential.expired" in types
    assert "webhook.test" not in types
    # Each entry carries its noun grouping (before the first dot).
    sample = next(e for e in entries if e["event_type"] == "credential.expired")
    assert sample["noun"] == "credential"
