"""Web tests for ``GET /governed-hosts`` (#1278) against real databases.

Exercises the full three-database derivation (admin bindings → control
credential scopes → registry host resolution), the scope gate, the toolkit-key
short-circuit, and the ETag change-poll round trip.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.servers import Server
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

from .conftest import _build_app_as

pytestmark = pytest.mark.integration

_VENDOR = "gvh-vendor"
_OTHER_VENDOR = "gvh-other"


# --- seeding -----------------------------------------------------------------


@pytest.fixture()
async def clean_tables(web_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with web_context.registry_db.session() as session:
            # Servers/revisions cascade from the API delete; clear the FK first.
            for vendor in (_VENDOR, _OTHER_VENDOR):
                await session.execute(
                    update(Api).where(Api.vendor == vendor).values(current_revision_id=None)
                )
                await session.execute(delete(Api).where(Api.vendor == vendor))
            await session.commit()
        async with web_context.control_db.session() as session:
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Credential).where(Credential.api_vendor.like("gvh-%")))
            await session.execute(delete(Toolkit).where(Toolkit.name.like("tk-gvh-%")))
            await session.commit()
        async with web_context.admin_db.session() as session:
            await session.execute(delete(AgentToolkitBinding))
            await session.execute(delete(Agent).where(Agent.name.like("gvh-%")))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api(ctx: Context, *, vendor: str, name: str, version: str, url: str) -> None:
    """Register an API whose current revision serves ``url``."""
    async with ctx.registry_db.session() as session:
        api = Api(vendor=vendor, name=name, version=version)
        session.add(api)
        await session.flush()
        rev = ApiRevision(api_id=api.id, spec_digest=f"sha256:{vendor}-{name}", source_type="url")
        session.add(rev)
        await session.flush()
        session.add(Server(revision_id=rev.id, url=url))
        api.current_revision_id = rev.id
        await session.commit()


async def _seed_toolkit_credential(
    ctx: Context,
    *,
    toolkit_name: str,
    api_vendor: str,
    api_name: str | None,
    api_version: str | None,
    active: bool = True,
) -> str:
    """Seed a toolkit + bound credential scope; return the toolkit id."""
    async with ctx.control_db.session() as session:
        toolkit = Toolkit(name=toolkit_name)
        credential = Credential(
            type="token_value",
            name=f"cred-{toolkit_name}-{api_name or 'wildcard'}",
            api_vendor=api_vendor,
            api_name=api_name,
            api_version=api_version,
            active=active,
        )
        session.add_all([toolkit, credential])
        await session.flush()
        session.add(ToolkitCredentialBinding(toolkit_id=toolkit.id, credential_id=credential.id))
        toolkit_id = toolkit.id
        await session.commit()
    return toolkit_id


async def _seed_agent_binding(ctx: Context, *, agent_name: str, toolkit_ids: list[str]) -> str:
    """Seed an agent bound to the toolkits; return the agent id."""
    async with ctx.admin_db.session() as session:
        agent = Agent(name=agent_name, registered_by="usr_gvh_test")
        session.add(agent)
        await session.flush()
        for toolkit_id in toolkit_ids:
            session.add(AgentToolkitBinding(agent_id=agent.id, toolkit_id=toolkit_id))
        agent_id = agent.id
        await session.commit()
    return agent_id


# --- clients -----------------------------------------------------------------


def _agent_client(web_context: Context, sub: str) -> TestClient:
    """A delegated agent holding only the owner-scoped leaf (DEFAULT_AGENT_SCOPES member)."""
    identity = Identity(sub=sub, actor_type=ActorType.AGENT, permissions=["owner:toolkits:read"])
    return TestClient(
        _build_app_as(web_context, identity), headers={"Authorization": "Bearer test-token"}
    )


def _toolkit_key_client(web_context: Context, toolkit_id: str) -> TestClient:
    """A toolkit-key actor — its ``sub`` *is* the toolkit id."""
    identity = Identity(sub=toolkit_id, actor_type=ActorType.TOOLKIT, permissions=["toolkits:read"])
    return TestClient(
        _build_app_as(web_context, identity), headers={"Authorization": "Bearer test-token"}
    )


# --- tests -------------------------------------------------------------------


@pytest.mark.usefixtures("clean_tables")
async def test_two_toolkits_union_of_hosts(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", url="https://alpha.gvh.test/v1"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", url="https://beta.gvh.test/v1"
    )
    tk1 = await _seed_toolkit_credential(
        web_context, toolkit_name="tk-gvh-a", api_vendor=_VENDOR, api_name="alpha", api_version="v1"
    )
    tk2 = await _seed_toolkit_credential(
        web_context, toolkit_name="tk-gvh-b", api_vendor=_VENDOR, api_name="beta", api_version="v1"
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent", toolkit_ids=[tk1, tk2]
    )

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    body = resp.json()
    hosts = [item["host"] for item in body["data"]]
    assert hosts == ["alpha.gvh.test", "beta.gvh.test"]  # union, sorted
    assert body["data"][0]["apis"] == [
        {"vendor": _VENDOR, "name": "alpha", "version": "v1", "host": "alpha.gvh.test"}
    ]
    assert resp.headers["ETag"] == f'"{body["digest"]}"'


@pytest.mark.usefixtures("clean_tables")
async def test_wildcard_credential_expands_to_all_covered_apis(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", url="https://alpha.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", url="https://beta.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_OTHER_VENDOR, name="gamma", version="v1", url="https://gamma.gvh.test"
    )
    tk = await _seed_toolkit_credential(
        web_context, toolkit_name="tk-gvh-wild", api_vendor=_VENDOR, api_name=None, api_version=None
    )
    agent_id = await _seed_agent_binding(web_context, agent_name="gvh-agent-wild", toolkit_ids=[tk])

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    hosts = [item["host"] for item in resp.json()["data"]]
    # Bare-vendor wildcard covers every _VENDOR API — but never the other vendor.
    assert hosts == ["alpha.gvh.test", "beta.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_inactive_credential_is_excluded(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", url="https://alpha.gvh.test"
    )
    tk = await _seed_toolkit_credential(
        web_context,
        toolkit_name="tk-gvh-inactive",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
        active=False,
    )
    agent_id = await _seed_agent_binding(web_context, agent_name="gvh-agent-ina", toolkit_ids=[tk])

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.usefixtures("clean_tables")
async def test_toolkit_key_actor_short_circuits_to_own_toolkit(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", url="https://alpha.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", url="https://beta.gvh.test"
    )
    tk_mine = await _seed_toolkit_credential(
        web_context,
        toolkit_name="tk-gvh-mine",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    # A second toolkit that must NOT leak into the toolkit key's view.
    await _seed_toolkit_credential(
        web_context,
        toolkit_name="tk-gvh-theirs",
        api_vendor=_VENDOR,
        api_name="beta",
        api_version="v1",
    )

    with _toolkit_key_client(web_context, tk_mine) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert [item["host"] for item in resp.json()["data"]] == ["alpha.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_empty_bindings_yield_empty_data_and_stable_digest(web_context: Context) -> None:
    agent_id = await _seed_agent_binding(web_context, agent_name="gvh-agent-empty", toolkit_ids=[])

    with _agent_client(web_context, agent_id) as client:
        first = client.get("/governed-hosts")
        second = client.get("/governed-hosts")
    assert first.status_code == 200
    assert first.json()["data"] == []
    assert first.json()["digest"] == second.json()["digest"]


@pytest.mark.usefixtures("clean_tables")
async def test_etag_round_trip(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", url="https://alpha.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", url="https://beta.gvh.test"
    )
    tk1 = await _seed_toolkit_credential(
        web_context,
        toolkit_name="tk-gvh-etag",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-etag", toolkit_ids=[tk1]
    )

    with _agent_client(web_context, agent_id) as client:
        first = client.get("/governed-hosts")
        assert first.status_code == 200
        etag = first.headers["ETag"]

        unchanged = client.get("/governed-hosts", headers={"If-None-Match": etag})
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        assert unchanged.headers["ETag"] == etag

        # Bind a second toolkit → the host set (and so the digest) changes.
        tk2 = await _seed_toolkit_credential(
            web_context,
            toolkit_name="tk-gvh-etag2",
            api_vendor=_VENDOR,
            api_name="beta",
            api_version="v1",
        )
        async with web_context.admin_db.session() as session:
            session.add(AgentToolkitBinding(agent_id=agent_id, toolkit_id=tk2))
            await session.commit()

        changed = client.get("/governed-hosts", headers={"If-None-Match": etag})
        assert changed.status_code == 200
        assert changed.headers["ETag"] != etag
        assert [item["host"] for item in changed.json()["data"]] == [
            "alpha.gvh.test",
            "beta.gvh.test",
        ]


@pytest.mark.usefixtures("clean_tables")
async def test_requires_toolkits_read_scope(web_context: Context) -> None:
    identity = Identity(
        sub="usr_gvh_wrong_scope",
        email="gvh-wrong@test.local",
        permissions=["apis:read"],  # neither toolkits:read nor owner:toolkits:read
    )
    with TestClient(
        _build_app_as(web_context, identity), headers={"Authorization": "Bearer test-token"}
    ) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 403
