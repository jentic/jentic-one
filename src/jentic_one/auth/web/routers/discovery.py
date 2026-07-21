"""OAuth discovery and JWKS endpoints (RFC 8414)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from jentic_one.shared.auth import CachedJWKSPublisher
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx

router = APIRouter()

_jwks_publishers: dict[tuple[tuple[str, str], ...], CachedJWKSPublisher] = {}


def _publisher_key(config: AuthConfig) -> tuple[tuple[str, str], ...]:
    """Identity of the active signing material, so a key rotation rebuilds the JWKS."""
    return tuple((k.kid, k.private_key_pem.get_secret_value()) for k in config.id_signing)


def _get_publisher(config: AuthConfig) -> CachedJWKSPublisher:
    key = _publisher_key(config)
    publisher = _jwks_publishers.get(key)
    if publisher is None:
        publisher = CachedJWKSPublisher(config)
        _jwks_publishers[key] = publisher
    return publisher


def _build_issuer(config: AuthConfig, request: Request) -> str:
    if config.canonical_base_url:
        return config.canonical_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get(
    "/.well-known/oauth-authorization-server",
    summary="OAuth authorization server metadata",
)
async def oauth_authorization_server(
    request: Request, ctx: Context = Depends(get_ctx)
) -> dict[str, Any]:
    """Return RFC 8414 authorization-server metadata (endpoints, grant types, algorithms)."""
    issuer = _build_issuer(ctx.config.auth, request)
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "introspection_endpoint": f"{issuer}/oauth/introspect",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "grant_types_supported": [
            "authorization_code",
            "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "refresh_token",
            "client_credentials",
        ],
        "token_endpoint_auth_methods_supported": ["private_key_jwt", "none"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["ES256"],
        "token_endpoint_auth_signing_alg_values_supported": ["EdDSA"],
    }


@router.get("/.well-known/jwks.json", summary="JSON Web Key Set")
async def jwks(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Return the JWKS document with the active public signing keys (ES256)."""
    return _get_publisher(ctx.config.auth).get_jwks()
