"""Unit tests for auth crypto utilities."""

from __future__ import annotations

import re

from jentic_one.auth.services.crypto import (
    generate_agent_api_key,
    hash_secret,
)

_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def test_generate_agent_api_key_has_jak_prefix() -> None:
    key = generate_agent_api_key()
    assert key.startswith("jak_")


def test_generate_agent_api_key_body_is_base64url() -> None:
    key = generate_agent_api_key()
    body = key.removeprefix("jak_")
    assert _BASE64URL_RE.match(body)


def test_generate_agent_api_key_is_unique() -> None:
    keys = {generate_agent_api_key() for _ in range(10)}
    assert len(keys) == 10


def test_hash_secret_is_stable() -> None:
    assert hash_secret("test-value") == hash_secret("test-value")


def test_hash_secret_differs_for_different_inputs() -> None:
    assert hash_secret("a") != hash_secret("b")
