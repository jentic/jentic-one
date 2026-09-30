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

Picker cards for registrations carry the registration id as a pin. With no
pin, ``:connect`` uses the config entry when it offers the requested flow,
else the vendor's single matching active registration (ambiguous → 400).
After ``:connect`` the credential's ``oauth_app_registration_id`` records the
source (NULL = config) and later steps re-open exactly that source.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    AmbiguousVendorError,
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
    "AmbiguousVendorError",
    "ResolvedScope",
    "ResolvedVendorSource",
    "UnknownVendorError",
    "UnsupportedFlowError",
    "VendorAppRegistrationSource",
    "VendorEntry",
    "VendorNotConfiguredError",
    "VendorRegistryService",
]


class VendorAppRegistrationSource(Protocol):
    """Abstraction over the admin-DB registration read path.

    Injectable so unit tests can exercise resolution semantics without
    opening a real database session — a fake implementation returns
    preconfigured registrations. The default implementation
    (``_DefaultRegistrationSource`` below) uses the control DB and the
    repository layer.
    """

    async def list_active_for_vendor(
        self, *, api_vendor: str, flow_kind: str | None = None
    ) -> list[OAuthAppRegistration]:
        """Return every active registration for a vendor slug (optionally one flow kind)."""

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

    async def list_active_for_vendor(
        self, *, api_vendor: str, flow_kind: str | None = None
    ) -> list[OAuthAppRegistration]:
        async with self._ctx.control_db.session() as session:
            return await OAuthAppRegistrationRepository.list_active_for_vendor(
                session, api_vendor=api_vendor, flow_kind=flow_kind
            )

    async def list_active(self) -> list[OAuthAppRegistration]:
        async with self._ctx.control_db.session() as session:
            return await OAuthAppRegistrationRepository.list_all(session, include_inactive=False)

    async def get_by_id(self, registration_id: str) -> OAuthAppRegistration | None:
        async with self._ctx.control_db.session() as session:
            return await OAuthAppRegistrationRepository.get_by_id(session, registration_id)


@dataclass(frozen=True, slots=True)
class ResolvedVendorSource:
    """One OAuth-app source for a vendor, with the flow chosen from it.

    ``registration`` is the admin-registered row the entry and flow were
    projected from, or ``None`` when the source is the platform config entry.
    Entry, flow and registration always come from the same source.
    """

    entry: VendorAuthConfig
    flow: VendorFlowConfig
    registration: OAuthAppRegistration | None

    @property
    def source(self) -> VendorEntrySource:
        return "config" if self.registration is None else "db"


