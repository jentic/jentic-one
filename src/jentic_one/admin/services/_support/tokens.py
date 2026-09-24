"""Invite-token generation/hashing helpers.

JWT issue/verify primitives now live in :mod:`jentic_one.shared.auth.tokens`
and are re-exported here for backwards compatibility.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import b32encode

from jentic_one.shared.auth.tokens import InvalidTokenError, decode_jwt, issue_jwt

__all__ = [
    "InvalidTokenError",
    "decode_jwt",
    "generate_client_id",
    "generate_client_secret",
    "generate_invite_token",
    "hash_invite_token",
    "issue_jwt",
]


def generate_client_id() -> str:
    """Generate a unique OAuth client_id with `oc_` prefix."""
    return f"oc_{secrets.token_urlsafe(24)}"


def generate_client_secret() -> str:
    """Generate a plaintext OAuth client secret."""
    return secrets.token_urlsafe(32)


def generate_invite_token() -> str:
    """Generate a new invite token with `inv_` prefix."""
    raw = secrets.token_bytes(32)
    encoded = b32encode(raw).decode("ascii").rstrip("=").lower()
    return f"inv_{encoded}"


def hash_invite_token(plaintext: str, pepper: str) -> str:
    """HMAC-SHA-256 hash of the plaintext invite token with a server pepper."""
    return hmac.HMAC(
        pepper.encode("utf-8"),
        plaintext.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
