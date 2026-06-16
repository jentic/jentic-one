"""OAuth broker sync — credential-rejection visibility.

`discover_accounts` swallows a bad token / upstream 401 and returns 0 so the
best-effort callers (startup seeding, the post-OAuth callback) aren't broken by
a transient blip. But a count of 0 is indistinguishable from a valid broker
that simply has nothing connected yet, so the user-initiated sync passes
`raise_on_auth_error=True` to make a genuine credential rejection surface as a
401 instead of a misleading "0 accounts synced" success.

These tests pin both halves: the unit-level re-raise on the broker, and the
endpoint mapping a Pipedream 401 → HTTP 401.
"""

import httpx
import pytest
from src.brokers.pipedream import PipedreamOAuthBroker


BROKER_ID = "test-oauth-broker-sync-auth"

_BASE_CONFIG = {
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "project_id": "proj_test123",
}


def _make_broker() -> PipedreamOAuthBroker:
    return PipedreamOAuthBroker(
        broker_id=BROKER_ID,
        client_id="test-client-id",
        client_secret="test-client-secret",
        project_id="proj_test123",
        environment="production",
        default_external_user_id="default",
    )


@pytest.mark.asyncio
async def test_discover_accounts_swallows_token_error_by_default(monkeypatch):
    """Default behaviour: a token failure is swallowed and reported as 0."""
    broker = _make_broker()

    async def boom(self) -> str:
        raise ValueError("bad client secret")

    monkeypatch.setattr(PipedreamOAuthBroker, "_get_access_token", boom)

    assert await broker.discover_accounts("default") == 0


@pytest.mark.asyncio
async def test_discover_accounts_reraises_token_error_when_asked(monkeypatch):
    """With raise_on_auth_error=True, the token failure propagates."""
    broker = _make_broker()

    async def boom(self) -> str:
        raise ValueError("bad client secret")

    monkeypatch.setattr(PipedreamOAuthBroker, "_get_access_token", boom)

    with pytest.raises(ValueError, match="bad client secret"):
        await broker.discover_accounts("default", raise_on_auth_error=True)


@pytest.mark.asyncio
async def test_discover_accounts_reraises_upstream_401_when_asked(monkeypatch):
    """A Pipedream 401 on /accounts re-raises as HTTPStatusError when asked."""
    broker = _make_broker()

    async def fake_token(self) -> str:
        return "pd_token"

    monkeypatch.setattr(PipedreamOAuthBroker, "_get_access_token", fake_token)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    # Patch the client used inside discover_accounts to a mock transport.
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    with pytest.raises(httpx.HTTPStatusError):
        await broker.discover_accounts("default", raise_on_auth_error=True)

    # And without the flag, the same 401 is swallowed to 0.
    assert await broker.discover_accounts("default") == 0


def test_sync_endpoint_maps_pipedream_401_to_401(admin_client, monkeypatch):
    """POST /sync surfaces a credential rejection as HTTP 401, not a 200/0."""
    create = admin_client.post(
        "/oauth-brokers",
        json={"id": BROKER_ID, "type": "pipedream", "config": _BASE_CONFIG},
    )
    assert create.status_code in (200, 201), f"Create failed: {create.text}"

    try:

        async def reject(self, external_user_id: str, raise_on_auth_error: bool = False) -> int:
            if raise_on_auth_error:
                raise httpx.HTTPStatusError(
                    "401 Unauthorized",
                    request=httpx.Request("GET", "https://api.pipedream.com"),
                    response=httpx.Response(401),
                )
            return 0

        monkeypatch.setattr(PipedreamOAuthBroker, "discover_accounts", reject)

        resp = admin_client.post(f"/oauth-brokers/{BROKER_ID}/sync", json={})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert "rejected" in resp.json()["detail"].lower()
    finally:
        admin_client.delete(f"/oauth-brokers/{BROKER_ID}")
