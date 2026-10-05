"""Shared credential-generation and hashing utilities for auth services."""

from __future__ import annotations

import secrets

from jentic_one.shared.crypto.digest import hash_secret

__all__ = ["generate_agent_api_key", "generate_client_secret", "hash_secret"]

_CLIENT_SECRET_PREFIX = "jcs_"
_AGENT_API_KEY_PREFIX = "jak_"


def generate_client_secret() -> str:
    """Generate a prefixed, URL-safe client secret."""
    return f"{_CLIENT_SECRET_PREFIX}{secrets.token_urlsafe(32)}"


def generate_agent_api_key() -> str:
    """Generate a prefixed, URL-safe API key for agents."""
    return f"{_AGENT_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
