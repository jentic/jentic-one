"""ES256 signing key management — loads PEM keys and produces JWK representations.

This module is permitted to import ``cryptography`` (exempted in the arch test
alongside encryption.py). All other code needing EC key operations must use
this facade.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)

from jentic_one.shared.config import SigningKeyConfig


def load_es256_private_key(key_config: SigningKeyConfig) -> EllipticCurvePrivateKey:
    """Load and validate an ES256 private key from PEM config."""
    pem_bytes = key_config.private_key_pem.get_secret_value().encode()
    key = load_pem_private_key(pem_bytes, password=None)
    if not isinstance(key, EllipticCurvePrivateKey):
        raise ValueError(f"Key '{key_config.kid}' is not an EC private key")
    if not isinstance(key.curve, SECP256R1):
        raise ValueError(f"Key '{key_config.kid}' must use P-256 curve for ES256")
    return key


def signing_key_spki_fingerprint(pem: str) -> str:
    """SHA-256 fingerprint of the public-key SPKI, stable across PEM encodings."""
    key = load_pem_private_key(pem.encode(), password=None)
    spki = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(spki).hexdigest()


def ec_public_key_to_jwk(pub: EllipticCurvePublicKey, kid: str) -> dict[str, str]:
    """Convert an EC public key to a JWK dict suitable for JWKS publication."""
    numbers = pub.public_numbers()
    x_bytes = numbers.x.to_bytes(32, byteorder="big")
    y_bytes = numbers.y.to_bytes(32, byteorder="big")
    return {
        "kty": "EC",
        "crv": "P-256",
        "use": "sig",
        "alg": "ES256",
        "kid": kid,
        "x": base64.urlsafe_b64encode(x_bytes).rstrip(b"=").decode(),
        "y": base64.urlsafe_b64encode(y_bytes).rstrip(b"=").decode(),
    }
