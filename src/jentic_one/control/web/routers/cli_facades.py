"""Vendor CLI facades router — where an agent's vendor CLI is pointed.

Served by the control surface under a ``/cli-facades`` prefix distinct from the
auth surface's ``/cli-invocations``, so the split-deploy gateway can route the
two to different backends.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from jentic_one.control.services.cli_facades import CliFacadeService
from jentic_one.control.web.deps import get_cli_facade_service
from jentic_one.control.web.schemas.cli_facades import (
    CliFacadeContainerWiringResponse,
    CliFacadeListResponse,
    CliFacadeResponse,
    CliFacadeWrapperWiringResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import CAPABILITIES_READ
from jentic_one.shared.web import get_current_identity

router = APIRouter()


@router.get("/cli-facades", summary="List vendor CLI facades")
async def list_cli_facades(
    identity: Identity = get_current_identity(required_permissions=[CAPABILITIES_READ]),
    svc: CliFacadeService = Depends(get_cli_facade_service),
) -> CliFacadeListResponse:
    """The vendor CLI facades this deployment serves, and the address of each.

    `jentic cf` (and `jentic cli list`) read this to learn where to point the
    vendor's own CLI, so no agent hard-codes a facade address. Lists only
    facades that are switched on (`cli_facades.enabled` and the vendor's
    `enabled`) and implemented by the Broker. Whether the caller holds a
    credential for the vendor is not checked here: a call without one fails
    at the facade with the usual Broker problem.

    Each facade also carries the agent-side wiring from the vendor's CLI
    profile: the binary and supported versions, the environment `jentic
    <cli>` sets and strips, and what `jentic run --container` sets and
    governs, so no CLI hard-codes them.
    """
    return CliFacadeListResponse(
        data=[
            CliFacadeResponse(
                vendor=v.vendor,
                cli=v.cli,
                mode=v.mode,
                address=v.address,
                protocols=list(v.protocols),
                display_name=v.display_name,
                binary=v.cli,
                supported_range=v.supported_range,
                wrapper=(
                    CliFacadeWrapperWiringResponse(
                        env_set=dict(v.wrapper.env_set), env_strip=list(v.wrapper.env_strip)
                    )
                    if v.wrapper is not None
                    else None
                ),
                container=(
                    CliFacadeContainerWiringResponse(
                        env_set=dict(v.container.env_set),
                        env_strip=list(v.container.env_strip),
                        tunnel_hosts=list(v.container.tunnel_hosts),
                    )
                    if v.container is not None
                    else None
                ),
                governed_domains=list(v.governed_domains),
            )
            for v in svc.list_served()
        ]
    )
