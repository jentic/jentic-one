"""Webhook primitives for the outbound (notification) delivery half of the
feature.

Lives under ``shared/`` because both the control-plane routers and the
background delivery worker need the same signing code, and duplicating it is
how signing implementations drift apart.
"""

from __future__ import annotations

from jentic_one.shared.webhooks.signing import (
    DEFAULT_TOLERANCE_SECONDS,
    HEADER_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    SignedHeaders,
    compute_signature,
    hash_secret,
    sign_payload,
)

__all__ = [
    "DEFAULT_TOLERANCE_SECONDS",
    "HEADER_ID",
    "HEADER_SIGNATURE",
    "HEADER_TIMESTAMP",
    "SignedHeaders",
    "compute_signature",
    "hash_secret",
    "sign_payload",
]
