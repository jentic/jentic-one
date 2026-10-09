"""Self-tests for the fake OAuth authorization server harness."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from tests.harness.fake_oauth_as.app import DEVICE_GRANT, build_fake_as_app

_REDIRECT = "https://app.example/callback"


@pytest.fixture
async def fake_as() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=build_fake_as_app())
    async with AsyncClient(transport=transport, base_url="https://as.local") as client:
        yield client


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def _authorize(client: AsyncClient, **params: str) -> dict[str, list[str]]:
    query = {"client_id": "c1", "redirect_uri": _REDIRECT, "state": "st", **params}
    resp = await client.get("/authorize", params=query)
    assert resp.status_code == 302
    return parse_qs(urlsplit(resp.headers["location"]).query)


async def test_authorization_code_with_pkce_issues_the_granted_scopes(
    fake_as: AsyncClient,
) -> None:
    verifier = "v" * 64
    q = await _authorize(
        fake_as,
        scope="read write",
        drop_scopes="write",
        code_challenge=_challenge(verifier),
        code_challenge_method="S256",
    )
    assert q["state"] == ["st"]
    form = {
        "grant_type": "authorization_code",
        "code": q["code"][0],
        "client_id": "c1",
        "client_secret": "s",  # pragma: allowlist secret
        "redirect_uri": _REDIRECT,
        "code_verifier": verifier,
    }
    resp = await fake_as.post("/token", data=form)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"].startswith("fake-at-")
    assert body["scope"] == "read"
    # The code is single-use.
    assert (await fake_as.post("/token", data=form)).status_code == 400


async def test_authorization_code_rejects_a_wrong_verifier(fake_as: AsyncClient) -> None:
    q = await _authorize(fake_as, code_challenge=_challenge("right"), code_challenge_method="S256")
    resp = await fake_as.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "client_id": "c1",
            "redirect_uri": _REDIRECT,
            "code_verifier": "wrong",
        },
    )
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_grant"}


async def test_deny_redirects_with_access_denied(fake_as: AsyncClient) -> None:
    q = await _authorize(fake_as, deny="1")
    assert q["error"] == ["access_denied"]
    assert "code" not in q


async def test_registered_clients_are_enforced(fake_as: AsyncClient) -> None:
    await fake_as.post("/clients", json={"client_id": "known", "client_secret": "s"})
    resp = await fake_as.get("/authorize", params={"client_id": "c1", "redirect_uri": _REDIRECT})
    assert resp.status_code == 401
    q = await _authorize(fake_as, client_id="known")
    resp = await fake_as.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "client_id": "known",
            "client_secret": "wrong",  # pragma: allowlist secret
            "redirect_uri": _REDIRECT,
        },
    )
    assert resp.status_code == 401


async def test_device_flow_pends_until_approved_then_refreshes(fake_as: AsyncClient) -> None:
    dev = (await fake_as.post("/device_authorization", data={"client_id": "c1"})).json()
    poll = {"grant_type": DEVICE_GRANT, "device_code": dev["device_code"], "client_id": "c1"}
    pending = await fake_as.post("/token", data=poll)
    assert pending.json() == {"error": "authorization_pending"}
    await fake_as.post("/device/approve", json={"user_code": dev["user_code"]})
    tokens = (await fake_as.post("/token", data=poll)).json()
    assert tokens["access_token"].startswith("fake-at-")
    refreshed = await fake_as.post(
        "/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
