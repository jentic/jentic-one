"""Toolkit API key generation and hashing utility.

Key issuance is retired (theme-5 Phase 4), so no service mints new keys; the
generator remains as key-record plumbing next to the toolkit repos — tests
fabricating retired ``jntc_live_`` keys still need it — and it dies with the
toolkit tables in Phase 6b.
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def lookup_hash(plaintext: str) -> str:
    """Deterministic SHA-256 digest of a toolkit key, for O(1) broker lookup.

    The salted argon2 ``hashed_key`` cannot be queried by value; this digest is
    the index the broker uses to find the key row before (optionally) verifying
    against the argon2 hash.
    """
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_toolkit_key() -> tuple[str, str, str, str]:
    """Generate a toolkit API key.

    Returns:
        (plaintext, hashed, preview, lookup) where:
        - plaintext: ``jntc_live_<32-hex-random>``
        - hashed: argon2id hash of the plaintext (verification hash)
        - preview: ``...<last 4 chars>``
        - lookup: SHA-256 digest of the plaintext (deterministic lookup index)
    """
    random_hex = secrets.token_hex(16)
    plaintext = f"jntc_live_{random_hex}"
    hashed = _hasher.hash(plaintext)
    preview = f"...{plaintext[-4:]}"
    return plaintext, hashed, preview, lookup_hash(plaintext)


def verify_toolkit_key(plaintext: str, hashed: str) -> bool:
    """Verify a plaintext key against its argon2 hash."""
    try:
        return _hasher.verify(hashed, plaintext)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