class VendorRegistryService:
    """Vendor auth registry: admin registrations and platform config as peers."""

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

    async def _pinned_registration(
        self, vendor_key: str, registration_id: str
    ) -> OAuthAppRegistration:
        """Fetch a pinned registration, refusing missing / inactive / wrong-vendor rows."""
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
        return registration

    def _config_entry(self, vendor_key: str) -> VendorAuthConfig:
        cfg = self._config.entries.get(vendor_key)
        if cfg is None:
            raise UnknownVendorError(vendor_key)
        return cfg

    async def resolve_connect_source(
        self,
        vendor_key: str,
        *,
        registration_id: str | None = None,
        preferred_flow: str | None = None,
    ) -> ResolvedVendorSource:
        """Choose the single source a new connect runs through.

        * ``registration_id`` set → that registration, validated
          (:class:`InvalidOAuthAppRegistrationError` when missing, inactive,
          or for another vendor).
        * No pin, and the platform config has ``vendor_key`` offering
          ``preferred_flow`` → the config entry. Config picker cards send no
          pin, so a registration sharing the slug never takes them over.
        * Otherwise the active registrations for the slug offering
          ``preferred_flow``: exactly one → it; more than one →
          :class:`AmbiguousVendorError` (the caller must pin); none →
          :class:`UnsupportedFlowError` if the config entry exists but lacks
          the flow, else :class:`UnknownVendorError`.

        Entry, flow and registration are resolved together, so the
        credential's identity and the minting app can never diverge.
        """
        if registration_id is not None:
            registration = await self._pinned_registration(vendor_key, registration_id)
            return self._from_registration(vendor_key, registration, preferred_flow)

        cfg = self._config.entries.get(vendor_key)
        if cfg is not None and (
            preferred_flow is None or any(f.kind == preferred_flow for f in cfg.flows)
        ):
            return ResolvedVendorSource(
                entry=cfg, flow=self._pick_flow(vendor_key, cfg, preferred_flow), registration=None
            )

        candidates = await self._registrations.list_active_for_vendor(
            api_vendor=vendor_key, flow_kind=preferred_flow
        )
        if len(candidates) > 1:
            raise AmbiguousVendorError(vendor_key, preferred_flow, [r.id for r in candidates])
        if candidates:
            return self._from_registration(vendor_key, candidates[0], preferred_flow)
        if cfg is not None and preferred_flow is not None:
            raise UnsupportedFlowError(vendor_key, preferred_flow)
        raise UnknownVendorError(vendor_key)

    async def resolve_session_source(
        self,
        vendor_key: str,
        *,
        registration_id: str | None,
        flow_kind: str | None = None,
    ) -> ResolvedVendorSource:
        """Re-open the source an existing connect session was created against.

        ``registration_id`` is the credential's ``oauth_app_registration_id``
        — stamped at ``:connect`` for every registration-backed session, so
        ``None`` unambiguously means the platform config entry. Never
        re-prefers: a registration added mid-session can't take over a
        config session, and a config entry removed mid-session raises
        :class:`UnknownVendorError` rather than falling through to a DB row.
        """
        if registration_id is not None:
            registration = await self._pinned_registration(vendor_key, registration_id)
            return self._from_registration(vendor_key, registration, flow_kind)
        cfg = self._config_entry(vendor_key)
        return ResolvedVendorSource(
            entry=cfg, flow=self._pick_flow(vendor_key, cfg, flow_kind), registration=None
        )

    async def resolve_by_pin(
        self,
        vendor_key: str,
        *,
        registration_id: str | None = None,
    ) -> VendorAuthConfig:
        """Vendor entry for a picker card: the pinned registration, else the connect default.

        Same precedence as :meth:`resolve_connect_source` with no flow
        preference, so a card's capabilities match what ``:connect`` will use.
        """
        resolved = await self.resolve_connect_source(vendor_key, registration_id=registration_id)
        return resolved.entry

    def _from_registration(
        self,
        vendor_key: str,
        registration: OAuthAppRegistration,
        flow_kind: str | None,
    ) -> ResolvedVendorSource:
        entry = _project_registration_to_auth_config(registration)
        return ResolvedVendorSource(
            entry=entry,
            flow=self._pick_flow(vendor_key, entry, flow_kind),
            registration=registration,
        )

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
            _project_registration_to_auth_config(row) for row in registrations
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

    # ---- flow resolution -----------------------------------------------------

    def _pick_flow(
        self, vendor_key: str, entry: VendorAuthConfig, preferred: str | None
    ) -> VendorFlowConfig:
        """Pick a flow off one source's entry.

        Precedence: preferred (if supplied and offered) > first entry in
        ``entry.flows``. Raises :class:`UnsupportedFlowError` if a preferred
        flow is not offered. Config-sourced auth-code flows carry their
        ``client_secret``; flows projected from a DB registration carry an
        empty placeholder — the registration secret is decrypted only at
        token exchange.
        """
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

    @staticmethod
    def merge_scopes(entry: VendorAuthConfig, requested: list[str] | None) -> list[ResolvedScope]:
        """Merge requested scopes with one source's scope catalog.

        Returns every scope offered by the source, with:
        - `default` = the scope is marked default (pre-selected)
        - `requested` = the initiator asked for this scope explicitly

        The review page renders a checkbox per scope; write scopes that were
        agent-requested get visually flagged.
        """
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

    @staticmethod
    def validate_scopes(entry: VendorAuthConfig, scopes: list[str]) -> list[str]:
        """Return scopes not offered by the source (empty list = all valid)."""
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
) -> VendorAuthConfig:
    """Build a ``VendorAuthConfig`` from a DB registration — standalone.

    Registration fields *only* — the platform config is never consulted here.
    The ``vendor`` string on the returned config is the admin-picked
    ``catalog_api_id``, which feeds ``credential.catalog_api_id`` at connect
    time and makes the operations preview resolve against a real registered
    API.

    Scopes come off the registration's ``default_scopes`` extension column,
    with every entry defaulted-on and classified ``read`` (no separate
    classification catalog on the DB side). ``identity_probe`` is always
    ``None`` on admin registrations — identity echo is a platform-config
    concern; DB-only vendors skip that step at connect finalise and land
    the credential with ``connected_as=None``.
    """
    flow = _synthesize_flow(registration)

    default_scopes = _extension_default_scopes(registration)
    scopes = [
        VendorScopeConfig(name=s, classification="read", default=True, description="")
        for s in (default_scopes or [])
    ]

    return VendorAuthConfig(
        vendor=registration.catalog_api_id,
        display_name=registration.display_name,
        flows=[flow],
        scopes=scopes,
        identity_probe=None,
    )


def _synthesize_flow(registration: OAuthAppRegistration) -> VendorFlowConfig:
    """Project the flow-kind-specific extension row into a ``VendorFlowConfig``.

    Never decrypts: vendor reads (listing, scope checks, display names) have
    no use for the auth-code client secret, so the flow carries an empty
    placeholder. The connect path re-reads the registration and decrypts
    lazily at token exchange. Device flow has no secret (public client).
    """
    if registration.flow_kind == "authorization_code":
        ac = registration.authorization_code_details
        if ac is None:
            raise VendorNotConfiguredError(
                registration.api_vendor,
                registration.flow_kind,
                "authorization_code registration is missing its details row",
            )
        return VendorAuthorizationCodeFlowConfig(
            client_id=registration.client_id,
            client_secret=SecretStr(""),
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
    vendor family label. No config merge.
    """
    default_scopes = _extension_default_scopes(registration)
    flow_kind: VendorFlowKind = _cast_flow_kind(registration.flow_kind)
    return VendorEntry(
        entry_id=registration.id,
        registration_id=registration.id,
        key=key,
        display_name=registration.display_name,
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
