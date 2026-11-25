"""An ended binding's 403 names its credential, so the call can be held for renewal."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jentic_one.broker.core.errors import CredentialBindingExpiredError
from jentic_one.broker.services.execution.authorization import derive_credential_bindings
from jentic_one.broker.web.routers.execute import _renewable
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import CredentialDerivation, EndedBinding
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

_API = APIReference(vendor="acme.com", name="pets", version="v1")
_AGENT = Identity(sub="agnt_1", actor_type=ActorType.AGENT, permissions=[])


class _EndedDeriver:
    async def derive_credentials(self, **_: str) -> CredentialDerivation:
        return CredentialDerivation(
            credentials=(),
            agent_bound_any=False,
            api_served=True,
            identity_mismatch=None,
            binding_expired=True,
            expired_credential_id="cred_ended",
            expired_reason="uses",
            ended_bindings=(
                EndedBinding(
                    credential_id="cred_ended", name="key", reason="uses", holds=True, active=True
                ),
            ),
        )


async def test_the_denial_carries_the_ended_credential_but_never_renders_it() -> None:
    with pytest.raises(CredentialBindingExpiredError) as raised:
        await derive_credential_bindings(
            deriver=_EndedDeriver(),
            identity=_AGENT,
            api=_API,
            instance="/pets",
            ctx=MagicMock(),
        )
    error = raised.value
    assert error.ended_credential_id == "cred_ended"
    assert error.extra == {"expired_reason": "uses"}
    assert "its uses have run out" in error.detail


@pytest.mark.parametrize(
    ("identity", "credential_id", "keyset", "expected"),
    [
        (_AGENT, "cred_ended", True, True),
        (Identity(sub="usr_1", permissions=[]), "cred_ended", True, False),
        (_AGENT, None, True, False),
        (_AGENT, "cred_ended", False, False),
    ],
)
def test_only_an_agents_call_with_a_named_binding_and_a_keyset_is_held(
    identity: Identity, credential_id: str | None, keyset: bool, expected: bool
) -> None:
    error = CredentialBindingExpiredError("ended")
    error.ended_credential_id = credential_id
    ctx = MagicMock()
    ctx.has_encryption_keyset = keyset
    assert _renewable(ctx, identity, error) is expected
