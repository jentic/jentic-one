"""Control web tests for the credential PATCH contract (#739, #589).

Exercises the real HTTP path (router → service → DB) to pin two invariants:

- ``updated_at`` moves iff a change was persisted (#739) — a no-op PATCH must
  leave it frozen.
- The api_key ``field_name``/``location`` binding is immutable after create
  (#589) — a PATCH that changes it returns 409 ``immutable_field`` and never
  leaks secret material.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


def _parse_ts(value: str) -> datetime:
    """Parse an API ``updated_at`` timestamp for order-safe comparison.

    Comparing the raw ISO strings works today but breaks silently if the
    serialization format or timezone suffix ever changes; parse instead.
    """
    return datetime.fromisoformat(value)


def _create_api_key(client: TestClient) -> str:
    resp = client.post(
        "/credentials",
        json={
            "type": "api_key",
            "name": "web-cred-589",
            "api": {"vendor": "openweathermap.org", "name": "onecall", "version": "3.0"},
            "provider": "static",
            "key": "sk-web-test-key-123",
            "location": "query",
            "field_name": "appid",
        },
    )
    assert resp.status_code == 201, resp.text
    credential_id: str = resp.json()["credential"]["credential_id"]
    return credential_id


def test_patch_changing_field_name_is_rejected(cred_writer_client: TestClient) -> None:
    """A PATCH that changes the api_key field name returns 409 immutable_field."""
    cred_id = _create_api_key(cred_writer_client)

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "field_name": "Default"},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["type"] == "immutable_field"
    # No secret / key material may leak into the error body (redaction rule).
    assert "sk-web-test-key-123" not in resp.text

    # The stored binding is unchanged.
    after = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert after["details"]["field_name"] == "appid"


def test_patch_noop_does_not_move_updated_at(cred_writer_client: TestClient) -> None:
    """A PATCH that echoes the stored binding and nothing else keeps updated_at frozen."""
    cred_id = _create_api_key(cred_writer_client)
    before = cred_writer_client.get(f"/credentials/{cred_id}").json()

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "field_name": "appid", "location": "query"},
    )
    assert resp.status_code == 200, resp.text
    assert _parse_ts(resp.json()["updated_at"]) == _parse_ts(before["updated_at"])


def test_patch_key_rotation_moves_updated_at(cred_writer_client: TestClient) -> None:
    """Rotating the secret persists a change, so updated_at advances (#739)."""
    cred_id = _create_api_key(cred_writer_client)
    before = cred_writer_client.get(f"/credentials/{cred_id}").json()

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "key": "sk-web-test-key-rotated"},
    )
    assert resp.status_code == 200, resp.text
    assert _parse_ts(resp.json()["updated_at"]) > _parse_ts(before["updated_at"])


# --- OAuth connect state on the redacted projection (#890) ---


def _create_oauth2(client: TestClient, *, grant_type: str, name: str) -> str:
    payload: dict[str, object] = {
        "type": "oauth2",
        "name": name,
        "api": {"vendor": "webtest-oauth.example", "name": "", "version": ""},
        "provider": "static",
        "grant_type": grant_type,
        "token_url": "https://auth.example/token",
        "client_id": "webtest-client",
        "client_secret": "webtest-secret",
    }
    if grant_type == "authorization_code":
        payload["authorize_url"] = "https://auth.example/authorize"
    resp = client.post("/credentials", json=payload)
    assert resp.status_code == 201, resp.text
    credential_id: str = resp.json()["credential"]["credential_id"]
    return credential_id


