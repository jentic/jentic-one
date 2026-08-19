"""Webhook signing-secret generation.

Lives under ``_support/`` because that is the only place in ``admin/`` allowed to
import :mod:`secrets` (enforced by ``tests/arch/test_admin_secrets_isolation.py``).

Unlike an invite token, a webhook secret must be **recoverable in plaintext**: HMAC
signing of an outbound request needs the key itself, so there is nothing to
hash irreversibly. It is stored encrypted instead — see
``admin/services/webhooks/secrets.py``.
"""

from __future__ import annotations

import secrets

__all__ = ["WEBHOOK_SECRET_PREFIX", "generate_webhook_secret"]

WEBHOOK_SECRET_PREFIX = "whsec_"  # pragma: allowlist secret

# 32 bytes of entropy, matching the invite-token strength. Comfortably beyond
# brute force, and the recommended key length for HMAC-SHA256 (RFC 7518 §3.2
# wants at least the hash output size, 32 bytes).
_SECRET_BYTES = 32


def generate_webhook_secret() -> str:
    """Return a new URL-safe signing secret with a ``whsec_`` prefix.

    The prefix follows the Standard Webhooks convention and makes the value
    recognisable in logs and secret scanners — so an accidentally committed
    secret can be detected and revoked rather than sitting unnoticed.
    """
    return f"{WEBHOOK_SECRET_PREFIX}{secrets.token_urlsafe(_SECRET_BYTES)}"
