"""Test-only fake OAuth 2.0 authorization server.

A minimal, DB-less authorization server for driving the platform's OAuth
connect flows end to end against a loopback upstream:

- ``GET /authorize`` — the authorization-code front channel. Consent is
  automatic: it redirects straight back to ``redirect_uri`` with a fresh
  ``code`` and the caller's ``state``. ``?deny=1`` redirects with
  ``error=access_denied`` instead. The granted scope is the requested
  ``scope`` minus anything listed in ``?drop_scopes=`` (comma-separated), so
  a test can make the vendor grant less than was asked for.
- ``POST /device_authorization`` — RFC 8628 device authorization.
- ``POST /device/approve`` — marks a ``user_code`` approved (the "user typed
  the code" step).
- ``POST /token`` — ``authorization_code`` (PKCE S256 checked when a
  challenge was sent), ``urn:ietf:params:oauth:grant-type:device_code``,
  ``refresh_token`` and ``client_credentials``.
- ``GET /clients`` / ``POST /clients`` — register the ``client_id`` /
  ``client_secret`` pairs the token endpoint accepts. An unregistered
  client is refused with ``invalid_client``; with no clients registered at
  all, any client is accepted.
- ``GET /requests`` — every authorize/token request seen, for assertions.

State is in memory, per process. Issued access tokens look like
``fake-at-<n>``; the smoke upstream only checks for a ``Bearer`` prefix.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, RedirectResponse

DEVICE_GRANT: Final = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_TTL_SECONDS: Final = 3600


@dataclass
class _Code:
    client_id: str
    redirect_uri: str
    scope: str
    code_challenge: str | None


@dataclass
class _Device:
    client_id: str
    scope: str
    user_code: str
    approved: bool = False
    denied: bool = False


@dataclass
class _State:
    clients: dict[str, str] = field(default_factory=dict)
    codes: dict[str, _Code] = field(default_factory=dict)
    devices: dict[str, _Device] = field(default_factory=dict)
    refresh_tokens: dict[str, str] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    counter: int = 0

    def next_token(self) -> str:
        self.counter += 1
        return f"fake-at-{self.counter}"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


def build_fake_as_app() -> FastAPI:
    app = FastAPI(title="Fake OAuth AS")
    state = _State()

    def _client_ok(client_id: str, client_secret: str | None, *, public: bool) -> bool:
        if not state.clients:
            return True
        if client_id not in state.clients:
            return False
        return public or state.clients[client_id] == (client_secret or "")

    def _issue(scope: str, client_id: str) -> dict[str, Any]:
        access = state.next_token()
        refresh = f"fake-rt-{secrets.token_urlsafe(8)}"
        state.refresh_tokens[refresh] = scope
        body: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SECONDS,
            "refresh_token": refresh,
        }
        if scope:
            body["scope"] = scope
        return body

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/clients")
    async def add_client(request: Request) -> dict[str, str]:
        body = await request.json()
        state.clients[str(body["client_id"])] = str(body.get("client_secret", ""))
        return {"client_id": str(body["client_id"])}

    @app.get("/clients")
    async def list_clients() -> dict[str, list[str]]:
        return {"client_ids": sorted(state.clients)}

    @app.get("/requests")
    async def list_requests() -> dict[str, list[dict[str, Any]]]:
        return {"requests": state.requests}

    @app.get("/authorize", response_model=None)
    async def authorize(request: Request) -> RedirectResponse | JSONResponse:
        q = request.query_params
        state.requests.append({"endpoint": "authorize", **dict(q)})
        redirect_uri = q.get("redirect_uri")
        client_id = q.get("client_id", "")
        if not redirect_uri:
            return _error("invalid_request")
        if state.clients and client_id not in state.clients:
            return _error("invalid_client", 401)
        params: dict[str, str] = {}
        if q.get("state") is not None:
            params["state"] = q["state"]
        if q.get("deny") == "1":
            params["error"] = "access_denied"
        else:
            dropped = {s for s in q.get("drop_scopes", "").split(",") if s}
            scope = " ".join(s for s in q.get("scope", "").split() if s not in dropped)
            code = secrets.token_urlsafe(16)
            state.codes[code] = _Code(
                client_id=client_id,
                redirect_uri=redirect_uri,
                scope=scope,
                code_challenge=q.get("code_challenge"),
            )
            params["code"] = code
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

    @app.post("/device_authorization")
    async def device_authorization(request: Request) -> dict[str, Any]:
        form = await request.form()
        client_id = str(form.get("client_id", ""))
        scope = str(form.get("scope", ""))
        state.requests.append({"endpoint": "device_authorization", "client_id": client_id})
        device_code = secrets.token_urlsafe(16)
        user_code = f"{secrets.randbelow(10**4):04d}-{secrets.randbelow(10**4):04d}"
        state.devices[device_code] = _Device(client_id=client_id, scope=scope, user_code=user_code)
        base = str(request.base_url).rstrip("/")
        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": f"{base}/device",
            "verification_uri_complete": f"{base}/device?user_code={user_code}",
            "expires_in": 600,
            "interval": 1,
        }

    @app.post("/device/approve")
    async def device_approve(request: Request) -> JSONResponse:
        body = await request.json()
        user_code = str(body.get("user_code", ""))
        deny = bool(body.get("deny", False))
        for device in state.devices.values():
            if device.user_code == user_code:
                device.approved = not deny
                device.denied = deny
                return JSONResponse({"ok": True})
        return _error("unknown_user_code", 404)

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        grant = str(form.get("grant_type", ""))
        client_id = str(form.get("client_id", ""))
        client_secret = form.get("client_secret")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            raw = base64.b64decode(auth[6:]).decode()
            client_id, _, basic_secret = raw.partition(":")
            client_secret = basic_secret
        state.requests.append({"endpoint": "token", "grant_type": grant, "client_id": client_id})

        if grant == "authorization_code":
            if not _client_ok(client_id, str(client_secret or ""), public=False):
                return _error("invalid_client", 401)
            code = state.codes.pop(str(form.get("code", "")), None)
            if code is None or code.client_id != client_id:
                return _error("invalid_grant")
            if code.redirect_uri != str(form.get("redirect_uri", "")):
                return _error("invalid_grant")
            if code.code_challenge is not None:
                verifier = str(form.get("code_verifier", ""))
                if not verifier or _challenge(verifier) != code.code_challenge:
                    return _error("invalid_grant")
            return JSONResponse(_issue(code.scope, client_id))

        if grant == DEVICE_GRANT:
            if not _client_ok(client_id, None, public=True):
                return _error("invalid_client", 401)
            device = state.devices.get(str(form.get("device_code", "")))
            if device is None:
                return _error("expired_token")
            if device.denied:
                return _error("access_denied")
            if not device.approved:
                return _error("authorization_pending")
            state.devices.pop(str(form.get("device_code", "")))
            return JSONResponse(_issue(device.scope, client_id))

        if grant == "refresh_token":
            scope = state.refresh_tokens.get(str(form.get("refresh_token", "")))
            if scope is None:
                return _error("invalid_grant")
            return JSONResponse(_issue(scope, client_id))

        if grant == "client_credentials":
            if not _client_ok(client_id, str(client_secret or ""), public=False):
                return _error("invalid_client", 401)
            return JSONResponse(_issue(str(form.get("scope", "")), client_id))

        return _error("unsupported_grant_type")

    app.state.fake_as = state
    app.state.started_at = time.time()
    return app
