"""Vendor auth registry service.

Single unified read seam over the vendor registry: every method is async and
resolves DB-first, falling back to the platform-shipped
``AppConfig.vendors`` snapshot. When a vendor slug exists in both tiers, the
DB row's OAuth-app material wins; the config's scope catalog + identity probe
+ canonical ``<domain>/<sub>`` vendor string are merged in.

Callers no longer have to reason about which method reads which tier — every
read hits both.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import SecretStr

from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.repos.oauth_app_registration_repo import (
    OAuthAppRegistrationRepository,
)
from jentic_one.control.services.integrations.errors import (
    InvalidOAuthAppRegistrationError,
)
from jentic_one.control.services.vendors.errors import (
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
)
from jentic_one.control.services.vendors.schemas import (
    ResolvedScope,
    VendorEntry,
    VendorEntrySource,
    VendorFlowKind,
)
from jentic_one.shared.config import (
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorFlowConfig,
    VendorRegistryConfig,
    VendorScopeConfig,
)
from jentic_one.shared.context import Context

# Re-exported for backwards compatibility — earlier revisions of this module
# defined the exceptions and ``ResolvedScope`` inline, and downstream code
# still imports them from here.
__all__ = [
    "ResolvedScope",
    "UnknownVendorError",
    "UnsupportedFlowError",
    "VendorAppRegistrationSource",
    "VendorEntry",
    "VendorNotConfiguredError",
    "VendorRegistryService",
]


class VendorAppRegistrationSource(Protocol):
    """Abstraction over the admin-DB registration read path.

    Injectable so unit tests can exercise DB-first / fallback / union
    semantics without opening a real database session — a fake implementation
    returns preconfigured registrations. The default implementation
    (``_DefaultRegistrationSource`` below) uses the control DB and the
    repository layer.
    """

    async def get_preferred_active(
        self, *, api_vendor: str, flow_kind: str | None = None
    ) -> OAuthAppRegistration | None:
        """Return the most-recently-updated active registration for a vendor, if any."""

    async def list_active(self) -> list[OAuthAppRegistration]:
        """Return every active registration across all vendors."""

    async def get_by_id(self, registration_id: str) -> OAuthAppRegistration | None:
        """Return the specific registration by id (regardless of active state)."""


class _DefaultRegistrationSource:
    """DB-backed implementation of :class:`VendorAppRegistrationSource`.

    Opens a read-only session for each call and delegates to the repository —
    the service layer does not import SQLAlchemy directly.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def get_preferred_active(
        self, *, api_vendor: str, flow_kind: str | None = None
    ) -> OAuthAppRegistration | None:
        async with self._ctx.control_db.session() as session:
            return await OAuthAppRegistrationRepository.get_preferred_for_vendor(
                session, api_vendor=api_vendor, flow_kind=flow_kind
            )

    async def list_active(self) -> list[OAuthAppRegistration]:
        async with self._ctx.control_db.session() as session:
            return await OAuthAppRegistrationRepository.list_all(session, include_inactive=False)

    async def get_by_id(self, registration_id: str) -> OAuthAppRegistration | None:
        async with self._ctx.control_db.session() as session:
            return await OAuthAppRegistrationRepository.get_by_id(session, registration_id)


