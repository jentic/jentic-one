"""Upstream + catalog fixture for the issue #1556 agent-lane e2e loop.

One process serving:

- the smoke-upstream harness served once per API under test, each on its own
  loopback port (``API_PORT_BASE`` + index, default 55620+): a host is served
  by one vendor at a time, so every API needs its own ``host:port``;
- ``/apis.json`` — an ``include``-style catalog manifest listing every API;
- ``/apis/openapi/<domain>/api/1.0.0/openapi.json`` — each API's spec, with a
  single declared security scheme (or two for the ``mixed`` API, none for
  ``noauth``). OAuth schemes point at the fake authorization server
  (``tests.harness.fake_oauth_as``).

``PORT`` (default 55602) is this process's port; ``FAKE_AS_URL`` (default
``https://127.0.0.1:55603``) is the fake authorization server.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Final

import uvicorn
from fastapi import FastAPI
from tests.harness.smoke_upstream.app import build_smoke_app

HOST: Final = "127.0.0.1"
PORT: Final = int(os.environ.get("PORT", "55602"))
API_PORT_BASE: Final = int(os.environ.get("API_PORT_BASE", "55620"))
FAKE_AS_URL: Final = os.environ.get("FAKE_AS_URL", "https://127.0.0.1:55603").rstrip("/")
BASE: Final = f"http://{HOST}:{PORT}"

OAUTH_SCOPES: Final = {"read": "Read access", "write": "Write access"}


@dataclass(frozen=True)
class ApiFixture:
    key: str
    scheme: dict[str, dict[str, Any]]
    path: str
    port: int = 0

    @property
    def domain(self) -> str:
        return f"e2e-{self.key}.test"

    @property
    def api_id(self) -> str:
        return f"{self.domain}/api"

    @property
    def server_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    @property
    def include_url(self) -> str:
        return f"{BASE}/apis/openapi/{self.domain}/api/1.0.0/apis.json"

    @property
    def spec_path(self) -> str:
        return f"/apis/openapi/{self.domain}/api/1.0.0/openapi.json"


def _oauth_scheme() -> dict[str, dict[str, Any]]:
    return {
        "oauth": {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": f"{FAKE_AS_URL}/authorize",
                    "tokenUrl": f"{FAKE_AS_URL}/token",
                    "scopes": OAUTH_SCOPES,
                }
            },
        }
    }


_APIS: Final = (
    ApiFixture(
        "apikey",
        {"apiKey": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}},
        "/auth/api-key",
    ),
    ApiFixture(
        "apikeyq",
        {"apiKey": {"type": "apiKey", "in": "query", "name": "api_key"}},
        "/auth/api-key-query",
    ),
    ApiFixture("bearer", {"bearer": {"type": "http", "scheme": "bearer"}}, "/auth/bearer"),
    ApiFixture("basic", {"basic": {"type": "http", "scheme": "basic"}}, "/auth/basic"),
    ApiFixture("oauth", _oauth_scheme(), "/auth/oauth2"),
    ApiFixture("oauthbind", _oauth_scheme(), "/auth/oauth2"),
    ApiFixture("shared", _oauth_scheme(), "/auth/oauth2"),
    ApiFixture("single", _oauth_scheme(), "/auth/oauth2"),
    ApiFixture("vendor", _oauth_scheme(), "/auth/oauth2"),
    ApiFixture(
        "mixed",
        {**_oauth_scheme(), "apiKey": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}},
        "/auth/api-key",
    ),
    ApiFixture("noauth", {}, "/auth/bearer"),
    ApiFixture("reject", {"bearer": {"type": "http", "scheme": "bearer"}}, "/auth/bearer"),
    ApiFixture("expire", {"bearer": {"type": "http", "scheme": "bearer"}}, "/auth/bearer"),
    ApiFixture("archive", {"bearer": {"type": "http", "scheme": "bearer"}}, "/auth/bearer"),
    ApiFixture("perms", {"bearer": {"type": "http", "scheme": "bearer"}}, "/auth/bearer"),
    ApiFixture("gateoff", {"bearer": {"type": "http", "scheme": "bearer"}}, "/auth/bearer"),
)


APIS: Final = tuple(
    ApiFixture(a.key, a.scheme, a.path, API_PORT_BASE + i) for i, a in enumerate(_APIS)
)


def spec_for(api: ApiFixture) -> dict[str, Any]:
    security = [{name: (["read"] if name == "oauth" else [])} for name in api.scheme]
    op: dict[str, Any] = {
        "operationId": f"{api.key}Echo",
        "summary": f"{api.key} protected echo",
        "description": f"Returns 200 only when the {api.key} credential is present.",
        "responses": {"200": {"description": "ok"}},
    }
    if security:
        op["security"] = security
    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {"title": f"E2E {api.key} API", "version": "1.0.0"},
        "servers": [{"url": api.server_url}],
        "paths": {
            api.path: {"get": op},
            "/behavior/echo": {
                "post": {
                    "operationId": f"{api.key}PostEcho",
                    "summary": "Echo the request",
                    "responses": {"200": {"description": "ok"}},
                    **({"security": security} if security else {}),
                }
            },
        },
    }
    if api.scheme:
        spec["components"] = {"securitySchemes": api.scheme}
        spec["security"] = security
    return spec


def build_app() -> FastAPI:
    app = FastAPI(title="E2E #1556 upstream")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/apis.json")
    async def manifest() -> dict[str, Any]:
        return {"include": [{"url": api.include_url} for api in APIS]}

    for api in APIS:

        async def _spec(api: ApiFixture = api) -> dict[str, Any]:
            return spec_for(api)

        app.add_api_route(api.spec_path, _spec, methods=["GET"])

    return app


async def _serve_all() -> None:
    servers = [
        uvicorn.Server(uvicorn.Config(build_app(), host=HOST, port=PORT, log_level="warning"))
    ]
    servers += [
        uvicorn.Server(
            uvicorn.Config(build_smoke_app(), host=HOST, port=api.port, log_level="warning")
        )
        for api in APIS
    ]
    await asyncio.gather(*(server.serve() for server in servers))


def main() -> None:
    asyncio.run(_serve_all())


if __name__ == "__main__":
    main()
