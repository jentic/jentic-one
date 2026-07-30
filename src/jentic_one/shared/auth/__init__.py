"""Shared identity/auth primitives: schemas, JWT helpers, token verification."""

from jentic_one.shared.auth.api_key_resolver import ApiKeyResolver
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import (
    ChangePasswordPayload,
    Identity,
    LoginPayload,
    TokenBundle,
)
from jentic_one.shared.auth.jwks import CachedJWKSPublisher, resolve_agent_key
from jentic_one.shared.auth.tokens import decode_jwt, issue_jwt
from jentic_one.shared.auth.verify import verify_token

__all__ = [
    "ApiKeyResolver",
    "CachedJWKSPublisher",
    "ChangePasswordPayload",
    "Identity",
    "LoginPayload",
    "TokenBundle",
    "TokenValidationError",
    "decode_jwt",
    "issue_jwt",
    "resolve_agent_key",
    "verify_token",
]
