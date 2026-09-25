"""ES256 ID token issuance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from jentic_one.shared.config import AuthConfig
from jentic_one.shared.crypto import load_es256_private_key

ID_TOKEN_TTL_SECONDS = 3600


def issue_id_token(
    config: AuthConfig,
    *,
    issuer: str,
    sub: str,
    email: str,
    aud: str,
    nonce: str | None = None,
) -> str:
    """Issue an ES256-signed OIDC ID token.

    Uses the first signing key in the config as the active signer.
    """
    if not config.id_signing:
        raise ValueError("No signing keys configured — cannot issue ID tokens")

    active_key_config = config.id_signing[0]
    private_key = load_es256_private_key(active_key_config)

    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": issuer,
        "sub": sub,
        "aud": aud,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ID_TOKEN_TTL_SECONDS)).timestamp()),
    }
    if nonce:
        payload["nonce"] = nonce

    headers: dict[str, str] = {"kid": active_key_config.kid}

    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


def get_active_kid(config: AuthConfig) -> str | None:
    """Return the kid of the active signing key, or None if unconfigured."""
    if config.id_signing:
        return config.id_signing[0].kid
    return None
