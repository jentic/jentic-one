"""Credential encryption facade — the single sanctioned path for cryptographic operations."""

from jentic_one.shared.crypto.digest import hash_secret
from jentic_one.shared.crypto.encryption import DecryptionError, EncryptionService
from jentic_one.shared.crypto.rotation import (
    EncryptedStorageRotator,
    FamilyRotationStats,
    FamilyStatsMap,
    KeyRetirementError,
    KeyRotationReport,
    RotationFailure,
    RotationReport,
    assert_only_active_key,
    rotate_encrypted_storage,
    verify_key_retired,
)
from jentic_one.shared.crypto.signing import ec_public_key_to_jwk, load_es256_private_key

__all__ = [
    "DecryptionError",
    "EncryptedStorageRotator",
    "EncryptionService",
    "FamilyRotationStats",
    "FamilyStatsMap",
    "KeyRetirementError",
    "KeyRotationReport",
    "RotationFailure",
    "RotationReport",
    "assert_only_active_key",
    "ec_public_key_to_jwk",
    "hash_secret",
    "load_es256_private_key",
    "rotate_encrypted_storage",
    "verify_key_retired",
]
