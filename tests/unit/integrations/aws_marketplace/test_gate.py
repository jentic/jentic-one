"""Unit tests for the entitlement gate middleware (``gate.py``).

Drives the verdict matrix through a minimal FastAPI app: entitled ⇒ 200,
locked ⇒ 503 problem details, probes answer 200 with the ``not_entitled``
body, ``/instance`` is exempt, and recovery unlocks without rebuild.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.integrations.aws_marketplace.gate import EntitlementGate, EntitlementMiddleware


@pytest.fixture()
def gate() -> EntitlementGate:
    return EntitlementGate()


@pytest.fixture()
def client(gate: EntitlementGate) -> TestClient:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/admin/health")
    async def admin_health() -> dict[str, str]:
        return {"status": "ok", "surface": "admin"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/instance")
    async def instance() -> dict[str, str]:
        return {"backend": "local"}

    @app.get("/agents")
    async def agents() -> list[str]:
        return []

    app.add_middleware(EntitlementMiddleware, gate=gate)
    return TestClient(app)


def test_unlocked_passes_everything(client: TestClient) -> None:
    assert client.get("/agents").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_locked_returns_503_problem_details(gate: EntitlementGate, client: TestClient) -> None:
    gate.lock("subscription expired")

    response = client.get("/agents")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "https://jentic.com/problems/not-entitled"
    assert body["title"] == "Not entitled"
    assert body["status"] == 503
    assert body["detail"] == "subscription expired"


def test_locked_health_stays_200_with_reason(gate: EntitlementGate, client: TestClient) -> None:
    gate.lock("subscription expired")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "not_entitled", "reason": "subscription expired"}


def test_locked_surface_health_stays_200(gate: EntitlementGate, client: TestClient) -> None:
    gate.lock("subscription expired")

    response = client.get("/admin/health")

    assert response.status_code == 200
    assert response.json()["status"] == "not_entitled"


def test_locked_readiness_stays_200(gate: EntitlementGate, client: TestClient) -> None:
    gate.lock("subscription expired")

    assert client.get("/ready").status_code == 200


def test_locked_instance_passes_through(gate: EntitlementGate, client: TestClient) -> None:
    gate.lock("subscription expired")

    response = client.get("/instance")

    assert response.status_code == 200
    assert response.json() == {"backend": "local"}


def test_health_lookalikes_are_not_exempt(gate: EntitlementGate, client: TestClient) -> None:
    """Only the probe surface is exempt — not any path containing 'health'."""
    gate.lock("subscription expired")

    assert client.get("/agents/health/extra").status_code == 503
    assert client.get("/a/b/health").status_code == 503


def test_recovery_unlocks_without_rebuild(gate: EntitlementGate, client: TestClient) -> None:
    gate.lock("subscription expired")
    assert client.get("/agents").status_code == 503

    gate.unlock()

    assert client.get("/agents").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
