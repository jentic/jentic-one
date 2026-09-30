"""Deterministic digests for storing bearer capabilities at rest.

High-entropy random tokens (API keys, client secrets, poll tokens) are stored
as their SHA-256 hex digest: the plaintext is handed to the caller once and
lookups hash the presented value. A fast, unsalted hash is sufficient because
the inputs are random, not user-chosen.
"""

from __future__ import annotations

import hashlib

__all__ = ["hash_secret"]


def hash_secret(value: str) -> str:
    """One-way SHA-256 hex digest for credential storage."""
    return hashlib.sha256(value.encode()).hexdigest()
