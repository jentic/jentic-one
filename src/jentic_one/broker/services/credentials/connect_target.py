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
from jentic_one.shared.context import Context
from jentic_one.shared.schemas import APIReference

logger = structlog.get_logger(__name__)


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
