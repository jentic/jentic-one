"""Control web tests for the credential PATCH contract (#739, #589).

Exercises the real HTTP path (router → service → DB) to pin two invariants:

- ``updated_at`` moves iff a change was persisted (#739) — a no-op PATCH must
  leave it frozen.
- The api_key ``field_name``/``location`` binding is immutable after create
  (#589) — a PATCH that changes it returns 409 ``immutable_field`` and never
  leaks secret material.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _create_api_key(client: TestClient) -> dict[str, object]:
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
    data: dict[str, object] = resp.json()
    return data


def test_patch_changing_field_name_is_rejected(cred_writer_client: TestClient) -> None:
    """A PATCH that changes the api_key field name returns 409 immutable_field."""
    created = _create_api_key(cred_writer_client)
    cred_id = created["credential_id"]

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
    created = _create_api_key(cred_writer_client)
    cred_id = created["credential_id"]
    before = cred_writer_client.get(f"/credentials/{cred_id}").json()

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "field_name": "appid", "location": "query"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated_at"] == before["updated_at"]


def test_patch_key_rotation_moves_updated_at(cred_writer_client: TestClient) -> None:
    """Rotating the secret persists a change, so updated_at advances (#739)."""
    created = _create_api_key(cred_writer_client)
    cred_id = created["credential_id"]
    before = cred_writer_client.get(f"/credentials/{cred_id}").json()

    resp = cred_writer_client.patch(
        f"/credentials/{cred_id}",
        json={"type": "api_key", "key": "sk-web-test-key-rotated"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated_at"] > before["updated_at"]
