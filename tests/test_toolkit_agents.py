"""Tests for GET /toolkits/{id}/agents — the reverse agent↔toolkit lookup
that powers the "Bound Agents" layer in the toolkit detail view."""

import sqlite3

from src.db import DB_PATH


_AGENT_A = "agnt_tk_agents_a"
_AGENT_B = "agnt_tk_agents_b"
_AGENT_DENIED = "agnt_tk_agents_denied"


def _seed_agent(client_id: str, name: str, status: str = "approved") -> None:
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT OR REPLACE INTO agents (client_id, client_name, jwks_json, status, created_at)
               VALUES (?, ?, '{}', ?, strftime('%s','now'))""",
            (client_id, name, status),
        )
        cx.commit()


def _grant(client_id: str, toolkit_id: str) -> None:
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT OR IGNORE INTO agent_toolkit_grants (client_id, toolkit_id, granted_at)
               VALUES (?, ?, strftime('%s','now'))""",
            (client_id, toolkit_id),
        )
        cx.commit()


def _cleanup(toolkit_id: str) -> None:
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            "DELETE FROM agent_toolkit_grants WHERE client_id IN (?,?,?)",
            (_AGENT_A, _AGENT_B, _AGENT_DENIED),
        )
        cx.execute(
            "DELETE FROM agents WHERE client_id IN (?,?,?)",
            (_AGENT_A, _AGENT_B, _AGENT_DENIED),
        )
        cx.execute("DELETE FROM toolkits WHERE id=?", (toolkit_id,))
        cx.commit()


def test_list_toolkit_agents_returns_granted_active_agents(admin_client):
    """Returns active agents holding a grant, ordered by grant time, and
    excludes denied/deregistered agents."""
    resp = admin_client.post("/toolkits", json={"name": "Agents TK"})
    assert resp.status_code in (200, 201), resp.text
    toolkit_id = resp.json()["id"]

    try:
        _seed_agent(_AGENT_A, "Alice")
        _seed_agent(_AGENT_B, "Bob")
        _seed_agent(_AGENT_DENIED, "Denied", status="denied")
        _grant(_AGENT_A, toolkit_id)
        _grant(_AGENT_B, toolkit_id)
        _grant(_AGENT_DENIED, toolkit_id)

        r = admin_client.get(f"/toolkits/{toolkit_id}/agents")
        assert r.status_code == 200, r.text
        agents = r.json()["agents"]
        ids = [a["client_id"] for a in agents]

        assert _AGENT_A in ids
        assert _AGENT_B in ids
        # Denied agents are not surfaced.
        assert _AGENT_DENIED not in ids
        # Shape carries display fields the UI card needs.
        alice = next(a for a in agents if a["client_id"] == _AGENT_A)
        assert alice["client_name"] == "Alice"
        assert alice["status"] == "approved"
    finally:
        _cleanup(toolkit_id)


def test_list_toolkit_agents_excludes_soft_deleted(admin_client):
    """An agent with a non-null `deleted_at` is deregistered and must not appear,
    even though its grant row still exists."""
    resp = admin_client.post("/toolkits", json={"name": "Soft Del TK"})
    toolkit_id = resp.json()["id"]
    try:
        _seed_agent(_AGENT_A, "Alive")
        _seed_agent(_AGENT_B, "Deleted")
        _grant(_AGENT_A, toolkit_id)
        _grant(_AGENT_B, toolkit_id)
        # Soft-delete agent B after granting.
        with sqlite3.connect(DB_PATH) as cx:
            cx.execute(
                "UPDATE agents SET deleted_at=strftime('%s','now') WHERE client_id=?",
                (_AGENT_B,),
            )
            cx.commit()

        r = admin_client.get(f"/toolkits/{toolkit_id}/agents")
        assert r.status_code == 200, r.text
        ids = [a["client_id"] for a in r.json()["agents"]]
        assert _AGENT_A in ids
        assert _AGENT_B not in ids
    finally:
        _cleanup(toolkit_id)


def test_list_toolkit_agents_includes_disabled_status(admin_client):
    """A `disabled`-status agent still holds its grant and must be returned so the
    UI can render it (only `denied`/soft-deleted are filtered out). This protects
    the boundary against an over-broad status filter regressing the endpoint."""
    resp = admin_client.post("/toolkits", json={"name": "Disabled Status TK"})
    toolkit_id = resp.json()["id"]
    try:
        _seed_agent(_AGENT_A, "Disabled One", status="disabled")
        _grant(_AGENT_A, toolkit_id)

        r = admin_client.get(f"/toolkits/{toolkit_id}/agents")
        assert r.status_code == 200, r.text
        agents = r.json()["agents"]
        ids = [a["client_id"] for a in agents]
        assert _AGENT_A in ids
        row = next(a for a in agents if a["client_id"] == _AGENT_A)
        assert row["status"] == "disabled"
    finally:
        _cleanup(toolkit_id)


def test_list_toolkit_agents_empty_when_no_grants(admin_client):
    resp = admin_client.post("/toolkits", json={"name": "No Agents TK"})
    toolkit_id = resp.json()["id"]
    try:
        r = admin_client.get(f"/toolkits/{toolkit_id}/agents")
        assert r.status_code == 200, r.text
        assert r.json()["agents"] == []
    finally:
        _cleanup(toolkit_id)


def test_list_toolkit_agents_404_for_unknown_toolkit(admin_client):
    r = admin_client.get("/toolkits/does-not-exist-xyz/agents")
    assert r.status_code == 404


def test_list_toolkit_agents_requires_admin_session(client):
    """Unauthenticated callers are rejected (admin-only route)."""
    r = client.get("/toolkits/default/agents")
    assert r.status_code in (401, 403)
