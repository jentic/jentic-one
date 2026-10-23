"""Shared path parameters for the ``/apis/{vendor}/{name}/{version}`` routes.

Vendor and name arrive in whatever spelling the caller typed
(``Stripe.com``, ``stripe.com``, ``stripe-com``). Ingest stores both as
:func:`~jentic_one.shared.models.slugify_api_field` slugs, and search's
``api`` filter applies the same rule, so the path parameters normalise with it
too: every spelling search resolves also resolves here. The version is matched
verbatim, because slugifying it corrupts values such as ``1.1.4``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import AfterValidator

from jentic_one.shared.models import slugify_api_field
from jentic_one.shared.web import openapi_examples as ex

ApiVendorPath = Annotated[
    str,
    Path(description="Vendor of the API.", openapi_examples=ex.example(ex.API_VENDOR)),
    AfterValidator(slugify_api_field),
]
"""Vendor path segment, normalised to its stored slug form."""

ApiNamePath = Annotated[
    str,
    Path(description="Name of the API.", openapi_examples=ex.example(ex.API_NAME)),
    AfterValidator(slugify_api_field),
]
"""Name path segment, normalised to its stored slug form."""

ApiVersionPath = Annotated[
    str,
    Path(description="Version of the API.", openapi_examples=ex.example(ex.API_VERSION)),
]
"""Version path segment, matched verbatim."""
