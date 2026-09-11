"""Tests for toolkit key generation utility."""

from __future__ import annotations

import hashlib
import re

from argon2 import PasswordHasher

from jentic_one.control.repos.toolkit_key_gen import generate_toolkit_key, verify_toolkit_key


def test_plaintext_format() -> None:
    plaintext, _hashed, _preview, _lookup = generate_toolkit_key()
    assert re.match(r"^jntc_live_[0-9a-f]{32}$", plaintext)


def test_preview_format() -> None:
    plaintext, _hashed, preview, _lookup = generate_toolkit_key()
    assert preview == f"...{plaintext[-4:]}"


def test_lookup_hash_is_sha256_of_plaintext() -> None:
    plaintext, _hashed, _preview, lookup = generate_toolkit_key()
    assert lookup == hashlib.sha256(plaintext.encode()).hexdigest()


def test_hash_validates_against_plaintext() -> None:
    plaintext, hashed, _preview, _lookup = generate_toolkit_key()
    hasher = PasswordHasher()
    assert hasher.verify(hashed, plaintext)


def test_verify_toolkit_key_valid() -> None:
    plaintext, hashed, _preview, _lookup = generate_toolkit_key()
    assert verify_toolkit_key(plaintext, hashed) is True


def test_verify_toolkit_key_invalid() -> None:
    _plaintext, hashed, _preview, _lookup = generate_toolkit_key()
    assert verify_toolkit_key("wrong_key", hashed) is False


def test_unique_keys() -> None:
    keys = {generate_toolkit_key()[0] for _ in range(10)}
    assert len(keys) == 10
