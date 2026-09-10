"""Vendor auth registry read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from jentic_one.control.services.vendors.service import (
    UnknownVendorError,
    VendorRegistryService,
)
from jentic_one.control.web.deps import get_vendor_registry_service
from jentic_one.control.web.schemas.integrations import (
    VendorAuthCapabilitiesResponse,
    VendorFlowResponse,
    VendorListResponse,
    VendorScopeResponse,
    VendorSummaryResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import VendorAuthConfig
from jentic_one.shared.web import get_current_identity

router = APIRouter(tags=["vendors"])


@router.get("/vendors", summary="List verified vendors")
async def list_vendors(
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: VendorRegistryService = Depends(get_vendor_registry_service),
) -> VendorListResponse:
    """Public metadata for every vendor in the config-seeded registry.

    Used by the UI's "Add integration" picker. Never returns secrets.
    """
    entries = svc.list_all()
    return VendorListResponse(
        data=[
            VendorSummaryResponse(
                key=_key_for(svc, e),
                vendor=e.vendor,
                display_name=e.display_name,
                flow_kinds=[f.kind for f in e.flows],
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
    identity: Identity = get_current_identity(required_permissions=["capabilities:read"]),
    svc: VendorRegistryService = Depends(get_vendor_registry_service),
) -> VendorAuthCapabilitiesResponse | JSONResponse:
    """Full auth capabilities for one vendor — flows, scopes, classifications.

    Never returns client_secret (authorization-code flow's secret is stripped
    at response build time).
    """
    try:
        entry = svc.get(vendor_key)
    except UnknownVendorError:
        return JSONResponse(status_code=404, content={"detail": f"unknown vendor: {vendor_key!r}"})
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


def _key_for(svc: VendorRegistryService, entry: VendorAuthConfig) -> str:
    """Look up the registry key (dict key) for a given VendorAuthConfig entry."""
    for key, cfg in svc._config.entries.items():
        if cfg is entry:
            return key
    return entry.vendor
