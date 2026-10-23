"""Shared parameters for the ``/apis`` and ``/apis/{vendor}/{name}/{version}`` routes.

Vendor and name arrive in whatever spelling the caller typed
(``Stripe.com``, ``stripe.com``, ``stripe-com``). Ingest stores both as
:func:`~jentic_one.shared.models.slugify_api_field` slugs, and search's
``api`` filter applies the same rule, so these parameters normalise with it
too: every spelling search resolves also resolves here. The version is only
stripped of surrounding whitespace, as ingest and search do, because
slugifying it corrupts values such as ``1.1.4``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query
from pydantic import AfterValidator

from jentic_one.shared.models import slugify_api_field
from jentic_one.shared.web import openapi_examples as ex

_SLUG_RULE = (
    "Normalised to its stored slug (lowercased; each run of characters outside "
    "`[a-z0-9-]` becomes `-`; leading and trailing `-` removed), so `Stripe.com` and "
    "`stripe-com` are equivalent. Responses and `_links` use the slug form. A value "
    "with no slug characters is rejected with 422."
)


def normalise_api_field(value: str) -> str:
    """Slugify a vendor/name value, rejecting one that has no slug characters."""
    slug = slugify_api_field(value)
    if not slug:
        raise ValueError("must contain at least one letter or digit")
    return slug


def _normalise_optional_api_field(value: str | None) -> str | None:
    return None if value is None else normalise_api_field(value)


ApiVendorPath = Annotated[
    str,
    Path(
        description=f"Vendor of the API. {_SLUG_RULE}",
        openapi_examples=ex.example(ex.API_VENDOR),
    ),
    AfterValidator(normalise_api_field),
]
"""Vendor path segment, normalised to its stored slug form."""

ApiNamePath = Annotated[
    str,
    Path(
        description=f"Name of the API. {_SLUG_RULE}",
        openapi_examples=ex.example(ex.API_NAME),
    ),
    AfterValidator(normalise_api_field),
]
"""Name path segment, normalised to its stored slug form."""

ApiVersionPath = Annotated[
    str,
    Path(
        description=(
            "Version of the API. Matched exactly after trimming surrounding whitespace; "
            "it is not slugified, so `1.1.4` and `1-1-4` are different versions."
        ),
        openapi_examples=ex.example(ex.API_VERSION),
    ),
    AfterValidator(str.strip),
]
"""Version path segment, matched verbatim after trimming whitespace."""

ApiVendorQuery = Annotated[
    str | None,
    Query(
        description=f"Only return APIs from this vendor. {_SLUG_RULE}",
        openapi_examples=ex.example(ex.API_VENDOR),
    ),
    AfterValidator(_normalise_optional_api_field),
]
"""Optional vendor filter on ``GET /apis``, normalised like the path segment."""