class VendorRegistryService:
    """Vendor auth registry with a DB-first / config-fallback resolution seam."""

    def __init__(
        self,
        ctx: Context,
        *,
        registration_source: VendorAppRegistrationSource | None = None,
    ) -> None:
        self._ctx = ctx
        self._registrations: VendorAppRegistrationSource = (
            registration_source
            if registration_source is not None
            else _DefaultRegistrationSource(ctx)
        )

    # ---- config accessor ----------------------------------------------------

    @property
    def _config(self) -> VendorRegistryConfig:
        return self._ctx.config.vendors

    # ---- core resolution ----------------------------------------------------

    async def _resolve_entry(
        self,
        vendor_key: str,
        *,
        flow_kind: str | None = None,
    ) -> tuple[VendorAuthConfig, VendorEntrySource]:
        """Resolve one vendor with source metadata attached.

        Thin wrapper over :meth:`_resolve_pinned_entry` for the un-pinned
        (preferred-active) lookup. Kept as its own name for readability at
        the older call sites.
        """
        return await self._resolve_pinned_entry(vendor_key, flow_kind=flow_kind)

    async def _resolve_pinned_entry(
        self,
        vendor_key: str,
        *,
        registration_id: str | None = None,
        flow_kind: str | None = None,
    ) -> tuple[VendorAuthConfig, VendorEntrySource]:
        """Resolve one vendor with source metadata, honouring an optional pin.

        When ``registration_id`` is set: fetch that specific registration; fail
        with :class:`InvalidOAuthAppRegistrationError` when it is missing,
        inactive, or its ``api_vendor`` does not match ``vendor_key``. Silent
        fall-through would surface the wrong OAuth-app's scopes / client_id.

        When ``registration_id`` is None: today's DB-first-with-config-fallback
        — the "preferred active" registration for the vendor wins, else the
        matching config entry wins, else :class:`UnknownVendorError`.
        """
        if registration_id is not None:
            registration = await self._registrations.get_by_id(registration_id)
            if registration is None:
                raise InvalidOAuthAppRegistrationError(registration_id, "not found")
            if not registration.is_active:
                raise InvalidOAuthAppRegistrationError(registration_id, "inactive")
            if registration.api_vendor != vendor_key:
                raise InvalidOAuthAppRegistrationError(
                    registration_id,
                    f"api_vendor mismatch (expected {vendor_key!r}, "
                    f"registration is {registration.api_vendor!r})",
                )
            cfg = self._config.entries.get(vendor_key)
            return (
                _synthesize_from_registration(vendor_key, registration, cfg, self._ctx),
                "db",
            )

        registration = await self._registrations.get_preferred_active(
            api_vendor=vendor_key, flow_kind=flow_kind
        )
        cfg = self._config.entries.get(vendor_key)

        if registration is not None:
            return _synthesize_from_registration(vendor_key, registration, cfg, self._ctx), "db"
        if cfg is not None:
            return cfg, "config"
        raise UnknownVendorError(vendor_key)

    async def resolve_by_pin(
        self,
        vendor_key: str,
        *,
        registration_id: str | None = None,
        flow_kind: str | None = None,
    ) -> VendorAuthConfig:
        """Single seam for every vendor read.

        Callers that know which admin-registered OAuth app the user picked
        (``:connect``, auth-capabilities, confirm-time scope validation)
        pass ``registration_id`` so the returned config's scopes + client
        material come from *that* row. Callers with no pin fall back to the
        DB-first / config-fallback behaviour.
        """
        entry, _source = await self._resolve_pinned_entry(
            vendor_key, registration_id=registration_id, flow_kind=flow_kind
        )
        return entry

    async def get(
        self,
        vendor_key: str,
        *,
        registration_id: str | None = None,
    ) -> VendorAuthConfig:
        """Look up a vendor entry by registry key (e.g. ``"github"``).

        Thin shim over :meth:`resolve_by_pin`. Kept so older call sites that
        don't know about the pin (e.g. list-time display-name lookups) can
        stay compact.
        """
        return await self.resolve_by_pin(vendor_key, registration_id=registration_id)

    async def list_all(self) -> list[VendorAuthConfig]:
        """List every vendor known to the platform.

        Every active DB registration becomes its own :class:`VendorAuthConfig`;
        a vendor that has ≥1 DB registration hides its config-shipped entry
        (DB replaces config, matching the single-vendor ``get`` /
        ``resolve_flow`` semantics). Sorted by display_name so the UI's
        picker does not shuffle between polls.
        """
        registrations = await self._registrations.list_active()
        vendors_with_db = {row.api_vendor for row in registrations}
        result: list[VendorAuthConfig] = [
            _synthesize_from_registration(
                row.api_vendor,
                row,
                self._config.entries.get(row.api_vendor),
                self._ctx,
            )
            for row in registrations
        ]
        for key, cfg in self._config.entries.items():
            if key not in vendors_with_db:
                result.append(cfg)
        return sorted(result, key=lambda v: v.display_name)

    async def list_entries(self) -> list[VendorEntry]:
        """List every vendor as a compact ``VendorEntry`` view.

        One entry per active DB registration — two admin-registered OAuth
        apps for the same vendor slug surface as two picker cards. Vendors
        with ≥1 DB registration hide their config-shipped entry.
        """
        registrations = await self._registrations.list_active()
        vendors_with_db = {row.api_vendor for row in registrations}
        entries: list[VendorEntry] = [
            _project_db_registration(
                row.api_vendor,
                row,
                cfg=self._config.entries.get(row.api_vendor),
            )
            for row in registrations
        ]
        for key, cfg in self._config.entries.items():
            if key not in vendors_with_db:
                entries.append(_project_config_entry(key, cfg))
        return sorted(entries, key=lambda e: (e.display_name, e.name))

    async def get_entry(
        self,
        vendor_key: str,
        *,
        flow_kind: str | None = None,
    ) -> VendorEntry:
        """Compact ``VendorEntry`` view for one vendor.

        Kept as a thin wrapper over :meth:`_resolve_entry` for callers that
        specifically want the source-tagged projection (e.g. the admin UI).
        """
        registration = await self._registrations.get_preferred_active(
            api_vendor=vendor_key, flow_kind=flow_kind
        )
        if registration is not None:
            return _project_db_registration(
                vendor_key,
                registration,
                cfg=self._config.entries.get(vendor_key),
            )
        cfg = self._config.entries.get(vendor_key)
        if cfg is None:
            raise UnknownVendorError(vendor_key)
        return _project_config_entry(vendor_key, cfg)

    # ---- flow resolution -----------------------------------------------------

    async def resolve_flow(
        self,
        vendor_key: str,
        preferred: str | None = None,
        *,
        registration_id: str | None = None,
    ) -> VendorFlowConfig:
        """Pick a flow for a connect request, DB-first with optional pin.

        Precedence: preferred (if supplied and offered by the vendor) > first
        entry in `vendor.flows`. Raises `UnsupportedFlowError` if a preferred
        flow is not offered by the vendor. When ``registration_id`` is set,
        the returned flow's client material comes from that specific
        registration (see :meth:`resolve_by_pin`).

        The returned ``VendorFlowConfig`` carries the resolved client
        material — ``client_secret`` is materialised for auth-code flows so
        the connect handler can POST the token exchange directly.
        """
        entry, _source = await self._resolve_pinned_entry(
            vendor_key, registration_id=registration_id, flow_kind=preferred
        )
        if not entry.flows:
            raise VendorNotConfiguredError(vendor_key, "any", "no flows configured")
        if preferred is None:
            flow = entry.flows[0]
            self._require_flow_ready(vendor_key, flow)
            return flow
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

    async def merge_scopes(
        self,
        vendor_key: str,
        requested: list[str] | None,
        *,
        registration_id: str | None = None,
    ) -> list[ResolvedScope]:
        """Merge requested scopes with the vendor's defaults.

        Returns every scope offered by the vendor, with:
        - `default` = the scope is marked default (pre-selected)
        - `requested` = the initiator asked for this scope explicitly

        The review page renders a checkbox per scope; write scopes that were
        agent-requested get visually flagged. DB-only vendors carry an empty
        scope catalog until an operator adds a matching config entry. When
        ``registration_id`` is set the scopes come from that specific
        registration — matches what the user saw at pick time.
        """
        entry = await self.resolve_by_pin(vendor_key, registration_id=registration_id)
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

    async def validate_scopes(
        self,
        vendor_key: str,
        scopes: list[str],
        *,
        registration_id: str | None = None,
    ) -> list[str]:
        """Return scopes not offered by the vendor (empty list = all valid)."""
        entry = await self.resolve_by_pin(vendor_key, registration_id=registration_id)
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


