"""Vendor auth registry service.

Read-only view over `AppConfig.vendors` — the config-seeded set of verified
vendors that support the agent-driven integration flow.
Phase 1 makes no runtime writes; the whole registry is a config snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorFlowConfig,
    VendorRegistryConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context


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


@dataclass(slots=True, frozen=True)
class ResolvedScope:
    """A scope resolved for a specific connect request.

    `default` = pre-selected on the review page (typically the read-only baseline).
    `requested` = the initiator asked for this scope (agents flag write scopes for
    human attention).
    """

    name: str
    classification: Literal["read", "write", "admin"]
    default: bool
    requested: bool
    description: str


class VendorRegistryService:
    """Read-only vendor auth registry backed by `AppConfig.vendors`."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    # ---- config accessors ----------------------------------------------------

    @property
    def _config(self) -> VendorRegistryConfig:
        return self._ctx.config.vendors

    def list_all(self) -> list[VendorAuthConfig]:
        """List every configured vendor (stable order for UI rendering)."""
        return sorted(self._config.entries.values(), key=lambda v: v.display_name)

    def get(self, vendor_key: str) -> VendorAuthConfig:
        """Look up a vendor entry by registry key (e.g. "github")."""
        entry = self._config.entries.get(vendor_key)
        if entry is None:
            raise UnknownVendorError(vendor_key)
        return entry

    # ---- flow resolution -----------------------------------------------------

    def resolve_flow(
        self,
        vendor_key: str,
        preferred: str | None = None,
    ) -> VendorFlowConfig:
        """Pick a flow for a connect request.

        Precedence: preferred (if supplied and offered by the vendor) > first
        entry in `vendor.flows`. Raises `UnsupportedFlowError` if a preferred
        flow is not offered by the vendor.
        """
        entry = self.get(vendor_key)
        if not entry.flows:
            raise VendorNotConfiguredError(vendor_key, "any", "no flows configured")
        if preferred is None:
            return entry.flows[0]
        for flow in entry.flows:
            if flow.kind == preferred:
                self._require_flow_ready(vendor_key, flow)
                return flow
        raise UnsupportedFlowError(vendor_key, preferred)

    @staticmethod
    def _require_flow_ready(vendor_key: str, flow: VendorFlowConfig) -> None:
        """Fail loudly if the operator forgot to fill in required flow secrets.

        Device flow needs a client_id; without it every begin_connect call
        would silently 400 at the vendor.
        """
        if flow.kind == "device_authorization" and not flow.client_id:
            raise VendorNotConfiguredError(
                vendor_key,
                flow.kind,
                "client_id is empty — set vendors.entries.<vendor>.flows[0].client_id",
            )

    # ---- scope resolution ----------------------------------------------------

    def merge_scopes(
        self,
        vendor_key: str,
        requested: list[str] | None,
    ) -> list[ResolvedScope]:
        """Merge requested scopes with the vendor's defaults.

        Returns every scope offered by the vendor, with:
        - `default` = the scope is marked default (pre-selected)
        - `requested` = the initiator asked for this scope explicitly

        The review page renders a checkbox per scope; write scopes that were
        agent-requested get visually flagged.
        """
        entry = self.get(vendor_key)
        requested_set = set(requested or [])
        return [
            ResolvedScope(
                name=s.name,
                classification=s.classification,
                default=s.default,
                requested=s.name in requested_set,
                description=s.description,
            )
            for s in entry.scopes
        ]

    def validate_scopes(self, vendor_key: str, scopes: list[str]) -> list[str]:
        """Return scopes not offered by the vendor (empty list = all valid)."""
        entry = self.get(vendor_key)
        offered = {s.name for s in entry.scopes}
        return [s for s in scopes if s not in offered]

    # ---- convenience for the resulting credential ----------------------------

    @staticmethod
    def scope_config(entry: VendorAuthConfig, name: str) -> VendorScopeConfig | None:
        """Look up a scope config by name on an already-loaded vendor entry."""
        for s in entry.scopes:
            if s.name == name:
                return s
        return None
