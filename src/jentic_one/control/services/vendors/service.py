"""Vendor auth registry service.

Two fully-independent parallel entrypoints to user SSO:

* **Admin-registered OAuth apps** — rows in ``oauth_app_registrations``,
  created by an admin via the credentials Add dialog with the "Available to
  everyone in the organization" toggle. Each row is self-describing: admin
  picks the target catalog API at registration time, so the resulting
  credential's ``catalog_api_id`` matches a real registered API and the
  operations preview on the rules page works out of the box.
* **Platform-shipped vendor config** — entries under
  ``AppConfig.vendors.entries``. Baked into the deployment.

The two tiers never merge. An admin registration and a config entry for the
same underlying vendor family surface as **separate picker cards**; users
pick one, and every subsequent read (auth-capabilities, scope catalog,
flow selection, confirm-time scope validation) resolves against that
specific source alone.
"""

from __future__ import annotations

from typing import Protocol

import structlog
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

_logger = structlog.get_logger(__name__)

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

    async def _resolve_pinned_entry(
        self,
        vendor_key: str,
        *,
        registration_id: str | None = None,
        flow_kind: str | None = None,
    ) -> tuple[VendorAuthConfig, VendorEntrySource]:
        """Resolve one vendor with source metadata, honouring an optional pin.

        Admin registrations and platform config entries are peers, never
        overlays: neither path crosses over.

        * ``registration_id`` set → the registration wins outright. Fetch that
          specific row; fail with :class:`InvalidOAuthAppRegistrationError`
          when it is missing, inactive, or its ``api_vendor`` does not match
          ``vendor_key``. The projection reads registration fields only —
          the config-shipped scope catalog / identity probe is **never**
          merged in.
        * ``registration_id`` unset → prefer the "most-recently-updated
          active" registration for the vendor slug; else fall back to the
          matching config entry; else :class:`UnknownVendorError`. Same
          no-merge posture on the DB branch.
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
            return (_project_registration_to_auth_config(registration, self._ctx), "db")

        registration = await self._registrations.get_preferred_active(
            api_vendor=vendor_key, flow_kind=flow_kind
        )
        if registration is not None:
            return _project_registration_to_auth_config(registration, self._ctx), "db"

        cfg = self._config.entries.get(vendor_key)
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

        Admin registrations and config entries are peers: each active
        registration becomes its own :class:`VendorAuthConfig`, and every
        config entry always surfaces alongside — a vendor with both surfaces
        as multiple entries. Sorted by display_name so the UI's picker does
        not shuffle between polls.
        """
        registrations = await self._registrations.list_active()
        result: list[VendorAuthConfig] = [
            _project_registration_to_auth_config(row, self._ctx) for row in registrations
        ]
        result.extend(self._config.entries.values())
        return sorted(result, key=lambda v: v.display_name)

    async def list_entries(self) -> list[VendorEntry]:
        """List every vendor as a compact ``VendorEntry`` view.

        One entry per active DB registration — two admin-registered OAuth
        apps for the same vendor slug surface as two picker cards. Config
        entries also surface, side-by-side; the two tiers never dedupe.
        """
        registrations = await self._registrations.list_active()
        entries: list[VendorEntry] = [
            _project_db_registration(row.api_vendor, row) for row in registrations
        ]
        for key, cfg in self._config.entries.items():
            entries.append(_project_config_entry(key, cfg))
        return sorted(entries, key=lambda e: (e.display_name, e.name))

    async def get_entry(
        self,
        vendor_key: str,
        *,
        flow_kind: str | None = None,
    ) -> VendorEntry:
        """Compact ``VendorEntry`` view for one vendor.

        Kept for callers that specifically want the source-tagged projection
        (e.g. the admin UI). Prefers the "preferred active" DB registration;
        otherwise falls back to the config entry. Neither tier merges.
        """
        registration = await self._registrations.get_preferred_active(
            api_vendor=vendor_key, flow_kind=flow_kind
        )
        if registration is not None:
            return _project_db_registration(vendor_key, registration)
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


def _project_registration_to_auth_config(
    registration: OAuthAppRegistration,
    ctx: Context,
) -> VendorAuthConfig:
    """Build a ``VendorAuthConfig`` from a DB registration — standalone.

    Registration fields *only* — the platform config is never consulted here.
    The ``vendor`` string on the returned config is the admin-picked
    ``catalog_api_id``, which feeds ``credential.catalog_api_id`` at connect
    time and makes the operations preview resolve against a real registered
    API.

    Registrations created before the decouple refactor may not carry
    ``catalog_api_id`` — those degrade to a synthesized ``<slug>/<slug>``
    placeholder and log a warning. Users can still connect through them; the
    operations preview just won't resolve until an admin re-creates or edits
    the registration to pick a real API.

    Scopes come off the registration's ``default_scopes`` extension column,
    with every entry defaulted-on and classified ``read`` (no separate
    classification catalog on the DB side). ``identity_probe`` is always
    ``None`` on admin registrations — identity echo is a platform-config
    concern; DB-only vendors skip that step at connect finalise and land
    the credential with ``connected_as=None``.
    """
    flow = _synthesize_flow(registration, ctx)

    default_scopes = _extension_default_scopes(registration)
    scopes = [
        VendorScopeConfig(name=s, classification="read", default=True, description="")
        for s in (default_scopes or [])
    ]

    if registration.catalog_api_id is not None:
        canonical_vendor = registration.catalog_api_id
    else:
        raw = registration.api_vendor
        canonical_vendor = raw if "/" in raw else f"{raw}/{raw}"
        _logger.warning(
            "oauth_app_registration.missing_catalog_api_id",
            registration_id=registration.id,
            api_vendor=raw,
            fallback_vendor=canonical_vendor,
            hint="operations preview will not resolve until an admin picks a "
            "catalog API for this registration",
        )

    return VendorAuthConfig(
        vendor=canonical_vendor,
        display_name=registration.display_name or registration.name,
        flows=[flow],
        scopes=scopes,
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
) -> VendorEntry:
    """Project a DB registration into the compact ``VendorEntry`` view.

    Registration-only shape: ``display_name`` reads off the admin-supplied
    vendor family label (falling back to the registration's ``api_vendor``
    for pre-refactor rows that don't have one). No config merge.
    """
    default_scopes = _extension_default_scopes(registration)
    flow_kind: VendorFlowKind = _cast_flow_kind(registration.flow_kind)
    family_display = registration.display_name or registration.api_vendor
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
