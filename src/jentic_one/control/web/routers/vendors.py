"""Vendor auth registry read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

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

router = APIRouter(tags=["Vendors"])


@router.get("/vendors", summary="List verified vendors")
async def list_vendors(
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: VendorRegistryService = Depends(get_vendor_registry_service),
) -> VendorListResponse:
    """Public metadata for every vendor known to the platform.

    Unioned across two sources: admin-registered ``oauth_app_registrations``
    rows and the platform-shipped ``vendors`` config. When a vendor slug
    exists in both, the DB row wins so admin-managed registrations always
    take precedence in the UI's "Add integration" picker. Never returns
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
                flow_kinds=[e.flow_kind],
            )
            for e in entries
        ]
    )


@router.get(
    "/vendors/{vendor_key}/auth-capabilities",
    summary="Get a vendor's SSO capabilities",
    response_model=None,
)
async def get_auth_capabilities(
    vendor_key: str,
    oauth_app_registration_id: str | None = Query(
        default=None,
        max_length=30,
        description=(
            "Pin to a specific admin-registered OAuth app when the vendor has "
            "more than one. Returns that registration's scopes + client_id, so "
            "the picker's tile and the connect payload stay aligned."
        ),
    ),
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: VendorRegistryService = Depends(get_vendor_registry_service),
) -> VendorAuthCapabilitiesResponse:
    """Full auth capabilities for one vendor — flows, scopes, classifications.

    Never returns client_secret (authorization-code flow's secret is stripped
    at response build time). ``UnknownVendorError`` maps to a 404 problem
    detail via the handler registered in ``control/web/app.py``.
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
