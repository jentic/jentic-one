"""Integration tests for the cross-DB ``ToolkitBindingResolver``.

Seeds the admin DB (agent→toolkit bindings) and the control DB (toolkits,
credentials, toolkit→credential bindings), then asserts ``derive_toolkits``
returns the intersection for an API identity: ``[]`` (no overlap), one toolkit,
and many toolkits (the 0/1/N cases the broker maps to 403/use/409).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.broker.repos.toolkit_binding_resolver import ToolkitBindingResolver
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_tables(
    admin_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Truncate the admin and control tables this module touches, before and after."""

    async def _truncate() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(AgentToolkitBinding))
            await session.execute(delete(Agent))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Credential))
            await session.execute(delete(Toolkit))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_agent_with_toolkits(admin_db: DatabaseSession, *, toolkit_ids: list[str]) -> str:
    """Create an agent bound to the given toolkit ids; return the agent id."""
    agent = Agent(name="test-agent", registered_by="usr_test", status="approved")
    async with admin_db.session() as session:
        session.add(agent)
        await session.flush()
        agent_id = agent.id
        for tk_id in toolkit_ids:
            session.add(
                AgentToolkitBinding(id=generate_ksuid("atb"), agent_id=agent_id, toolkit_id=tk_id)
            )
        await session.commit()
    return agent_id


async def _seed_toolkit_with_credential(
    control_db: DatabaseSession,
    *,
    toolkit_name: str,
    vendor: str,
    name: str | None,
    version: str | None,
) -> str:
    """Create a toolkit + credential + binding for an API identity; return the toolkit id."""
    toolkit = Toolkit(name=toolkit_name)
    credential = Credential(
        type="token_value",
        name=f"cred-{toolkit_name}",
        api_vendor=vendor,
        api_name=name,
        api_version=version,
    )
    async with control_db.session() as session:
        session.add(toolkit)
        session.add(credential)
        await session.flush()
        tk_id = toolkit.id
        session.add(
            ToolkitCredentialBinding(
                id=generate_ksuid("tcb"), toolkit_id=tk_id, credential_id=credential.id
            )
        )
        await session.commit()
    return tk_id


async def test_derive_toolkits_no_overlap_returns_empty(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Agent bound to a toolkit that does not contain the API → [] (403 at caller)."""
    tk_id = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-other", vendor="other.com", name="x", version="v1"
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[tk_id])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.toolkits == ()


async def test_derive_toolkits_single_match(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Exactly one of the agent's toolkits contains the API → [that toolkit]."""
    matching = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-acme", vendor="acme.com", name="pets-api", version="v1"
    )
    other = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-other", vendor="other.com", name="x", version="v1"
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[matching, other])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.toolkits == (matching,)


async def test_derive_toolkits_multiple_matches(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Two of the agent's toolkits contain the API → both (409 at caller)."""
    tk_a = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-a", vendor="acme.com", name="pets-api", version="v1"
    )
    tk_b = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-b", vendor="acme.com", name="pets-api", version="v1"
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[tk_a, tk_b])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.toolkits == tuple(sorted([tk_a, tk_b]))


async def test_derive_toolkits_wildcard_name_version(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A NULL-name/version credential covers any concrete API for the vendor."""
    # The credential is vendor-scoped (NULL name/version), so it covers a concrete
    # operation identity for that vendor.
    tk_id = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-acme", vendor="acme.com", name=None, version=None
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[tk_id])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v2"
    )

    assert result.toolkits == (tk_id,)


async def test_derive_toolkits_no_agent_bindings_returns_empty(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """An agent with no toolkit bindings short-circuits to []."""
    await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-acme", vendor="acme.com", name="pets-api", version="v1"
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.toolkits == ()
    assert result.agent_bound_any is False


async def test_derive_toolkits_excludes_unbound_wildcard_toolkit(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """Defense-in-depth: a NULL-wildcard credential toolkit the agent is *not*
    bound to must never be returned, even though its credential covers the
    vendor. This locks in that the execute-time accept-any matching is always
    intersected with the agent's own bindings, so the bind-time / execute-time
    asymmetry can never let an agent execute against a toolkit it wasn't bound to.
    """
    bound = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-bound", vendor="acme.com", name="pets-api", version="v1"
    )
    # A wildcard (NULL name/version) credential toolkit for the same vendor that
    # the agent is deliberately NOT bound to.
    unbound_wildcard = await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-wild", vendor="acme.com", name=None, version=None
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[bound])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.toolkits == (bound,)
    assert unbound_wildcard not in result.toolkits
    # Both toolkits serve the API (the wildcard covers it too), independent of
    # the agent's bindings — this drives the recovery directive, not authz.
    assert set(result.api_served_toolkits) == {bound, unbound_wildcard}


async def test_derive_toolkits_served_true_when_a_toolkit_serves(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """A toolkit with a bound credential for the API → serves the API (issue #683).

    ``api_served_toolkits`` is independent of any agent binding: it drives the
    ``no_toolkit_binding`` recovery directive, not authorization.
    """
    await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-acme", vendor="acme.com", name="pets-api", version="v1"
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.api_served_toolkits != ()


async def test_derive_toolkits_served_false_when_no_toolkit_serves(
    admin_db: DatabaseSession, control_db: DatabaseSession, clean_tables: None
) -> None:
    """No toolkit bound a credential for the API → does not serve it (issue #683).

    This is the state whose directive must recommend provisioning a credential
    first rather than an unapprovable toolkit-binding request.
    """
    await _seed_toolkit_with_credential(
        control_db, toolkit_name="tk-other", vendor="other.com", name="x", version="v1"
    )
    agent_id = await _seed_agent_with_toolkits(admin_db, toolkit_ids=[])

    resolver = ToolkitBindingResolver(admin_db, control_db)
    result = await resolver.derive_toolkits(
        agent_id=agent_id, vendor="acme.com", name="pets-api", version="v1"
    )

    assert result.api_served_toolkits == ()
