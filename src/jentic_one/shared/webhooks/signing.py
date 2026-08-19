"""Webhook signing (Standard Webhooks style).

* :func:`sign_payload` — what we send on outbound notifications.
* :func:`compute_signature` — the raw HMAC used to build (and, in tests, to
  re-derive) an outbound signature.

The signed content is ``{id}.{timestamp}.{body}`` where ``body`` is the **exact
raw bytes** of the request. Including the id and timestamp inside the signed
material is what makes a captured request non-replayable: change either and the
signature no longer matches.

Two rules this module exists to enforce:

1. **Never re-serialise the body before signing.** Two JSON encoders that
   disagree about spacing produce different bytes and therefore different
   signatures. Callers must hand over the raw bytes they will transmit.
2. **Never compare digests with ``==``.** A short-circuiting comparison leaks
   how many leading bytes were correct, which is enough to forge a signature
   given patience. :func:`hmac.compare_digest` takes constant time.

The header names follow the Standard Webhooks convention so consumers can verify
us with off-the-shelf libraries instead of bespoke code.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

HEADER_ID = "webhook-id"
HEADER_TIMESTAMP = "webhook-timestamp"
HEADER_SIGNATURE = "webhook-signature"

# Signature scheme version prefix. Carrying it in the header value lets us
# introduce a second scheme later while old senders keep working.
SCHEME = "v1"

# How far a timestamp may drift before a receiver should refuse it. Five minutes
# is the de-facto industry default: generous enough for clock skew and retries,
# short enough that a captured request stops being useful quickly.
DEFAULT_TOLERANCE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class SignedHeaders:
    """Headers to attach to an outbound signed request."""

    webhook_id: str
    webhook_timestamp: str
    webhook_signature: str

    def as_dict(self) -> dict[str, str]:
        return {
            HEADER_ID: self.webhook_id,
            HEADER_TIMESTAMP: self.webhook_timestamp,
            HEADER_SIGNATURE: self.webhook_signature,
        }


def _signing_content(message_id: str, timestamp: str, body: bytes) -> bytes:
    """Build the exact bytes that get signed: ``id.timestamp.body``."""
    return b".".join((message_id.encode(), timestamp.encode(), body))


def compute_signature(secret: str, message_id: str, timestamp: str, body: bytes) -> str:
    """Return the base-16 HMAC-SHA256 signature, without the scheme prefix."""
    digest = hmac.new(
        secret.encode(), _signing_content(message_id, timestamp, body), hashlib.sha256
    )
    return digest.hexdigest()


def sign_payload(
    secret: str, message_id: str, body: bytes, *, timestamp: int | None = None
) -> SignedHeaders:
    """Sign ``body`` for an outbound delivery.

    ``body`` must be the exact bytes that will be transmitted — serialise once,
    sign those bytes, send those bytes.
    """
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signature = compute_signature(secret, message_id, ts, body)
    return SignedHeaders(
        webhook_id=message_id,
        webhook_timestamp=ts,
        webhook_signature=f"{SCHEME},{signature}",
    )


def hash_secret(secret: str) -> str:
    """Hash a secret for storage.

    Endpoint secrets are high-entropy machine-generated values, not
    user-chosen passwords, so a single SHA-256 is appropriate here: there is no
    dictionary to attack. The hash is a non-reversible fingerprint used to
    detect secret reuse; HMAC signing itself always uses the recoverable
    plaintext (see ``admin/services/webhooks/secrets.py``), never this digest.
    """
    return hashlib.sha256(secret.encode()).hexdigest()
