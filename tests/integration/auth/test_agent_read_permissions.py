"""Integration tests: ``GET /agents/{id}`` reads on the effective permission set.

An API-key identity carries its direct grants only, while a login token
carries the expanded set. ``AgentService.get_readable_agent`` (and
``list_credentials``, which shares its check) must expand implied permissions
the same way the route gate does, so ``agents:write`` reads like
``agents:read`` whichever channel the caller authenticates through.
"""

from __future__ import annotations

import pytest

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import ActorNotFoundError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_READ,
    AGENTS_WRITE,
    OWNER_AGENTS_READ,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration


def _sibling(sibling_id: str, owner_id: str, permissions: list[str]) -> Identity:
    """An agent reading its owner's other agents through ``owner:agents:read``."""
    return Identity(
        sub=sibling_id,
        email="",
        permissions=[OWNER_AGENTS_READ, *permissions],
        actor_type=ActorType.AGENT,
        parent_actor_id=owner_id,
    )


@pytest.mark.parametrize(
    ("permissions", "readable"),
    [
        ([], False),
        ([AGENTS_READ], True),
        ([AGENTS_WRITE], True),
    ],
    ids=["no-agents-permission", "agents-read", "agents-write-implies-read"],
)
async def test_get_readable_agent_uses_effective_permissions(
    integration_context: Context,
    clean_grants: None,
    permissions: list[str],
    readable: bool,
) -> None:
    """A visible sibling agent is readable with ``agents:read`` or ``agents:write``.

    Without either, the read is the same not-found as a missing id.
    """
    ctx = integration_context
    owner_id = await seeds.seed_user(ctx, "usr_read_perm_owner")
    target_id = await seeds.seed_agent(ctx, owner_id=owner_id, scopes=[], name="target")
    sibling_id = await seeds.seed_agent(ctx, owner_id=owner_id, scopes=[], name="sibling")
    identity = _sibling(sibling_id, owner_id, permissions)
    svc = AgentService(ctx)

    if readable:
        view = await svc.get_readable_agent(target_id, identity=identity)
        assert view.id == target_id
        assert await svc.list_credentials(target_id, identity=identity) == []
    else:
        with pytest.raises(ActorNotFoundError):
            await svc.get_readable_agent(target_id, identity=identity)
        with pytest.raises(ActorNotFoundError):
            await svc.list_credentials(target_id, identity=identity)

    # A missing id answers the same not-found for every caller.
    with pytest.raises(ActorNotFoundError):
        await svc.get_readable_agent("agnt_missing", identity=identity)
