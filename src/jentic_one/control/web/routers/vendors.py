"""Vendor auth registry read endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from jentic_one.control.services.vendors.service import VendorRegistryService
from jentic_one.control.web.deps import get_vendor_registry_service
from jentic_one.control.web.schemas.integrations import (
    VendorAuthCapabilitiesResponse,
    VendorFlowResponse,
    VendorListResponse,
    VendorScopeResponse,
    VendorSummaryResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex

router = APIRouter(tags=["Vendors"])


@router.get("/vendors", summary="List verified vendors")
async def list_vendors(
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: VendorRegistryService = Depends(get_vendor_registry_service),
) -> VendorListResponse:
    """Public metadata for every vendor known to the platform.

    Unioned across two sources: admin-registered ``oauth_app_registrations``
    rows and the platform-shipped ``vendors`` config. The two never dedupe —
    each active registration and each config entry is its own picker card,
    and ``flow_kinds`` lists every flow that card can run. Never returns
    secrets.
    """
    entries = await svc.list_entries()
    # For DB-sourced rows the ``vendor`` field (config's fully-qualified
    # ``<host>/<api-id>`` shape) is not stored — the DB tracks a plain
    # ``api_vendor`` slug and re-uses it as the entry key. Fall back to the
    # key in that case so the wire payload stays populated.
    config_entries = svc._config.entries
    return VendorListResponse(
        data=[
            VendorSummaryResponse(
                entry_id=e.entry_id,
                registration_id=e.registration_id,
                key=e.key,
                vendor=(config_entries[e.key].vendor if e.key in config_entries else e.key),
                display_name=e.display_name,
                name=e.name,
                source=e.source,
                flow_kinds=list(e.flow_kinds),
                catalog_api_id=e.catalog_api_id,
            )
            for e in entries
        ]
    )


@router.get(
    "/vendors/{vendor_key}/auth-capabilities",
    summary="Get a vendor's OAuth flows and scopes",
)
async def get_auth_capabilities(
    vendor_key: Annotated[
        str,
        Path(description="Vendor registry key.", openapi_examples=ex.example(ex.VENDOR_KEY)),
    ],
    oauth_app_registration_id: str | None = Query(
        default=None,
        max_length=30,
        description=(
            "Pin to a specific admin-registered OAuth app when the vendor has "
            "more than one. Returns that registration's scopes + client_id, so "
            "the picker's tile and the connect payload stay aligned."
        ),
        openapi_examples=ex.example(ex.OAUTH_APP_REGISTRATION_ID),
    ),
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: VendorRegistryService = Depends(get_vendor_registry_service),
) -> VendorAuthCapabilitiesResponse:
    """Describe how to connect a vendor: its OAuth flows and scope catalogue.

    `vendor_key` is a key from `GET /vendors`. Returns the vendor's display
    name, the OAuth `flows` it supports (`authorization_code` and/or
    `device_authorization`), and every scope with its `classification`
    (`read`, `write` or `admin`), whether it is requested by default, and a
    description. Use it to build a connect screen before starting a connect
    session. When the vendor has several admin-registered OAuth apps, pass
    `oauth_app_registration_id` to pick one; without it the request fails
    with 400 (`ambiguous_vendor`). The OAuth client secret is never returned.
    An unknown vendor returns 404 (`unknown_vendor`).
    """
    entry = await svc.resolve_by_pin(vendor_key, registration_id=oauth_app_registration_id)
    return VendorAuthCapabilitiesResponse(
        vendor=entry.vendor,
        display_name=entry.display_name,
        flows=[VendorFlowResponse(kind=f.kind) for f in entry.flows],
        scopes=[
            VendorScopeResponse(
                name=s.name,
                classification=s.classification,
                default=s.default,
                description=s.description,
            )
            for s in entry.scopes
        ],
    )
