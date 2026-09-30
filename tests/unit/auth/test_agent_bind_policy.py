"""Unit tests for the direct-bind ownership policy (``AgentService._can_bind_credential``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType

_OWNER = "usr_owner"
_OTHER = "usr_other"


def _identity(
    sub: str,
    permissions: list[str],
    *,
    actor_type: ActorType = ActorType.USER,
    parent_actor_id: str | None = None,
) -> Identity:
    return Identity(
        sub=sub,
        actor_type=actor_type,
        permissions=permissions,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
        parent_actor_id=parent_actor_id,
    )


@pytest.mark.parametrize(
    ("identity", "created_by", "allowed"),
    [
        # org:admin administers every credential, including unowned ones.
        (_identity(_OTHER, ["org:admin"]), _OWNER, True),
        (_identity(_OTHER, ["org:admin"]), None, True),
        # The creator may bind their own credential with no credential scopes.
        (_identity(_OWNER, ["agents:write"]), _OWNER, True),
        # credentials:* never widens binding to someone else's credential (#88).
        (_identity(_OTHER, ["agents:write", "credentials:read"]), _OWNER, False),
        (_identity(_OTHER, ["agents:write", "credentials:write"]), _OWNER, False),
        # An unowned credential is admin-only.
        (_identity(_OWNER, ["agents:write", "credentials:write"]), None, False),
        # A delegated agent may bind its owner's credential...
        (
            _identity(
                "agnt_1",
                ["owner:credentials:read"],
                actor_type=ActorType.AGENT,
                parent_actor_id=_OWNER,
            ),
            _OWNER,
            True,
        ),
        # ...but not without the delegation scope...
        (
            _identity("agnt_1", [], actor_type=ActorType.AGENT, parent_actor_id=_OWNER),
            _OWNER,
            False,
        ),
        # ...and never another user's credential.
        (
            _identity(
                "agnt_1",
                ["owner:credentials:read"],
                actor_type=ActorType.AGENT,
                parent_actor_id=_OWNER,
            ),
            _OTHER,
            False,
        ),
    ],
)
def test_can_bind_credential_is_ownership_scoped(
    identity: Identity, created_by: str | None, allowed: bool
) -> None:
    service = AgentService(MagicMock())
    assert service._can_bind_credential(identity, created_by) is allowed
