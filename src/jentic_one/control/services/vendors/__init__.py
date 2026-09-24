"""Vendor auth registry service (agent-driven SSO)."""

from jentic_one.control.services.vendors.errors import (
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
)
from jentic_one.control.services.vendors.schemas import ResolvedScope, VendorEntry
from jentic_one.control.services.vendors.service import (
    VendorAppRegistrationSource,
    VendorRegistryService,
)

__all__ = [
    "ResolvedScope",
    "UnknownVendorError",
    "UnsupportedFlowError",
    "VendorAppRegistrationSource",
    "VendorEntry",
    "VendorNotConfiguredError",
    "VendorRegistryService",
]
