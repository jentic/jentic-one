"""Killswitch (toolkit.disabled) enforcement at the broker execution layer.

The kill switch is a TOOLKIT-level flag (``toolkits.disabled``), not a key-level
one. These tests pin the runtime contract that a suspended toolkit blocks
execution for BOTH authentication paths:

* toolkit API key (``X-Jentic-API-Key: tk_…``) — gated by the broker killswitch
  loop, returns ``toolkit_suspended``.
* agent identity (``Authorization: Bearer at_…``) — the auth layer filters
  disabled toolkits out of the usable grant set; the broker then reports
  ``toolkit_suspended`` (NOT the misleading ``policy_denied`` / "no grants")
  when *every* grant points at a killed toolkit, and lets the agent keep using
  any still-live grant.

Also asserts the denial is observable: the killswitch exit writes an execution
trace like every other broker exit point.
"""

from __future__ import annotations

import json
import time

import aiosqlite
import pytest
from src.agent_identity_util import hash_token, new_access_token
from src.db import DB_PATH


async def _seed_toolkit(toolkit_id: str, *, disabled: bool = False) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO toolkits (id, name, api_key, disabled) VALUES (?, ?, ?, ?)",
            (toolkit_id, toolkit_id, f"api_{toolkit_id}", 1 if disabled else 0),
        )
        await db.commit()


async def _set_disabled(toolkit_id: str, disabled: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE toolkits SET disabled=? WHERE id=?", (1 if disabled else 0, toolkit_id)
        )
        await db.commit()


async def _seed_approved_agent(client_id: str) -> str:
    """Insert an approved agent + a live access token. Returns the raw at_ token."""
    raw = new_access_token()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO agents (client_id, client_name, status, jwks_json, created_at)
               VALUES (?, ?, 'approved', ?, strftime('%s','now'))""",
            (client_id, f"test-{client_id}", json.dumps({"keys": []})),
        )
        await db.execute(
            """INSERT INTO agent_tokens (token_hash, client_id, token_type, expires_at)
               VALUES (?, ?, 'access', ?)""",
            (hash_token(raw), client_id, time.time() + 3600),
        )
        await db.commit()
    return raw


async def _grant(client_id: str, toolkit_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO agent_toolkit_grants (client_id, toolkit_id, granted_at, granted_by)
               VALUES (?, ?, strftime('%s','now'), 'test')""",
            (client_id, toolkit_id),
        )
        await db.commit()


async def _cleanup(client_id: str | None, *toolkit_ids: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if client_id:
            await db.execute("DELETE FROM agent_tokens WHERE client_id=?", (client_id,))
            await db.execute("DELETE FROM agent_toolkit_grants WHERE client_id=?", (client_id,))
            await db.execute("DELETE FROM agents WHERE client_id=?", (client_id,))
        for tid in toolkit_ids:
            if tid != "default":
                await db.execute("DELETE FROM toolkits WHERE id=?", (tid,))
        await db.commit()


async def _trace_for(trace_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status, http_status FROM executions WHERE id=?", (trace_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


# ── API-key path ──────────────────────────────────────────────────────────────


def test_killswitch_blocks_tk_key(admin_client, client):
    """A toolkit API key on a suspended toolkit gets 403 toolkit_suspended.

    Uses the cookieless ``client`` for the broker call — the admin_client's
    session cookie would otherwise win over the tk_ key in the auth middleware
    and resolve to the default toolkit.
    """
    tk = admin_client.post("/toolkits", json={"name": "ks-tk-test"})
    assert tk.status_code in (200, 201), tk.text
    toolkit_id = tk.json()["id"]

    key_resp = admin_client.post(f"/toolkits/{toolkit_id}/keys", json={"label": "ks"})
    assert key_resp.status_code in (200, 201), key_resp.text
    key = key_resp.json()["key"]

    patch = admin_client.patch(f"/toolkits/{toolkit_id}", json={"disabled": True})
    assert patch.status_code == 200, patch.text

    resp = client.get("/httpbin.org/get", headers={"X-Jentic-API-Key": key})
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["error"] == "toolkit_suspended", body
    assert body["toolkit_id"] == toolkit_id

    # Restoring access clears the block (the call now fails for a *different*
    # reason — no credential — proving the killswitch is no longer the gate).
    patch = admin_client.patch(f"/toolkits/{toolkit_id}", json={"disabled": False})
    assert patch.status_code == 200, patch.text
    resp2 = client.get("/httpbin.org/get", headers={"X-Jentic-API-Key": key})
    if resp2.status_code == 403:
        assert resp2.json().get("error") != "toolkit_suspended"


# ── Agent-identity path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_killswitch_blocks_agent_only_grant(client):
    """Agent whose ONLY grant is killed gets toolkit_suspended — not the
    misleading 'no toolkit grants' policy_denied — and a trace is written.
    """
    cid = "agnt_ks_only_aaaaaaaaaaaaaa"
    tid = "tk_ks_only"
    await _seed_toolkit(tid, disabled=False)
    token = await _seed_approved_agent(cid)
    await _grant(cid, tid)
    await _set_disabled(tid, True)

    try:
        resp = client.get("/httpbin.org/get", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"] == "toolkit_suspended", (
            f"agent with a single killed grant must report toolkit_suspended, got {body}"
        )
        assert body["toolkit_id"] == tid

        # The denial is observable in traces.
        exec_id = resp.headers.get("X-Jentic-Execution-Id")
        assert exec_id, "killswitch denial must return an execution id"
        trace = await _trace_for(exec_id)
        assert trace is not None, "killswitch denial must write an execution trace"
        assert trace["http_status"] == 403
        assert trace["status"] == "toolkit_suspended"
    finally:
        await _cleanup(cid, tid)


@pytest.mark.asyncio
async def test_killswitch_one_grant_killed_other_live(client):
    """Killing one of an agent's grants must not block the still-live one.

    The killed toolkit simply disappears from the usable grant set; the agent
    keeps routing through its remaining live grant.
    """
    cid = "agnt_ks_multi_aaaaaaaaaaaaa"
    tid_live = "tk_ks_live"
    tid_dead = "tk_ks_dead"
    await _seed_toolkit(tid_live, disabled=False)
    await _seed_toolkit(tid_dead, disabled=False)
    token = await _seed_approved_agent(cid)
    await _grant(cid, tid_live)
    await _grant(cid, tid_dead)
    await _set_disabled(tid_dead, True)

    try:
        resp = client.get("/httpbin.org/get", headers={"Authorization": f"Bearer {token}"})
        # Not blocked by the killswitch — the live grant is usable. The call
        # may still 403 for lack of a matching credential, but never with
        # toolkit_suspended.
        if resp.status_code == 403:
            assert resp.json().get("error") != "toolkit_suspended", (
                f"a live grant must not be blocked by another grant's killswitch: {resp.text}"
            )
    finally:
        await _cleanup(cid, tid_live, tid_dead)


@pytest.mark.asyncio
async def test_no_grants_still_reports_policy_denied(client):
    """An agent with genuinely zero grants still gets the 'no grants' message —
    the suspended-grant special case must not swallow the real no-grants path.
    """
    cid = "agnt_ks_nogrants_aaaaaaaaaaa"
    token = await _seed_approved_agent(cid)

    try:
        resp = client.get("/httpbin.org/get", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"] == "policy_denied", (
            f"an agent with no grants at all must report policy_denied, got {body}"
        )
    finally:
        await _cleanup(cid)
