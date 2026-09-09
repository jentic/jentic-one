"""Behavioural tests for the governed-hosts derivation on the integration leg (#1278).

``tests/web`` runs on Postgres only, so the identity-scoping and change-poll
digest behaviours are pinned here too — this suite runs on **both** backends
(``JENTIC_TEST_BACKEND``), including the sqlite leg that gates every PR. It
drives ``GovernedHostsService`` directly on the fixture's event loop (the
HTTP seam — ETag/304/If-None-Match/cache headers — is covered by the unit
router tests); the full derivation matrix lives in
``tests/web/registry/test_governed_hosts.py``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.url_index import build_index_entry, merge_paths, parse_server_url
from jentic_one.registry.services.governed_hosts_service import GovernedHostsService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration

_VENDOR = "gvhi-vendor"


@pytest.fixture()
async def clean_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with integration_context.registry_db.session() as session:
            await session.execute(
                update(Api).where(Api.vendor == _VENDOR).values(current_revision_id=None)
            )
            await session.execute(delete(Api).where(Api.vendor == _VENDOR))
            await session.commit()
        async with integration_context.control_db.session() as session:
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.execute(delete(Toolkit).where(Toolkit.name.like("tk-gvhi-%")))
            await session.commit()
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(AgentToolkitBinding))
            await session.execute(delete(Agent).where(Agent.name.like("gvhi-%")))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api_toolkit_agent(ctx: Context, *, tag: str, url: str) -> tuple[str, str]:
    """One API (served at ``url``) + toolkit + credential + agent.

    Returns ``(agent_id, toolkit_id)``. The registry seed mirrors the ingest's
    ``URLIndexStage`` (same ``build_index_entry`` the broker's discovery
    matches against).
    """
    op_path = "/things"
    async with ctx.registry_db.session() as session:
        api = Api(vendor=_VENDOR, name=tag, version="v1")
        session.add(api)
        await session.flush()
        rev = ApiRevision(api_id=api.id, spec_digest=f"sha256:{tag}", source_type="url")
        session.add(rev)
        await session.flush()
        op = Operation(id=f"op-gvhi-{tag}", revision_id=rev.id, path=op_path, method="GET")
        session.add(op)
        session.add(Server(revision_id=rev.id, url=url))
        parsed = parse_server_url(url)
        entry = build_index_entry(parsed.host, merge_paths(parsed.path, op_path), parsed.scheme)
        session.add(
            OperationURLIndex(
                operation_id=op.id,
                revision_id=rev.id,
                method="GET",
                host=entry.host_pattern,
                host_regex=entry.host_regex.pattern,
                path_template=entry.path_pattern,
                path_regex=entry.path_regex.pattern,
                param_names=entry.param_names,
                segment_count=entry.segment_count,
            )
        )
        api.current_revision_id = rev.id
        await session.commit()

    async with ctx.control_db.session() as session:
        toolkit = Toolkit(name=f"tk-gvhi-{tag}")
        credential = Credential(
            type="token_value",
            name=f"cred-gvhi-{tag}",
            api_vendor=_VENDOR,
            api_name=tag,
            api_version="v1",
        )
        session.add_all([toolkit, credential])
        await session.flush()
        session.add(ToolkitCredentialBinding(toolkit_id=toolkit.id, credential_id=credential.id))
        toolkit_id = toolkit.id
        await session.commit()

    async with ctx.admin_db.session() as session:
        agent = Agent(name=f"gvhi-{tag}", registered_by="usr_gvhi_test")
        session.add(agent)
        await session.flush()
        session.add(AgentToolkitBinding(agent_id=agent.id, toolkit_id=toolkit_id))
        agent_id = agent.id
        await session.commit()
    return agent_id, toolkit_id


def _agent_identity(agent_id: str) -> Identity:
    return Identity(sub=agent_id, actor_type=ActorType.AGENT, permissions=["owner:toolkits:read"])


@pytest.mark.usefixtures("clean_tables")
async def test_identity_scoping_two_agents_disjoint(integration_context: Context) -> None:
    """Each agent sees only its own toolkit-derived hosts — an unscoped read on
    any of the three legs fails this test on both backends."""
    agent_a, _ = await _seed_api_toolkit_agent(
        integration_context, tag="alpha", url="https://alpha.gvhi.test/v1"
    )
    agent_b, _ = await _seed_api_toolkit_agent(
        integration_context, tag="beta", url="https://beta.gvhi.test/v1"
    )
    svc = GovernedHostsService(integration_context)

    view_a = await svc.get_governed_hosts(_agent_identity(agent_a))
    view_b = await svc.get_governed_hosts(_agent_identity(agent_b))

    assert view_a.hosts == ("alpha.gvhi.test",)
    assert view_b.hosts == ("beta.gvhi.test",)
    assert set(view_a.hosts).isdisjoint(view_b.hosts)
    assert view_a.digest != view_b.digest


@pytest.mark.usefixtures("clean_tables")
async def test_digest_is_stable_until_bindings_change(integration_context: Context) -> None:
    """The change-poll contract at derivation level: the digest is stable
    across repeated reads and changes exactly when the host set does."""
    agent_id, _ = await _seed_api_toolkit_agent(
        integration_context, tag="alpha", url="https://alpha.gvhi.test/v1"
    )
    svc = GovernedHostsService(integration_context)
    identity = _agent_identity(agent_id)

    first = await svc.get_governed_hosts(identity)
    second = await svc.get_governed_hosts(identity)
    assert first == second

    # A second bound API changes the host set → a new digest.
    _, toolkit_b = await _seed_api_toolkit_agent(
        integration_context, tag="beta", url="https://beta.gvhi.test/v1"
    )
    async with integration_context.admin_db.session() as session:
        session.add(AgentToolkitBinding(agent_id=agent_id, toolkit_id=toolkit_b))
        await session.commit()

    changed = await svc.get_governed_hosts(identity)
    assert changed.hosts == ("alpha.gvhi.test", "beta.gvhi.test")
    assert changed.digest != first.digest
