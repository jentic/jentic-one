"""Domain exceptions for the vendor auth registry service."""

from __future__ import annotations


class UnknownVendorError(Exception):
    """Raised when the requested vendor is not in the registry."""

    def __init__(self, vendor: str) -> None:
        super().__init__(f"unknown vendor: {vendor!r}")
        self.vendor = vendor


class UnsupportedFlowError(Exception):
    """Raised when the requested flow kind is not offered by the vendor."""

    def __init__(self, vendor: str, flow: str) -> None:
        super().__init__(f"vendor {vendor!r} does not support flow {flow!r}")
        self.vendor = vendor
        self.flow = flow


class VendorNotConfiguredError(Exception):
    """Raised when the vendor is in the registry but its flow lacks credentials."""

    def __init__(self, vendor: str, flow: str, reason: str) -> None:
        super().__init__(f"vendor {vendor!r} flow {flow!r} not configured: {reason}")
        self.vendor = vendor
        self.flow = flow
        self.reason = reason


class AmbiguousVendorError(Exception):
    """Raised when an unpinned connect matches more than one OAuth app.

    Registrations for the same ``api_vendor`` can target different catalog
    APIs (Gmail and Calendar both sit under ``googleapis.com``), so picking
    one silently would mint the credential through an app the caller didn't
    choose. The caller must pin ``oauth_app_registration_id``.
    """

    def __init__(self, vendor: str, flow: str | None, registration_ids: list[str]) -> None:
        flow_part = f" (flow {flow!r})" if flow else ""
        super().__init__(
            f"vendor {vendor!r}{flow_part} matches {len(registration_ids)} OAuth apps; "
            "pin oauth_app_registration_id to choose one"
        )
        self.vendor = vendor
        self.flow = flow
        self.registration_ids = registration_ids