# ---------------------------------------------------------------------------
# View projection helpers (module-level so the service reads compactly).
# ---------------------------------------------------------------------------


def _synthesize_from_registration(
    vendor_key: str,
    registration: OAuthAppRegistration,
    cfg: VendorAuthConfig | None,
    ctx: Context,
) -> VendorAuthConfig:
    """Build a ``VendorAuthConfig`` from a DB registration, merging config metadata.

    When a matching config entry exists, its scope catalog + identity probe +
    canonical vendor string come along for free; the DB row supplies the
    OAuth-app material (client_id / secret / endpoints / display_name).

    When the DB row has no matching config, a minimal ``VendorAuthConfig`` is
    synthesized — the vendor string is derived as ``<slug>/<slug>`` to
    satisfy the pydantic validator, scopes default to empty, and
    ``identity_probe`` is left ``None`` so the connect finalise step skips
    identity-echo (the credential still stores; just no ``connected_as`` UX
    label). Operators wanting the identity echo for a fully-custom vendor
    should still add a matching entry in ``AppConfig.vendors.entries`` with
    the vendor's userinfo endpoint.
    """
    flow = _synthesize_flow(registration, ctx)

    if cfg is not None:
        return VendorAuthConfig(
            vendor=cfg.vendor,
            display_name=registration.name,
            flows=[flow],
            scopes=list(cfg.scopes),
            identity_probe=cfg.identity_probe,
        )

    default_scopes = _extension_default_scopes(registration)
    synthesized_scopes = [
        VendorScopeConfig(name=s, classification="read", default=True, description="")
        for s in (default_scopes or [])
    ]
    canonical_vendor = vendor_key if "/" in vendor_key else f"{vendor_key}/{vendor_key}"
    return VendorAuthConfig(
        vendor=canonical_vendor,
        display_name=registration.name,
        flows=[flow],
        scopes=synthesized_scopes,
        identity_probe=None,
    )


