"""Broker request-side header hygiene.

The broker's `_HOP_BY_HOP` set defines which incoming headers are dropped
before the request is forwarded upstream. This test pins the security-
relevant entries so a careless refactor can't reopen a known leak.

Background — `Cookie`:
    The `jentic_session` cookie is the admin browser-session JWT. If a
    human session calls the broker (e.g. clicking "Try it" in the UI, or
    a test script that reuses an admin cookie jar), urllib/the browser
    will attach `Cookie: jentic_session=...` to the broker request. Without
    explicit stripping that JWT is forwarded verbatim to the upstream API,
    landing in the upstream's access logs *and* — for async dispatch —
    in the broker's own stored `jobs.result.body` (any admin can then read
    the JWT via GET /jobs/{id}). This test guards against that regression.

    See issue #56 (closed wontfix) for the response-side analogue. The
    response-side reasoning ("agent is the legitimate caller, headers are
    application data") does not apply here: the agent's `Cookie` header
    is intended for *Jentic*, never for the upstream.
"""

import asyncio
import json
import os

import aiosqlite
import pytest
import src.vault as vault


_HOST = "127.0.10.51"


@pytest.fixture(scope="module")
def hygiene_credential():
    """Stand up a no-auth credential for our test host — needed so the broker
    accepts the simulate call instead of returning 403 policy_denied."""

    async def setup():
        db_path = os.environ["DB_PATH"]
        async with aiosqlite.connect(db_path) as db:
            enc = vault.encrypt("")
            await db.execute(
                "INSERT OR IGNORE INTO credentials "
                "(id, label, env_var, encrypted_value, api_id, auth_type, scheme) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "hygiene-test",
                    "Hygiene Test",
                    "HYGIENE_TEST",
                    enc,
                    _HOST,
                    "none",
                    json.dumps({}),
                ),
            )
            await db.execute(
                "INSERT OR IGNORE INTO credential_routes (credential_id, host) VALUES (?, ?)",
                ("hygiene-test", _HOST),
            )
            await db.execute(
                "INSERT OR IGNORE INTO toolkit_credentials (toolkit_id, credential_id) "
                "VALUES ('default', ?)",
                ("hygiene-test",),
            )
            await db.commit()

    asyncio.run(setup())
    yield

    async def teardown():
        db_path = os.environ["DB_PATH"]
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM credential_routes WHERE credential_id='hygiene-test'")
            await db.execute("DELETE FROM toolkit_credentials WHERE credential_id='hygiene-test'")
            await db.execute("DELETE FROM credentials WHERE id='hygiene-test'")
            await db.commit()

    asyncio.run(teardown())


def test_broker_does_not_forward_cookie_header(client, agent_key_header, hygiene_credential):
    """Cookie request header is stripped before forwarding upstream.

    The broker's `_HOP_BY_HOP` set must include `cookie` so that an admin's
    `jentic_session` JWT (or any other session cookie) cannot leak to the
    upstream API or into stored job result bodies.

    We use `X-Jentic-Simulate: true` to avoid hitting a real upstream — the
    `would_send.headers` payload is the same set the broker would forward,
    filtered by the same `_HOP_BY_HOP` rule that the live forward path uses.
    """
    resp = client.get(
        f"/{_HOST}/api/echo",
        headers={
            **agent_key_header,
            "X-Jentic-Simulate": "true",
            "Cookie": "jentic_session=eyJfake.jwt.token; other=value",
        },
    )
    assert resp.status_code == 200, f"Simulate failed: {resp.text}"
    forwarded = {k.lower(): v for k, v in resp.json()["would_send"]["headers"].items()}
    assert "cookie" not in forwarded, (
        f"Cookie header was forwarded upstream — leaks Jentic session JWT. "
        f"Forwarded headers: {sorted(forwarded.keys())}"
    )


def test_broker_strips_jentic_control_headers_from_upstream(
    client, agent_key_header, hygiene_credential
):
    """Jentic-controlled headers (X-Jentic-API-Key, X-Jentic-Simulate, etc.)
    must never reach the upstream. Pins the existing behavior so a refactor
    of `_HOP_BY_HOP` can't quietly reopen leaks.
    """
    resp = client.get(
        f"/{_HOST}/api/echo",
        headers={
            **agent_key_header,
            "X-Jentic-Simulate": "true",
            "X-Jentic-Credential": "some-alias",
            "X-Jentic-Service": "some-service",
            "X-Jentic-Callback": "https://cb.example/cb",
            "X-Jentic-Parent-Trace": "trace-internal-should-not-leak",
        },
    )
    assert resp.status_code == 200, f"Simulate failed: {resp.text}"
    forwarded = {k.lower(): v for k, v in resp.json()["would_send"]["headers"].items()}
    sensitive = {
        "x-jentic-api-key",
        "x-jentic-simulate",
        "x-jentic-credential",
        "x-jentic-service",
        "x-jentic-parent-trace",
    }
    leaked = sensitive & set(forwarded)
    assert not leaked, (
        f"Jentic control headers leaked upstream: {sorted(leaked)}. "
        f"Forwarded headers: {sorted(forwarded.keys())}"
    )
