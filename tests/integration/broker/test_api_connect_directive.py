"""Integration tests for the API-target connect suggestion on the 403 denial path.

Runs the real derive → deny path (``CredentialBindingResolver`` against the
admin and control DBs) for an agent with no binding and an API outside the
vendor registry: with ``control.connect.manual_flows_enabled`` on, the
``no_credential_binding`` directive names the API itself as the connect
target; off, it keeps the operator-only ask.
"""

from __future__ import annotations

import pytest

from jentic_one.broker.core.exceptions import ActionDeniedError
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.broker.services.execution.authorization import derive_credential_bindings
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_API = APIReference(vendor="acme-example", name="acme-example-pets", version="v1")


async def _deny(ctx: Context) -> ActionDeniedError:
    identity = Identity(sub="agnt_api_connect", actor_type=ActorType.AGENT, permissions=[])
    with pytest.raises(ActionDeniedError) as raised:
        await derive_credential_bindings(
            deriver=CredentialBindingResolver(ctx.admin_db, ctx.control_db),
            identity=identity,
            api=_API,
            instance="/pets",
            ctx=ctx,
        )
    return raised.value


async def test_denial_names_the_api_as_connect_target_when_api_connect_is_on(
    integration_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(integration_context.config.control.connect, "manual_flows_enabled", True)

    denial = await _deny(integration_context)

    assert denial.type == "no_credential_binding"
    directive = denial.directive
    assert directive is not None
    assert directive.parameters["api_served"] is False
    assert directive.parameters["suggested_command"] == (
        "jentic connect --api acme-example/acme-example-pets/v1"
    )
    assert directive.parameters["connect"] == {
        "api": {"vendor": "acme-example", "name": "acme-example-pets", "version": "v1"}
    }
    assert "end your turn" in directive.human_readable_instruction


async def test_denial_keeps_the_operator_ask_when_api_connect_is_off(
    integration_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(integration_context.config.control.connect, "manual_flows_enabled", False)

    denial = await _deny(integration_context)

    directive = denial.directive
    assert directive is not None
    assert "suggested_command" not in directive.parameters
    assert "connect" not in directive.parameters
    assert "dashboard" in directive.human_readable_instruction
