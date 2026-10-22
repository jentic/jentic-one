"""Canonical API identity models shared across layers."""

from pydantic import BaseModel, Field

# Literal mirrors of ``shared.web.openapi_examples`` (API_VENDOR, API_NAME,
# API_VERSION, CATALOG_API_ID): importing that module from here would pull in
# the ``shared.web`` package and create an import cycle.
_EX_API_VENDOR = "stripe.com"
_EX_API_NAME = "stripe"
_EX_API_VERSION = "2024-04-10"
_EX_CATALOG_API_ID = "stripe.com"


class APIReference(BaseModel):
    """Identifies a target API — the strict (all-required) variant.

    Used in responses and as the canonical identity tuple.
    """

    vendor: str = Field(
        description="Vendor of the API (for example `stripe.com`).", examples=[_EX_API_VENDOR]
    )
    name: str = Field(description="Name of the API within the vendor.", examples=[_EX_API_NAME])
    version: str = Field(description="Version of the API.", examples=[_EX_API_VERSION])


class APIReferenceRequest(BaseModel):
    """Relaxed variant for request bodies where partial identification is allowed."""

    vendor: str = Field(
        description="Vendor of the API (for example `stripe.com`).", examples=[_EX_API_VENDOR]
    )
    name: str = Field(
        default="",
        description="Name of the API within the vendor; empty to cover every API of the vendor.",
        examples=[_EX_API_NAME],
    )
    version: str = Field(
        default="",
        description="Version of the API; empty to cover every version.",
        examples=[_EX_API_VERSION],
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
        examples=[_EX_CATALOG_API_ID],
    )


class ServedApiRef(BaseModel):
    """An API served by an agent's bound credential, keyed by its stored identity.

    Distinct from ``APIReference`` on purpose: this carries the *stored* credential
    identity, where ``api_name``/``api_version`` may be NULL (the "covers all
    names/versions" wildcard, #775) — so they're optional here, unlike the strict
    all-required ``APIReference``. Shared so the auth service schema and the
    ``/me`` web schema use ONE model instead of two identical copies.
    """

    api_vendor: str = Field(description="Vendor of the served API.", examples=[_EX_API_VENDOR])
    api_name: str | None = Field(
        default=None,
        description="Name of the served API; null means every API of the vendor.",
        examples=[_EX_API_NAME],
    )
    api_version: str | None = Field(
        default=None,
        description="Version of the served API; null means every version.",
        examples=[_EX_API_VERSION],
    )
    api_role: str | None = Field(
        default=None,
        description=(
            "Set when the credential is a CLI-profile credential (one connect covering several "
            "APIs of a vendor): the API's role in the profile (`rest`, `graphql`, `git`, "
            "`uploads`). The binding's rules for that role apply to it."
        ),
        examples=["git"],
    )
