"""Resolve the connect target a missing-credential denial suggests.

Both missing-credential denials (the 403 ``no_credential_binding`` and the 424
``credential_not_provisioned``) tell the agent which connect key — and, for a
single matching shared OAuth app, which registration — would provision the
denied API. The lookup runs here, before the denial is built, because the
directive builders are synchronous.
"""

from __future__ import annotations

import structlog

from jentic_one.broker.repos.connectable_registrations import ConnectableRegistrationReader
from jentic_one.shared.access_guidance import ConnectTarget, connect_target, connect_vendor_key
from jentic_one.shared.broker.protocols import (
    ConnectableRegistration,
    ConnectableRegistrationSourceProtocol,
)
from jentic_one.shared.cli_profiles import declaring_profile, load_profiles
from jentic_one.shared.context import Context
from jentic_one.shared.schemas import APIReference

logger = structlog.get_logger(__name__)


def _profile_connect_key(ctx: Context, api: APIReference) -> str | None:
    """The profile connect covering ``api`` when a ``profile_scoped`` CLI profile declares it.

    One connect then covers every API of the profile (``jentic connect github``
    for the GitHub git API too), so it is suggested ahead of a per-API entry.
    Only a key the deployment configures is suggested.
    """
    if not (api.vendor and api.name and api.version):
        return None
    name = declaring_profile(api.vendor, api.name, api.version)
    profile = load_profiles().get(name) if name is not None else None
    if profile is None or not profile.profile_scoped or profile.connect is None:
        return None
    key = profile.connect.vendor_entry
    return key if key in ctx.config.vendors.entries else None


async def resolve_connect_target(
    ctx: Context,
    api: APIReference,
    *,
    registrations: ConnectableRegistrationSourceProtocol | None = None,
) -> ConnectTarget | None:
    """The connect target covering ``api``, or ``None`` when nothing connectable does.

    A covering config entry answers without I/O. Otherwise the active shared
    OAuth-app registrations are read (``registrations``, defaulting to the
    control-DB reader). A failed read degrades to the operator-only guidance:
    the denial itself must still reach the agent.
    """
    key = _profile_connect_key(ctx, api)
    if key is None:
        key = connect_vendor_key(
            ctx.config.vendors, vendor=api.vendor, name=api.name, version=api.version
        )
    if key is not None:
        return ConnectTarget(vendor_key=key)
    source = (
        registrations
        if registrations is not None
        else ConnectableRegistrationReader(ctx.control_db)
    )
    rows: tuple[ConnectableRegistration, ...] = ()
    try:
        rows = await source.list_active()
    except Exception:
        logger.warning("connect_target_lookup_failed", vendor=api.vendor, exc_info=True)
    return connect_target(
        ctx.config.vendors, rows, vendor=api.vendor, name=api.name, version=api.version
    )