async def test_authorization_code_connected_flips_on_token(
    cred_writer_client: TestClient, web_context: Context
) -> None:
    """An authorization_code credential reports connected=False until its
    sign-in lands a usable token row, then True — and back to False when the
    token is revoked or can no longer mint (expired with no refresh token).
    Lets list consumers (the fulfilment wizard's adopt picker) warn about a
    never-connected pick before it fails at execute time (#890). grant_type
    is the honest stored grant, not the historical client_credentials
    hardcode.
    """
    cred_id = _create_oauth2(
        cred_writer_client, grant_type="authorization_code", name="web-cred-connect"
    )

    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["grant_type"] == "authorization_code"
    assert got["details"]["connected"] is False

    listed = cred_writer_client.get("/credentials", params={"vendor": "webtest-oauth-example"})
    rows = [c for c in listed.json()["data"] if c["credential_id"] == cred_id]
    # The vendor filter matches the slugified stored form of the dotted input.
    assert len(rows) == 1
    assert rows[0]["details"]["connected"] is False

    # A completed connect flow persists the token row (simulated directly —
    # the flow itself is exercised in tests/integration/control/test_connect_flow.py).
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO oauth_tokens (id, credential_id, encrypted_access_token) "
                "VALUES ('oat_webtest_conn', :cred, 'enc-webtest') ON CONFLICT DO NOTHING"
            ),
            {"cred": cred_id},
        )
        await session.commit()
    # No local cleanup: the credential-delete teardown cascades to oauth_tokens.

    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is True

    # Expired with no refresh token: the row exists but cannot mint again —
    # the sign-in must be redone, so the flag drops back to False.
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "UPDATE oauth_tokens SET expires_at = '2020-01-01T00:00:00Z' "
                "WHERE id = 'oat_webtest_conn'"
            )
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is False

    # A refresh token rescues an expired access token (the broker re-mints).
    async with web_context.control_db.session() as session:
        await session.execute(
            text(
                "UPDATE oauth_tokens SET encrypted_refresh_token = 'enc-refresh' "
                "WHERE id = 'oat_webtest_conn'"
            )
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is True

    # Revocation trumps everything.
    async with web_context.control_db.session() as session:
        await session.execute(
            text("UPDATE oauth_tokens SET revoked_at = now() WHERE id = 'oat_webtest_conn'")
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is False


async def test_managed_provider_account_ref_counts_as_connected(
    cred_writer_client: TestClient, web_context: Context
) -> None:
    """Managed providers (e.g. Pipedream) complete connect by stamping
    `provider_account_ref` without a local token row — the ref alone must
    read as connected, or the picker would warn forever on a working
    credential.
    """
    cred_id = _create_oauth2(
        cred_writer_client, grant_type="authorization_code", name="web-cred-managed"
    )
    async with web_context.control_db.session() as session:
        await session.execute(
            text("UPDATE credentials SET provider_account_ref = 'acc_webtest' WHERE id = :cred"),
            {"cred": cred_id},
        )
        await session.commit()
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["connected"] is True


def test_client_credentials_has_no_connect_state(cred_writer_client: TestClient) -> None:
    """client_credentials needs no interactive sign-in step, so connect state
    is meaningless there: the key is absent (None is dropped from the wire),
    never a scary false."""
    cred_id = _create_oauth2(
        cred_writer_client, grant_type="client_credentials", name="web-cred-cc"
    )
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["details"]["grant_type"] == "client_credentials"
    assert "connected" not in got["details"]


def test_catalog_api_id_round_trips_verbatim(cred_writer_client: TestClient) -> None:
    """A create that carries the catalog identity slug stores it verbatim and
    exposes it on the create echo, read, and list responses (#910).

    Verbatim matters: the slug's `domain/sub-api` shape is exactly what the
    vendor/name tuple loses to slugification, and the UI derives friendly
    titles from the separable form.
    """
    resp = cred_writer_client.post(
        "/credentials",
        json={
            "type": "api_key",
            "name": "web-cred-910",
            "api": {
                "vendor": "nytimes.com",
                "name": "article_search",
                "version": "",
                "catalog_api_id": "nytimes.com/article_search",
            },
            "provider": "static",
            "key": "sk-web-test-key-910",
            "location": "query",
            "field_name": "api-key",
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["credential"]
    assert created["catalog_api_id"] == "nytimes.com/article_search"

    got = cred_writer_client.get(f"/credentials/{created['credential_id']}").json()
    assert got["catalog_api_id"] == "nytimes.com/article_search"

    listed = cred_writer_client.get("/credentials").json()["data"]
    row = next(r for r in listed if r["credential_id"] == created["credential_id"])
    assert row["catalog_api_id"] == "nytimes.com/article_search"


def test_catalog_api_id_defaults_to_null(cred_writer_client: TestClient) -> None:
    """Creates that don't know the slug (older clients, manual imports) store
    and expose null — the UI falls back to the vendor/name tuple."""
    cred_id = _create_api_key(cred_writer_client)
    got = cred_writer_client.get(f"/credentials/{cred_id}").json()
    assert got["catalog_api_id"] is None


# --- Create-time unmatched-API advisory on the wire (#1020) ---


def test_create_unmatched_scope_returns_warnings(cred_writer_client: TestClient) -> None:
    """A scope covering no imported API still creates (201) but the response
    carries the advisory `warnings` list."""
    resp = cred_writer_client.post(
        "/credentials",
        json={
            "type": "bearer_token",
            "name": "web-cred-1020-unmatched",
            "api": {"vendor": "webtest-unmatched.example", "name": "nothing-here", "version": ""},
            "provider": "static",
            "token": "sk-web-warn-token",
        },
    )
    assert resp.status_code == 201, resp.text
    warnings = resp.json()["warnings"]
    assert isinstance(warnings, list) and warnings
    assert "matches no imported API" in warnings[0]
    assert "webtest-unmatched-example/nothing-here" in warnings[0]


async def test_create_matching_scope_returns_null_warnings(
    cred_writer_client: TestClient, web_context: Context
) -> None:
    """When the (canonicalized) scope covers an imported registry API the
    response `warnings` field is null."""
    async with web_context.registry_db.session() as session:
        api = await ApiRepository.upsert(
            session,
            vendor="webtest-match-example",
            name="onecall",
            version="3.0",
            created_by="usr_test",
        )
        api_id = api.id
        await session.commit()
    try:
        resp = cred_writer_client.post(
            "/credentials",
            json={
                "type": "bearer_token",
                "name": "web-cred-1020-matched",
                "api": {"vendor": "webtest-match.example", "name": "onecall", "version": "3.0"},
                "provider": "static",
                "token": "sk-web-match-token",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["warnings"] is None
    finally:
        async with web_context.registry_db.session() as session:
            await session.execute(delete(Api).where(Api.id == api_id))
            await session.commit()
