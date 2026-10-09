"""Auth-validation router — exercises the broker's credential injection.

Each endpoint returns ``401`` unless the credential the corresponding OpenAPI
``securityScheme`` would inject is present. This only checks *presence* (the
broker owns acquisition/rotation), so any non-empty value passes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import JSONResponse

router = APIRouter(prefix="/auth", tags=["auth"])

HEADER_AUTHORIZATION: Final = "Authorization"
HEADER_API_KEY: Final = "X-Api-Key"
HEADER_APP_ID: Final = "X-App-Id"
QUERY_API_KEY: Final = "api_key"

_BEARER_PREFIX: Final = "bearer "
_BASIC_PREFIX: Final = "basic "


class AuthScheme(StrEnum):
    BEARER = "bearer"
    BASIC = "basic"
    API_KEY = "api-key"  # pragma: allowlist secret
    API_KEY_QUERY = "api-key-query"  # pragma: allowlist secret
    OAUTH2 = "oauth2"
    COMPLEX = "complex"


def _unauthorized(scheme: AuthScheme) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"authenticated": False, "scheme": scheme.value},
    )


def _authorized(scheme: AuthScheme) -> dict[str, object]:
    return {"authenticated": True, "scheme": scheme.value}


def _has_authorization_prefix(request: Request, prefix: str) -> bool:
    value = request.headers.get(HEADER_AUTHORIZATION, "")
    return value.lower().startswith(prefix)


@router.get("/bearer", response_model=None)
async def auth_bearer(request: Request) -> JSONResponse | dict[str, object]:
    if not _has_authorization_prefix(request, _BEARER_PREFIX):
        return _unauthorized(AuthScheme.BEARER)
    return _authorized(AuthScheme.BEARER)


@router.get("/basic", response_model=None)
async def auth_basic(request: Request) -> JSONResponse | dict[str, object]:
    if not _has_authorization_prefix(request, _BASIC_PREFIX):
        return _unauthorized(AuthScheme.BASIC)
    return _authorized(AuthScheme.BASIC)


@router.get("/api-key", response_model=None)
async def auth_api_key(request: Request) -> JSONResponse | dict[str, object]:
    if not request.headers.get(HEADER_API_KEY):
        return _unauthorized(AuthScheme.API_KEY)
    return _authorized(AuthScheme.API_KEY)


@router.get("/api-key-query", response_model=None)
async def auth_api_key_query(request: Request) -> JSONResponse | dict[str, object]:
    if not request.query_params.get(QUERY_API_KEY):
        return _unauthorized(AuthScheme.API_KEY_QUERY)
    return _authorized(AuthScheme.API_KEY_QUERY)


@router.get("/oauth2", response_model=None)
async def auth_oauth2(request: Request) -> JSONResponse | dict[str, object]:
    if not _has_authorization_prefix(request, _BEARER_PREFIX):
        return _unauthorized(AuthScheme.OAUTH2)
    return _authorized(AuthScheme.OAUTH2)


@router.get("/complex", response_model=None)
async def auth_complex(request: Request) -> JSONResponse | dict[str, object]:
    has_api_key = bool(request.headers.get(HEADER_API_KEY))
    has_app_id = bool(request.headers.get(HEADER_APP_ID))
    if not (has_api_key and has_app_id):
        return _unauthorized(AuthScheme.COMPLEX)
    return _authorized(AuthScheme.COMPLEX)