def _synthesize_flow(
    registration: OAuthAppRegistration,
    ctx: Context,
) -> VendorFlowConfig:
    """Project the flow-kind-specific extension row into a ``VendorFlowConfig``.

    For auth-code the encrypted client secret is decrypted via
    ``ctx.encryption.decrypt`` — this is the one materialisation site for the
    secret. Device flow has no secret (public client).
    """
    if registration.flow_kind == "authorization_code":
        ac = registration.authorization_code_details
        if ac is None:
            raise VendorNotConfiguredError(
                registration.api_vendor,
                registration.flow_kind,
                "authorization_code registration is missing its details row",
            )
        client_secret = ctx.encryption.decrypt(ac.encrypted_client_secret)
        return VendorAuthorizationCodeFlowConfig(
            client_id=registration.client_id,
            client_secret=SecretStr(client_secret),
            authorize_url=ac.authorize_url,
            token_url=ac.token_url,
        )
    if registration.flow_kind == "device_authorization":
        dev = registration.device_authorization_details
        if dev is None:
            raise VendorNotConfiguredError(
                registration.api_vendor,
                registration.flow_kind,
                "device_authorization registration is missing its details row",
            )
        return VendorDeviceAuthorizationFlowConfig(
            client_id=registration.client_id,
            authorization_endpoint=dev.authorization_endpoint,
            token_endpoint=dev.token_endpoint,
        )
    raise UnsupportedFlowError(registration.api_vendor, registration.flow_kind)


def _extension_default_scopes(registration: OAuthAppRegistration) -> list[str] | None:
    """Read ``default_scopes`` off whichever extension is populated."""
    ac = registration.authorization_code_details
    if ac is not None and ac.default_scopes is not None:
        return list(ac.default_scopes)
    dev = registration.device_authorization_details
    if dev is not None and dev.default_scopes is not None:
        return list(dev.default_scopes)
    return None


def _project_db_registration(
    key: str,
    registration: OAuthAppRegistration,
    *,
    cfg: VendorAuthConfig | None = None,
) -> VendorEntry:
    """Project a DB registration into the compact ``VendorEntry`` view.

    ``cfg`` supplies the vendor's family display name when a matching
    config entry exists — the picker uses that as the subtitle when it
    differs from the admin's registration name.
    """
    default_scopes = _extension_default_scopes(registration)
    flow_kind: VendorFlowKind = _cast_flow_kind(registration.flow_kind)
    family_display = cfg.display_name if cfg is not None else registration.name
    return VendorEntry(
        entry_id=registration.id,
        registration_id=registration.id,
        key=key,
        display_name=family_display,
        name=registration.name,
        flow_kind=flow_kind,
        client_id=registration.client_id,
        has_client_secret=registration.authorization_code_details is not None,
        default_scopes=default_scopes,
        source="db",
    )


def _project_config_entry(key: str, cfg: VendorAuthConfig) -> VendorEntry:
    """Project a config entry into the compact ``VendorEntry`` view.

    Config vendors may offer multiple flows — this collapses to the first
    entry, mirroring ``resolve_flow``'s precedence for callers that ask for
    the vendor's default flow.
    """
    flow = cfg.flows[0]
    flow_kind: VendorFlowKind = _cast_flow_kind(flow.kind)
    has_secret = flow.kind == "authorization_code"
    default_scopes = [s.name for s in cfg.scopes if s.default] or None
    return VendorEntry(
        entry_id=key,
        registration_id=None,
        key=key,
        display_name=cfg.display_name,
        name=cfg.display_name,
        flow_kind=flow_kind,
        client_id=flow.client_id,
        has_client_secret=has_secret,
        default_scopes=default_scopes,
        source="config",
    )


def _cast_flow_kind(raw: str) -> VendorFlowKind:
    """Narrow a stringly-typed DB / config flow discriminator."""
    if raw not in ("authorization_code", "device_authorization"):
        raise UnsupportedFlowError("<registration>", raw)
    return raw  # type: ignore[return-value]
