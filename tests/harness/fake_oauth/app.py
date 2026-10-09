"""Fake OAuth authorization server + the API it protects.

Endpoints:

- ``GET /specs/oauth.json`` — an OpenAPI 3.1 document whose ``oauth2`` scheme
  (authorization code, scopes ``read`` / ``write`` / ``admin``) points at this
  server, and whose one operation (``GET /me``) needs that scheme.
- ``GET /authorize`` — auto-consents and redirects to ``redirect_uri`` with a
  code. The granted scope is the requested one unless ``POST /control/grant``
  set an override.
- ``POST /token`` — ``authorization_code``, ``refresh_token`` and
  ``urn:ietf:params:oauth:grant-type:device_code`` grants.
- ``POST /device/code`` and ``POST /control/device/approve`` — RFC 8628 device
  authorization, with the user's approval driven by the test.
- ``GET /me`` — the identity probe and the protected operation.
- ``POST /control/grant`` / ``POST /control/reset`` and ``GET /control/log`` —
  test controls and an inspection log of token requests.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

#: Env var that overrides the advertised base URL.
PUBLIC_URL_ENV: Final = "FAKE_OAUTH_PUBLIC_URL"
DEFAULT_PUBLIC_URL: Final = "http://127.0.0.1:8085"

SCOPES: Final = {
    "read": "Read the account",
    "write": "Change the account",
    "admin": "Administer the account",
}


@dataclass
class _State:
    codes: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    grant_override: str | None = None
    log: list[dict[str, Any]] = field(default_factory=list)


def _base_url() -> str:
    return os.environ.get(PUBLIC_URL_ENV, DEFAULT_PUBLIC_URL).rstrip("/")


def _spec() -> dict[str, Any]:
    base = _base_url()
    return {
        "openapi": "3.1.0",
        "info": {"title": "Fake OAuth API", "version": "1.0.0"},
        "servers": [{"url": base}],
        "paths": {
            "/me": {
                "get": {
                    "operationId": "getMe",
                    "summary": "Who am I",
                    "description": "Returns the signed-in account.",
                    "security": [{"oauth": ["read"]}],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {
            "securitySchemes": {
                "oauth": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"{base}/authorize",
                            "tokenUrl": f"{base}/token",
                            "scopes": SCOPES,
                        }
                    },
                }
            }
        },
    }


def _issue(state: _State, scope: str) -> dict[str, Any]:
    access = "fake-at-" + secrets.token_urlsafe(12)
    state.tokens[access] = scope
    return {
        "access_token": access,
        "refresh_token": "fake-rt-" + secrets.token_urlsafe(12),
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": scope,
    }


def build_fake_oauth_app() -> FastAPI:
    app = FastAPI(title="Fake OAuth")
    state = _State()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/specs/oauth.json")
    async def spec() -> dict[str, Any]:
        return _spec()

    @app.get("/authorize")
    async def authorize(request: Request) -> RedirectResponse:
        params = request.query_params
        redirect_uri = params["redirect_uri"]
        requested = params.get("scope", "")
        code = "fake-code-" + secrets.token_urlsafe(12)
        state.codes[code] = state.grant_override if state.grant_override is not None else requested
        sep = "&" if "?" in redirect_uri else "?"
        query = urlencode({"code": code, "state": params.get("state", "")})
        return RedirectResponse(f"{redirect_uri}{sep}{query}", status_code=302)

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        form = dict((await request.form()).items())
        state.log.append({k: v for k, v in form.items() if "secret" not in k})
        grant = form.get("grant_type")
        if grant == "authorization_code":
            scope = state.codes.pop(str(form.get("code", "")), None)
            if scope is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(_issue(state, scope))
        if grant == "refresh_token":
            return JSONResponse(_issue(state, str(form.get("scope", "read"))))
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            device = state.devices.get(str(form.get("device_code", "")))
            if device is None:
                return JSONResponse({"error": "expired_token"}, status_code=400)
            if not device["approved"]:
                return JSONResponse({"error": "authorization_pending"}, status_code=400)
            return JSONResponse(_issue(state, device["scope"]))
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    @app.post("/device/code")
    async def device_code(scope: str = Form(default="")) -> dict[str, Any]:
        device = "fake-dc-" + secrets.token_urlsafe(12)
        user_code = secrets.token_hex(2).upper() + "-" + secrets.token_hex(2).upper()
        state.devices[device] = {"user_code": user_code, "scope": scope, "approved": False}
        base = _base_url()
        return {
            "device_code": device,
            "user_code": user_code,
            "verification_uri": f"{base}/device",
            "verification_uri_complete": f"{base}/device?user_code={user_code}",
            "expires_in": 900,
            "interval": 1,
        }

    @app.post("/control/device/approve")
    async def device_approve(user_code: str) -> dict[str, bool]:
        for device in state.devices.values():
            if device["user_code"] == user_code:
                device["approved"] = True
                if state.grant_override is not None:
                    device["scope"] = state.grant_override
                return {"approved": True}
        return {"approved": False}

    @app.get("/me", response_model=None)
    async def me(request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        scope = state.tokens.get(auth.removeprefix("Bearer ").strip())
        if scope is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"login": "fake-user", "scope": scope})

    @app.post("/control/grant")
    async def set_grant(scope: str | None = None) -> dict[str, str | None]:
        state.grant_override = scope
        return {"grant_override": scope}

    @app.post("/control/reset")
    async def reset() -> dict[str, bool]:
        state.grant_override = None
        state.log.clear()
        return {"reset": True}

    @app.get("/control/log")
    async def log() -> list[dict[str, Any]]:
        return state.log

    return app
