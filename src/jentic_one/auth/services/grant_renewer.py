"""The agents service's side of ``shared.auth.grant_renewal``.

Deciding a ``grant_renewal`` hold renews the binding through the same
:meth:`AgentService.renew_credential` the console's renew endpoint calls, so
the two can never disagree about who may renew.
"""

from __future__ import annotations

from typing import Any

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    CredentialBindingNotFoundError,
    CredentialNotVisibleError,
)
from jentic_one.shared.auth.grant_renewal import GrantRenewalPolicy, GrantRenewalRefusedError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import AGENTS_WRITE, ORG_ADMIN, compute_effective
from jentic_one.shared.context import Context


class AgentGrantRenewer:
    """The grant-renewer protocol, implemented by the agents service."""

    def __init__(self, ctx: Context) -> None:
        self._agents = AgentService(ctx)

    async def may_renew(self, *, identity: Identity, agent_id: str, credential_id: str) -> bool:
        # The renew endpoint's route gate, then its service checks.
        held = compute_effective(set(identity.permissions))
        if ORG_ADMIN not in held and AGENTS_WRITE not in held:
            return False
        try:
            await self._agents.require_renewable(
                agent_id, credential_id=credential_id, identity=identity
            )
        except (ActorNotFoundError, CredentialNotVisibleError):
            return False
        return True

    async def renew(
        self,
        *,
        identity: Identity,
        agent_id: str,
        credential_id: str,
        policy: GrantRenewalPolicy,
        session: Any | None = None,
    ) -> None:
        if not await self.may_renew(
            identity=identity, agent_id=agent_id, credential_id=credential_id
        ):
            raise GrantRenewalRefusedError(
                f"'{identity.sub}' may not renew agent '{agent_id}'s access to '{credential_id}'"
            )
        try:
            await self._agents.renew_credential(
                agent_id,
                credential_id=credential_id,
                identity=identity,
                expires_at=policy.expires_at,
                max_idle_period=policy.max_idle_period,
                idle_warning=policy.idle_warning,
                max_quota=policy.max_quota,
                after_expiry=policy.after_expiry,
                session=session,
            )
        except (
            ActorNotFoundError,
            CredentialNotVisibleError,
            CredentialBindingNotFoundError,
        ) as exc:
            raise GrantRenewalRefusedError(str(exc)) from exc
