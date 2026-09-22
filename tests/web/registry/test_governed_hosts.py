"""Web tests for ``GET /governed-hosts`` (#1278) against real databases.

Exercises the full three-database derivation (admin credential bindings →
control credential scopes → registry URL-index host resolution), the scope
gate, and the ETag change-poll round trip.

The registry seed mirrors the ingest pipeline's ``URLIndexStage``: hosts are
read from ``operation_url_indexes`` (what the broker's discovery matches), so
each seeded server gets an index row built with the same
``build_index_entry`` the ingest uses.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.url_index import build_index_entry, merge_paths, parse_server_url
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
            # Servers/revisions/operations/index rows cascade from the API
            # delete; clear the FK first.
            for vendor in (_VENDOR, _OTHER_VENDOR):
                await session.execute(
                    update(Api).where(Api.vendor == vendor).values(current_revision_id=None)
                )
                await session.execute(delete(Api).where(Api.vendor == vendor))
            await session.commit()
        async with web_context.control_db.session() as session:
            await session.execute(delete(Credential).where(Credential.api_vendor.like("gvh-%")))
            await session.commit()
        async with web_context.admin_db.session() as session:
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(delete(Agent).where(Agent.name.like("gvh-%")))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api(
    ctx: Context, *, vendor: str, name: str, version: str, urls: str | list[str]
) -> None:
    """Register an API whose current revision serves ``urls``.

    Mirrors the ingest's ``URLIndexStage``: one operation, one URL-index row
    per server, built with the same ``build_index_entry`` — the index is what
    ``GET /governed-hosts`` reads (the broker discovery's match source).
    """
    server_urls = [urls] if isinstance(urls, str) else urls
    op_path = "/things"
    async with ctx.registry_db.session() as session:
        api = Api(vendor=vendor, name=name, version=version)
        session.add(api)
        await session.flush()
        rev = ApiRevision(api_id=api.id, spec_digest=f"sha256:{vendor}-{name}", source_type="url")
        session.add(rev)
        await session.flush()
        op = Operation(id=f"op-{vendor}-{name}", revision_id=rev.id, path=op_path, method="GET")
        session.add(op)
        for url in server_urls:
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


async def _seed_credential(
    ctx: Context,
    *,
    label: str,
    api_vendor: str,
    api_name: str | None,
    api_version: str | None,
    active: bool = True,
) -> str:
    """Seed a stored credential scope (control DB); return the credential id."""
    async with ctx.control_db.session() as session:
        credential = Credential(
            type="token_value",
            name=f"cred-{label}",
            api_vendor=api_vendor,
            api_name=api_name,
            api_version=api_version,
            active=active,
        )
        session.add(credential)
        await session.flush()
        credential_id = credential.id
        await session.commit()
    return credential_id


async def _seed_agent_binding(
    ctx: Context, *, agent_name: str, credential_ids: list[str], suspended: bool = False
) -> str:
    """Seed an agent bound to the credentials; return the agent id."""
    async with ctx.admin_db.session() as session:
        agent = Agent(name=agent_name, registered_by="usr_gvh_test")
        session.add(agent)
        await session.flush()
        for credential_id in credential_ids:
            session.add(
                AgentCredentialBinding(
                    agent_id=agent.id, credential_id=credential_id, suspended=suspended
                )
            )
        agent_id = agent.id
        await session.commit()
    return agent_id


# --- clients -----------------------------------------------------------------


def _agent_client(web_context: Context, sub: str) -> TestClient:
    """A delegated agent holding only the owner-scoped leaf (DEFAULT_AGENT_PERMISSIONS member)."""
    identity = Identity(sub=sub, actor_type=ActorType.AGENT, permissions=["owner:credentials:read"])
    return TestClient(
        _build_app_as(web_context, identity), headers={"Authorization": "Bearer test-token"}
    )


# --- tests -------------------------------------------------------------------


@pytest.mark.usefixtures("clean_tables")
async def test_two_bindings_union_of_hosts(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test/v1"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", urls="https://beta.gvh.test/v1"
    )
    cred1 = await _seed_credential(
        web_context, label="gvh-a", api_vendor=_VENDOR, api_name="alpha", api_version="v1"
    )
    cred2 = await _seed_credential(
        web_context, label="gvh-b", api_vendor=_VENDOR, api_name="beta", api_version="v1"
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent", credential_ids=[cred1, cred2]
    )

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == ["alpha.gvh.test", "beta.gvh.test"]  # union, sorted
    assert resp.headers["ETag"] == f'"{body["digest"]}"'


@pytest.mark.usefixtures("clean_tables")
async def test_multi_server_api_contributes_every_host(web_context: Context) -> None:
    """An API with several servers governs *all* of their hosts, not just the first."""
    await _seed_api(
        web_context,
        vendor=_VENDOR,
        name="alpha",
        version="v1",
        urls=["https://alpha.gvh.test/v1", "https://alpha-eu.gvh.test/v1"],
    )
    cred = await _seed_credential(
        web_context,
        label="gvh-ms",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-ms", credential_ids=[cred]
    )

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert resp.json()["data"] == ["alpha-eu.gvh.test", "alpha.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_templated_host_is_excluded(web_context: Context) -> None:
    """A defaultless server variable produces a ``{var}`` host pattern the
    URL index's exact-match lookup can never match (the regex branch requires
    ``host IS NULL``, which the ingest never writes) — so publishing it would
    tell a gate to divert traffic the broker cannot serve. Excluded."""
    await _seed_api(
        web_context,
        vendor=_VENDOR,
        name="alpha",
        version="v1",
        urls=["https://{region}.alpha.gvh.test/v1", "https://static.alpha.gvh.test/v1"],
    )
    cred = await _seed_credential(
        web_context,
        label="gvh-tpl",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-tpl", credential_ids=[cred]
    )

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert resp.json()["data"] == ["static.alpha.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_wildcard_credential_expands_to_all_covered_apis(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", urls="https://beta.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_OTHER_VENDOR, name="gamma", version="v1", urls="https://gamma.gvh.test"
    )
    cred = await _seed_credential(
        web_context, label="gvh-wild", api_vendor=_VENDOR, api_name=None, api_version=None
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-wild", credential_ids=[cred]
    )

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    # Bare-vendor wildcard covers every _VENDOR API — but never the other vendor.
    assert resp.json()["data"] == ["alpha.gvh.test", "beta.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_inactive_credential_is_still_governed(web_context: Context) -> None:
    """An inactive credential's hosts stay in the governed set: deactivation
    must not divert its traffic around the broker — the call must still arrive
    and be refused loudly (fail closed). Omitting the host would send the
    traffic direct to the upstream, unbrokered."""
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test"
    )
    cred = await _seed_credential(
        web_context,
        label="gvh-inactive",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
        active=False,
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-ina", credential_ids=[cred]
    )

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert resp.json()["data"] == ["alpha.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_two_agents_see_disjoint_host_sets(web_context: Context) -> None:
    """Identity scoping, pinned adversarially: two agents with disjoint
    bindings must never see each other's hosts. A mutation that unscopes any
    of the three legs (an all-rows binding read, an unfiltered scope read, or
    an unscoped host resolution) fails here — a single-actor test cannot
    distinguish "self-scoped" from "returns everything"."""
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", urls="https://beta.gvh.test"
    )
    # Leg-2/3 tripwire: a credential (covering gamma) bound to NO agent — its
    # host must appear for neither actor.
    await _seed_api(
        web_context, vendor=_OTHER_VENDOR, name="gamma", version="v1", urls="https://gamma.gvh.test"
    )
    cred_a = await _seed_credential(
        web_context, label="gvh-a", api_vendor=_VENDOR, api_name="alpha", api_version="v1"
    )
    cred_b = await _seed_credential(
        web_context, label="gvh-b", api_vendor=_VENDOR, api_name="beta", api_version="v1"
    )
    await _seed_credential(
        web_context,
        label="gvh-unbound",
        api_vendor=_OTHER_VENDOR,
        api_name="gamma",
        api_version="v1",
    )
    agent_a = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-a", credential_ids=[cred_a]
    )
    agent_b = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-b", credential_ids=[cred_b]
    )

    with _agent_client(web_context, agent_a) as client:
        resp_a = client.get("/governed-hosts")
    with _agent_client(web_context, agent_b) as client:
        resp_b = client.get("/governed-hosts")

    assert resp_a.json()["data"] == ["alpha.gvh.test"]
    assert resp_b.json()["data"] == ["beta.gvh.test"]
    assert set(resp_a.json()["data"]).isdisjoint(resp_b.json()["data"])
    assert resp_a.json()["digest"] != resp_b.json()["digest"]
    for body in (resp_a.json(), resp_b.json()):
        assert "gamma.gvh.test" not in body["data"]


@pytest.mark.usefixtures("clean_tables")
async def test_archived_revision_hosts_still_governed(web_context: Context) -> None:
    """Archiving clears ``current_revision_id`` but never deletes the URL-index
    rows, and discovery's ``lookup_by_host_any_revision`` has no revision or
    state predicate — the archived revision's hosts still route through the
    broker, so they must stay in the governed set."""
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test"
    )
    cred = await _seed_credential(
        web_context,
        label="gvh-arch",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-arch", credential_ids=[cred]
    )

    # Mirror RevisionService.archive: state → archived, live pointer cleared.
    # The index rows stay (only re-indexing deletes them).
    async with web_context.registry_db.session() as session:
        await session.execute(
            update(Api).where(Api.vendor == _VENDOR).values(current_revision_id=None)
        )
        await session.execute(
            update(ApiRevision)
            .where(ApiRevision.api_id.in_(select(Api.id).where(Api.vendor == _VENDOR)))
            .values(state="archived")
        )
        await session.commit()

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert resp.json()["data"] == ["alpha.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_suspended_binding_is_still_governed(web_context: Context) -> None:
    """A suspended binding's hosts stay in the governed set: suspension is the
    reversible cut-off, and it is only *enforced* if the traffic still diverts
    to the broker to be refused. Dropping the host would send the cut-off
    agent's traffic direct to the upstream — suspension would widen its
    effective egress instead of closing it."""
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test"
    )
    cred = await _seed_credential(
        web_context,
        label="gvh-susp",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-susp", credential_ids=[cred], suspended=True
    )

    with _agent_client(web_context, agent_id) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 200
    assert resp.json()["data"] == ["alpha.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_empty_bindings_yield_empty_data_and_stable_digest(web_context: Context) -> None:
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-empty", credential_ids=[]
    )

    with _agent_client(web_context, agent_id) as client:
        first = client.get("/governed-hosts")
        second = client.get("/governed-hosts")
    assert first.status_code == 200
    assert first.json()["data"] == []
    assert first.json()["digest"] == second.json()["digest"]


@pytest.mark.usefixtures("clean_tables")
async def test_etag_round_trip(web_context: Context) -> None:
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test"
    )
    await _seed_api(
        web_context, vendor=_VENDOR, name="beta", version="v1", urls="https://beta.gvh.test"
    )
    cred1 = await _seed_credential(
        web_context,
        label="gvh-etag",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-etag", credential_ids=[cred1]
    )

    with _agent_client(web_context, agent_id) as client:
        first = client.get("/governed-hosts")
        assert first.status_code == 200
        etag = first.headers["ETag"]

        unchanged = client.get("/governed-hosts", headers={"If-None-Match": etag})
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        assert unchanged.headers["ETag"] == etag

        # Bind a second credential → the host set (and so the digest) changes.
        cred2 = await _seed_credential(
            web_context,
            label="gvh-etag2",
            api_vendor=_VENDOR,
            api_name="beta",
            api_version="v1",
        )
        async with web_context.admin_db.session() as session:
            session.add(AgentCredentialBinding(agent_id=agent_id, credential_id=cred2))
            await session.commit()

        changed = client.get("/governed-hosts", headers={"If-None-Match": etag})
        assert changed.status_code == 200
        assert changed.headers["ETag"] != etag
        assert changed.json()["data"] == ["alpha.gvh.test", "beta.gvh.test"]


@pytest.mark.usefixtures("clean_tables")
async def test_same_host_growth_keeps_etag_valid(web_context: Context) -> None:
    """A new covered API behind an already-governed host changes nothing the
    body carries (hosts only), so the ETag legitimately still matches — the
    digest covers exactly the representation."""
    await _seed_api(
        web_context, vendor=_VENDOR, name="alpha", version="v1", urls="https://alpha.gvh.test/v1"
    )
    cred1 = await _seed_credential(
        web_context,
        label="gvh-same",
        api_vendor=_VENDOR,
        api_name="alpha",
        api_version="v1",
    )
    agent_id = await _seed_agent_binding(
        web_context, agent_name="gvh-agent-same", credential_ids=[cred1]
    )

    with _agent_client(web_context, agent_id) as client:
        first = client.get("/governed-hosts")
        etag = first.headers["ETag"]

        # A second API served from the SAME host, newly covered by a binding.
        await _seed_api(
            web_context,
            vendor=_VENDOR,
            name="alpha-admin",
            version="v1",
            urls="https://alpha.gvh.test/admin",
        )
        cred2 = await _seed_credential(
            web_context,
            label="gvh-same2",
            api_vendor=_VENDOR,
            api_name="alpha-admin",
            api_version="v1",
        )
        async with web_context.admin_db.session() as session:
            session.add(AgentCredentialBinding(agent_id=agent_id, credential_id=cred2))
            await session.commit()

        second = client.get("/governed-hosts", headers={"If-None-Match": etag})
        assert second.status_code == 304  # host set unchanged → body unchanged

        fresh = client.get("/governed-hosts")
        assert fresh.json()["data"] == ["alpha.gvh.test"]
        assert fresh.headers["ETag"] == etag


@pytest.mark.usefixtures("clean_tables")
async def test_requires_credentials_read_scope(web_context: Context) -> None:
    identity = Identity(
        sub="usr_gvh_wrong_scope",
        email="gvh-wrong@test.local",
        permissions=["apis:read"],  # neither credentials:read nor owner:credentials:read
    )
    with TestClient(
        _build_app_as(web_context, identity), headers={"Authorization": "Bearer test-token"}
    ) as client:
        resp = client.get("/governed-hosts")
    assert resp.status_code == 403
