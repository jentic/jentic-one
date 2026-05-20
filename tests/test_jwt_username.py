"""
JWT username claim tests.

Verifies that the username is embedded in the JWT at login time and correctly
surfaced on GET /user/me for both cookie and bearer sessions.  Also verifies
the backward-compatibility fallback: legacy JWTs issued without a username
claim still return the correct username via the DB query in /user/me.
"""

import sqlite3
import time

import jwt
from src.auth import JWT_ALGORITHM, JWT_TTL_SECONDS
from src.db import DB_PATH
from starlette.testclient import TestClient


def _jwt_secret() -> str:
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute("SELECT value FROM settings WHERE key = 'jwt_secret'")
        row = cur.fetchone()
    assert row, "jwt_secret not in settings — DB not initialised"
    return row[0]


def _legacy_jwt(secret: str) -> str:
    """Craft a JWT that has no username claim (simulates pre-fix tokens)."""
    now = int(time.time())
    payload = {"sub": "human", "iat": now, "exp": now + JWT_TTL_SECONDS}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


# ── Cookie session ────────────────────────────────────────────────────────────


def test_cookie_session_me_returns_correct_username(admin_client):
    resp = admin_client.get("/user/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["logged_in"] is True
    assert body["username"] == "testadmin"


# ── Bearer JWT (/user/token) ──────────────────────────────────────────────────


def test_bearer_jwt_me_returns_correct_username(client, admin_client):
    resp = client.post(
        "/user/token",
        data={"username": "testadmin", "password": "testpassword123", "grant_type": "password"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    resp = client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["logged_in"] is True
    assert body["username"] == "testadmin"


# ── Legacy JWT backward compatibility ────────────────────────────────────────


def test_legacy_jwt_me_falls_back_to_db(app, admin_client):
    """JWTs without a username claim still return the correct username via DB fallback."""
    legacy_token = _legacy_jwt(_jwt_secret())

    with TestClient(app, raise_server_exceptions=False) as c:
        c.cookies.set("jentic_session", legacy_token)
        resp = c.get("/user/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logged_in"] is True
    assert body["username"] == "testadmin"
