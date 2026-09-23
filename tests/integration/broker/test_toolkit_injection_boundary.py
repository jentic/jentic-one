"""Integration test for the toolkit-path injection boundary (issue #88 shape).

Chains the real pieces the broker router composes when
``broker.direct_bindings_enabled`` is off: ``ToolkitBindingResolver`` derives
the agent's toolkit and its covering credentials, ``select_toolkit`` turns that
into the injection boundary, and ``CredentialService.inject`` resolves under
it. Two users each have a toolkit with a credential for the same API; agent A
is bound only to toolkit A. No header may reach user B's credential.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.broker.core.exceptions import ActionDeniedError, InvalidCredentialNameError
from jentic_one.broker.repos.toolkit_binding_resolver import ToolkitBindingResolver
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.web.routers.execute import select_toolkit
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_API = APIReference(vendor="acme-com", name="pets-api", version="v1")


@pytest.fixture()
async def clean_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(AgentToolkitBinding))
            await session.execute(delete(Agent))
            await session.commit()
        async with integration_context.control_db.session() as session:
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(CustomerAPIKey))
            await session.execute(delete(Credential))
            await session.execute(delete(Toolkit))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_toolkit(ctx: Context, *, label: str, owner: str, secret: str) -> tuple[str, str]:
    """A toolkit owned by ``owner`` with one API-key credential; returns (toolkit, credential)."""
    toolkit = Toolkit(name=f"tk-{label}")
    credential = Credential(
        type=StoredCredentialType.API_KEY,
        name=f"cred-{label}",
        api_vendor=_API.vendor,
        api_name=_API.name,
        api_version=_API.version,
        created_by=owner,
    )
    async with ctx.control_db.session() as session:
        session.add_all([toolkit, credential])
        await session.flush()
        session.add(
            CustomerAPIKey(
                id=generate_ksuid("key"),
                credential_id=credential.id,
                encrypted_key=ctx.encryption.encrypt(secret),
                location="header",
                field_name="X-Api-Key",
            )
        )
        session.add(
            ToolkitCredentialBinding(
                id=generate_ksuid("tcb"), toolkit_id=toolkit.id, credential_id=credential.id
            )
        )
        await session.commit()
        return toolkit.id, credential.id


async def _seed_agent(ctx: Context, *, toolkit_id: str) -> str:
    agent = Agent(name="agent-a", registered_by="usr_a", status="approved")
    async with ctx.admin_db.session() as session:
        session.add(agent)
        await session.flush()
        session.add(
            AgentToolkitBinding(id=generate_ksuid("atb"), agent_id=agent.id, toolkit_id=toolkit_id)
        )
        await session.commit()
        return agent.id


async def test_toolkit_path_never_injects_another_toolkits_credential(
    integration_context: Context, clean_tables: None
) -> None:
    ctx = integration_context
    tk_a, cred_a = await _seed_toolkit(
        ctx,
        label="a",
        owner="usr_a",
        secret="SECRET-A",  # pragma: allowlist secret
    )
    _tk_b, cred_b = await _seed_toolkit(
        ctx,
        label="b",
        owner="usr_b",
        secret="SECRET-B",  # pragma: allowlist secret
    )
    agent_id = await _seed_agent(ctx, toolkit_id=tk_a)
    identity = Identity(sub=agent_id, actor_type=ActorType.AGENT, permissions=[], active=True)

    selection = await select_toolkit(
        deriver=ToolkitBindingResolver(ctx.admin_db, ctx.control_db),
        identity=identity,
        api=_API,
        header_toolkit=None,
        instance="/acme.com/v1/pets",
    )
    assert selection.toolkit_id == tk_a
    assert selection.credential_ids == (cred_a,)

    service = CredentialService(ctx)

    async def _inject(
        *, credential_name: str | None = None, credential_id: str | None = None
    ) -> InjectedAuth:
        return await service.inject(
            api_vendor=_API.vendor,
            api_name=_API.name,
            api_version=_API.version,
            identity=identity,
            allowed_credential_ids=list(selection.credential_ids),
            credential_name=credential_name,
            credential_id=credential_id,
        )

    # No header: agent A's own credential, never B's.
    injected = await _inject()
    assert injected.credential_id == cred_a
    assert injected.headers == {"X-Api-Key": "SECRET-A"}

    # Naming B's credential (Jentic-Credential-Name) is "not found", not a leak.
    with pytest.raises(InvalidCredentialNameError) as by_name:
        await _inject(credential_name="cred-b")
    assert by_name.value.type == "credential_name_not_found"

    # Nor can B's id (Jentic-Credential-Id) select it from outside the boundary.
    with pytest.raises(InvalidCredentialNameError):
        await _inject(credential_id=cred_b)

    # With A's credential unbound, toolkit A no longer serves the API: derivation
    # denies rather than falling back to toolkit B, which the agent is not bound to.
    async with ctx.control_db.session() as session:
        await session.execute(
            delete(ToolkitCredentialBinding).where(ToolkitCredentialBinding.toolkit_id == tk_a)
        )
        await session.commit()
    with pytest.raises(ActionDeniedError):
        await select_toolkit(
            deriver=ToolkitBindingResolver(ctx.admin_db, ctx.control_db),
            identity=identity,
            api=_API,
            header_toolkit=None,
            instance="/acme.com/v1/pets",
        )
