"""The ``provisioning_url`` on the broker's missing-credential denials.

A 403 ``no_credential_binding`` or 424 ``credential_not_provisioned`` carries a
``provisioning_url`` only when the denied agent already has an open connect
session for the API: the URL is that session's token-less owner deep link
(``/app/agents?approve=<sid>``, the same address as its ``approval_url``), so
the agent relays it instead of opening a duplicate request. With no open
session the directive omits the field.
"""

from __future__ import annotations

import structlog

from jentic_one.broker.repos.open_connect_session import OpenConnectSessionReader
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import connect_approval_url
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

logger = structlog.get_logger(__name__)


async def open_session_provisioning_url(
    ctx: Context, *, identity: Identity, api: APIReference
) -> str | None:
    """Return the owner deep link to the agent's open connect session for ``api``.

    Only agents open connect sessions in their own name, so any other caller
    skips the lookup. Best-effort: the denial is the primary outcome, so a
    failed read logs and yields ``None`` rather than turning the 403/424 into
    a 500.
    """
    if identity.actor_type != ActorType.AGENT or not identity.sub or not api.vendor:
        return None
    try:
        session_id = await OpenConnectSessionReader(ctx.control_db).find_session_id(
            agent_id=identity.sub, vendor=api.vendor, name=api.name, version=api.version
        )
    except Exception:
        logger.warning("provisioning_url_lookup_failed", vendor=api.vendor, exc_info=True)
        return None
    if session_id is None:
        return None
    return connect_approval_url(ctx.config, session_id)
