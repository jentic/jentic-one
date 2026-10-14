"""Canonical API identity models shared across layers."""

from pydantic import BaseModel, Field


class APIReference(BaseModel):
    """Identifies a target API — the strict (all-required) variant.

    Used in responses and as the canonical identity tuple.
    """

    vendor: str = Field(description="Vendor of the API (for example `stripe.com`).")
    name: str = Field(description="Name of the API within the vendor.")
    version: str = Field(description="Version of the API.")


class APIReferenceRequest(BaseModel):
    """Relaxed variant for request bodies where partial identification is allowed."""

    vendor: str = Field(description="Vendor of the API (for example `stripe.com`).")
    name: str = Field(
        default="",
        description="Name of the API within the vendor; empty to cover every API of the vendor.",
    )
    version: str = Field(
        default="",
        description="Version of the API; empty to cover every version.",
    )
    # The catalog identity slug of the API this credential targets
    # (`domain[/sub-api]`, e.g. `nytimes.com/article_search`), when the client
    # knows it (catalog imports and workspace APIs carry it). Stored verbatim
    # so credential surfaces can derive friendly titles; purely display-side —
    # never part of credential-resolution identity.
    catalog_api_id: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Catalog identity of the API (`domain[/sub-api]`, for example "
            "`nytimes.com/article_search`), when known; used for display only, not for "
            "matching."
        ),
    )


class ServedApiRef(BaseModel):
    """An API served by an agent's bound credential, keyed by its stored identity.

    Distinct from ``APIReference`` on purpose: this carries the *stored* credential
    identity, where ``api_name``/``api_version`` may be NULL (the "covers all
    names/versions" wildcard, #775) — so they're optional here, unlike the strict
    all-required ``APIReference``. Shared so the auth service schema and the
    ``/me`` web schema use ONE model instead of two identical copies.
    """

    api_vendor: str = Field(description="Vendor of the served API.")
    api_name: str | None = Field(
        default=None, description="Name of the served API; null means every API of the vendor."
    )
    api_version: str | None = Field(
        default=None, description="Version of the served API; null means every version."
    )
