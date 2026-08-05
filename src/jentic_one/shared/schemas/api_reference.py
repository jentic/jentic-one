"""Canonical API identity models shared across layers."""

from pydantic import BaseModel, Field


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
    # The catalog identity slug of the API this credential targets
    # (`domain[/sub-api]`, e.g. `nytimes.com/article_search`), when the client
    # knows it (catalog imports and workspace APIs carry it). Stored verbatim
    # so credential surfaces can derive friendly titles; purely display-side —
    # never part of credential-resolution identity.
    catalog_api_id: str | None = Field(default=None, max_length=255)


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
