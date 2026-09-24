"""Pure idempotency primitives: the request fingerprint.

Pure domain helper (no FastAPI/httpx/DB) so it unit-tests in isolation — the
``IdempotencyStore`` *service* (over the shared-state backend) lives in
``broker/services/idempotency.py`` per the layering convention.

The fingerprint is the stable identity of a request under a given
``Idempotency-Key``: a repeat of the *same* fingerprint replays the original
response; a *different* fingerprint under the same key is a ``409`` conflict
(the client reused a key for a different request).
"""

from __future__ import annotations

import hashlib

_SEP = b"\0"


def fingerprint(method: str, url: str, toolkit_id: str, body: bytes | None) -> str:
    """Stable hash of the parts that define request identity.

    Includes the method, the reconstructed upstream URL, the resolved toolkit,
    and a hash of the (already body-capped) request body. Volatile headers are
    deliberately excluded — they don't change *what* the request does.
    """
    h = hashlib.sha256()
    for part in (method.upper(), url, toolkit_id):
        h.update(part.encode())
        h.update(_SEP)
    h.update(hashlib.sha256(body or b"").digest())
    return h.hexdigest()


__all__ = ["fingerprint"]
