"""Canonical API identity models shared across layers."""

from pydantic import BaseModel


class APIReference(BaseModel):
    """Identifies a target API — the strict (all-required) variant.

    Used in responses and as the canonical identity tuple.
    """

    vendor: str
    name: str
    version: str


class APIReferenceRequest(BaseModel):
    """Relaxed variant for request bodies where partial identification is allowed."""

    vendor: str
    name: str = ""
    version: str = ""


class ServedApiRef(BaseModel):
    """An API served by a toolkit's bound credential, keyed by its stored identity.

    Distinct from ``APIReference`` on purpose: this carries the *stored* credential
    identity, where ``api_name``/``api_version`` may be NULL (the "covers all
    names/versions" wildcard, #775) — so they're optional here, unlike the strict
    all-required ``APIReference``. Shared so the auth service schema and the
    ``/me`` web schema use ONE model instead of two identical copies.
    """

    api_vendor: str
    api_name: str | None = None
    api_version: str | None = None
