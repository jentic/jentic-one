"""JWT issue/verify primitives shared across surfaces."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

# Re-exported so callers can catch decode failures without importing pyjwt
# themselves (the admin service layer bans direct jwt imports outside its
# _support seam).
from jwt import InvalidTokenError

__all__ = ["InvalidTokenError", "decode_jwt", "issue_jwt"]


def issue_jwt(claims: dict[str, Any], secret: str, ttl_seconds: int) -> str:
    """Sign a JWT with HS256 containing the given claims and expiry."""
    now = datetime.now(UTC)
    payload = {
        **claims,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_jwt(token: str, secret: str) -> dict[str, Any]:
    """Verify and decode a JWT. Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError."""
    result: dict[str, Any] = jwt.decode(token, secret, algorithms=["HS256"])
    return result
