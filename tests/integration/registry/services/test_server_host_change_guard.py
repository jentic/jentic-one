"""Integration tests for the server-host change guard (real registry/control/admin DBs).

A catalog re-import or a draft promote that changes the server hosts of an API
with credentials bound to an agent is held for operator review
(``credentials:write``):

- catalog re-import without approval → kept as a DRAFT, current revision unchanged;
- operator-approved re-import, unchanged hosts, or no bindings → flows as before;
- promote of a host-changing draft → 403 without ``credentials:write``, allowed with it;
- an https → http downgrade counts as a change, an http → https upgrade does not;
- archiving the current revision first does not skip the check;
- a held draft's hosts are not routable, and pinning it is refused, until promoted.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.services.errors import HostChangeRequiresOperatorError
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.inspect.registry_service import RegistryService
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RevisionPinOutcome
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, ApiRevisionState, StoredCredentialType

pytestmark = pytest.mark.integration

_VENDOR = "hostguard"
_NAME = "widgets"
_VERSION = "1.0.0"
_AGENT_PREFIX = "hostguard-agent"

_WRITER = Identity(sub="usr_writer", actor_type=ActorType.USER, permissions=["apis:write"])
_OPERATOR = Identity(
    sub="usr_operator",
    actor_type=ActorType.USER,
    permissions=["apis:write", "credentials:write"],
)


def _spec(host: str, *, marker: str, scheme: str = "https") -> str:
    return json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "Widgets", "version": _VERSION, "description": marker},
            "servers": [{"url": f"{scheme}://{host}/v1"}],
            "paths": {
                "/widgets": {
                    "get": {
                        "operationId": "listWidgets",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )


def _source(
    host: str, *, marker: str, approved: bool = False, scheme: str = "https"
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "type": "inline",
        "content": _spec(host, marker=marker, scheme=scheme),
        "filename": "openapi.json",
        "vendor": _VENDOR,
        "api_name": _NAME,
        "version": _VERSION,
        "origin": "catalog",
        "submitted_by": _WRITER.sub,
    }
    if approved:
        source["host_change_approved"] = "true"
    return source


@pytest.fixture()
async def clean_state(
    registry_db: DatabaseSession, control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(SecuritySchemeFlow))
            await session.execute(delete(SecurityScheme))
            await session.execute(delete(ServerVariable))
            await session.execute(delete(Server))
            await session.execute(delete(Operation))
            await session.execute(delete(SpecFile))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.commit()
        async with admin_db.session() as session:
            agent_ids = select(Agent.id).where(Agent.name.like(f"{_AGENT_PREFIX}%"))
            await session.execute(
                delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id.in_(agent_ids))
            )
            await session.execute(delete(Agent).where(Agent.name.like(f"{_AGENT_PREFIX}%")))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _run(handler: ImportHandler, source: dict[str, Any]) -> dict[str, Any]:
    result = await handler.execute(
        job_id=f"job_{uuid.uuid4().hex[:20]}",
        session=None,
        payload={"sources": [source]},
        created_by="usr_x",
    )
    revision: dict[str, Any] = result.body["revisions"][0]
    return revision


async def _bind_credential(
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    *,
    wildcard: bool = False,
    bind: bool = True,
) -> None:
    """A credential covering the API, bound to an agent (suspended, to show it still counts)."""
    async with control_db.session() as session:
        credential_id = generate_ksuid("cred")
        credential = Credential(
            id=credential_id,
            type=StoredCredentialType.API_KEY,
            name="cred-hostguard",
            api_vendor=_VENDOR,
            api_name=None if wildcard else _NAME,
            api_version=None if wildcard else _VERSION,
            created_by="usr_x",
        )
        session.add(credential)
        await session.commit()
    if not bind:
        return
    async with admin_db.session() as session:
        agent = Agent(name=f"{_AGENT_PREFIX}-1", registered_by="usr_x")
        session.add(agent)
        await session.flush()
        session.add(
            AgentCredentialBinding(agent_id=agent.id, credential_id=credential_id, suspended=True)
        )
        await session.commit()


async def _api(registry_db: DatabaseSession) -> Api:
    async with registry_db.session() as session:
        return (await session.execute(select(Api).where(Api.vendor == _VENDOR))).scalar_one()


async def _state(registry_db: DatabaseSession, revision_id: str) -> str:
    async with registry_db.session() as session:
        row = (
            await session.execute(
                select(ApiRevision).where(ApiRevision.id == uuid.UUID(revision_id))
            )
        ).scalar_one()
        return row.state


async def _base_then_bind(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    *,
    scheme: str = "https",
) -> tuple[ImportHandler, str]:
    handler = ImportHandler(integration_context)
    base = await _run(handler, _source("old.example.com", marker="base", scheme=scheme))
    assert base["state"] == ApiRevisionState.IMPORTED
    await _bind_credential(control_db, admin_db)
    return handler, base["revision_id"]


async def test_catalog_reimport_host_change_is_held_as_draft(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, base_id = await _base_then_bind(integration_context, control_db, admin_db)

    rev = await _run(handler, _source("new.example.com", marker="moved"))

    assert rev["state"] == ApiRevisionState.DRAFT
    assert rev["held_for_review"] is True
    assert rev["host_change"] == {
        "current_hosts": ["https://old.example.com"],
        "new_hosts": ["https://new.example.com"],
    }
    api = await _api(registry_db)
    assert str(api.current_revision_id) == base_id
    assert await _state(registry_db, base_id) == ApiRevisionState.IMPORTED
    assert await _state(registry_db, rev["revision_id"]) == ApiRevisionState.DRAFT

    async with admin_db.session() as session:
        entry = (
            await session.execute(
                select(AuditEntry).where(
                    AuditEntry.target_id == rev["revision_id"],
                    AuditEntry.reason == "server_host_change_held",
                )
            )
        ).scalar_one()
    assert entry.after is not None
    assert entry.after["host_change"]["new_hosts"] == ["https://new.example.com"]


async def test_catalog_reimport_host_change_held_for_wildcard_credential(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """A vendor-wide credential covers the API too, so the change is held."""
    handler = ImportHandler(integration_context)
    base = await _run(handler, _source("old.example.com", marker="base"))
    await _bind_credential(control_db, admin_db, wildcard=True)

    rev = await _run(handler, _source("new.example.com", marker="moved"))

    assert rev["state"] == ApiRevisionState.DRAFT
    assert str((await _api(registry_db)).current_revision_id) == base["revision_id"]


async def test_operator_approved_reimport_becomes_current(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, base_id = await _base_then_bind(integration_context, control_db, admin_db)

    rev = await _run(handler, _source("new.example.com", marker="moved", approved=True))

    assert rev["state"] == ApiRevisionState.IMPORTED
    assert "held_for_review" not in rev
    assert str((await _api(registry_db)).current_revision_id) == rev["revision_id"]
    assert await _state(registry_db, base_id) == ApiRevisionState.ARCHIVED


async def test_reimport_with_same_hosts_flows_automatically(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, base_id = await _base_then_bind(integration_context, control_db, admin_db)

    rev = await _run(handler, _source("old.example.com", marker="new description"))

    assert rev["state"] == ApiRevisionState.IMPORTED
    assert str((await _api(registry_db)).current_revision_id) == rev["revision_id"]
    assert await _state(registry_db, base_id) == ApiRevisionState.ARCHIVED


async def test_reimport_host_change_without_bindings_flows_automatically(
    integration_context: Context,
    registry_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler = ImportHandler(integration_context)
    await _run(handler, _source("old.example.com", marker="base"))

    rev = await _run(handler, _source("new.example.com", marker="moved"))

    assert rev["state"] == ApiRevisionState.IMPORTED
    assert str((await _api(registry_db)).current_revision_id) == rev["revision_id"]


async def test_promote_held_draft_requires_operator(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, base_id = await _base_then_bind(integration_context, control_db, admin_db)
    held = await _run(handler, _source("new.example.com", marker="moved"))
    svc = RevisionService(integration_context)

    with pytest.raises(HostChangeRequiresOperatorError) as exc_info:
        await svc.promote(_VENDOR, _NAME, _VERSION, held["revision_id"], identity=_WRITER)
    assert exc_info.value.current_hosts == ["https://old.example.com"]
    assert exc_info.value.new_hosts == ["https://new.example.com"]
    assert str((await _api(registry_db)).current_revision_id) == base_id

    view = await svc.promote(_VENDOR, _NAME, _VERSION, held["revision_id"], identity=_OPERATOR)

    assert str(view.current_revision_id) == held["revision_id"]
    # A promoted catalog draft goes live as IMPORTED, so the next catalog
    # re-import archives it rather than colliding with it.
    assert await _state(registry_db, held["revision_id"]) == ApiRevisionState.IMPORTED
    assert await _state(registry_db, base_id) == ApiRevisionState.ARCHIVED

    nxt = await _run(handler, _source("new.example.com", marker="next"))
    assert nxt["state"] == ApiRevisionState.IMPORTED
    assert await _state(registry_db, held["revision_id"]) == ApiRevisionState.ARCHIVED


async def test_promote_manual_draft_host_change_requires_operator(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """The ``apis:write`` draft + promote path is guarded the same way."""
    handler, _ = await _base_then_bind(integration_context, control_db, admin_db)
    draft_source = _source("new.example.com", marker="manual")
    draft_source.pop("origin")
    draft = await _run(handler, draft_source)
    assert draft["state"] == ApiRevisionState.DRAFT
    svc = RevisionService(integration_context)

    with pytest.raises(HostChangeRequiresOperatorError):
        await svc.promote(_VENDOR, _NAME, _VERSION, draft["revision_id"], identity=_WRITER)

    await svc.promote(_VENDOR, _NAME, _VERSION, draft["revision_id"], identity=_OPERATOR)
    assert await _state(registry_db, draft["revision_id"]) == ApiRevisionState.PUBLISHED


async def test_promote_without_host_change_needs_no_operator(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, _ = await _base_then_bind(integration_context, control_db, admin_db)
    draft_source = _source("old.example.com", marker="manual same host")
    draft_source.pop("origin")
    draft = await _run(handler, draft_source)

    view = await RevisionService(integration_context).promote(
        _VENDOR, _NAME, _VERSION, draft["revision_id"], identity=_WRITER
    )

    assert str(view.current_revision_id) == draft["revision_id"]


async def test_scheme_downgrade_is_held_and_upgrade_flows(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, base_id = await _base_then_bind(integration_context, control_db, admin_db)

    downgrade = await _run(handler, _source("old.example.com", marker="plain", scheme="http"))

    assert downgrade["state"] == ApiRevisionState.DRAFT
    assert downgrade["held_for_review"] is True
    assert downgrade["host_change"]["new_hosts"] == ["http://old.example.com"]
    assert str((await _api(registry_db)).current_revision_id) == base_id


async def test_scheme_upgrade_flows_automatically(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, _ = await _base_then_bind(integration_context, control_db, admin_db, scheme="http")

    upgrade = await _run(handler, _source("old.example.com", marker="tls", scheme="https"))

    assert upgrade["state"] == ApiRevisionState.IMPORTED
    assert str((await _api(registry_db)).current_revision_id) == upgrade["revision_id"]


async def test_archiving_current_revision_does_not_skip_reimport_check(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """With nothing current, the last live revision is the baseline."""
    handler, base_id = await _base_then_bind(integration_context, control_db, admin_db)
    await RevisionService(integration_context).archive(
        _VENDOR, _NAME, _VERSION, base_id, identity=_WRITER
    )
    assert (await _api(registry_db)).current_revision_id is None

    rev = await _run(handler, _source("new.example.com", marker="moved"))

    assert rev["state"] == ApiRevisionState.DRAFT
    assert rev["host_change"] == {
        "current_hosts": ["https://old.example.com"],
        "new_hosts": ["https://new.example.com"],
    }
    assert (await _api(registry_db)).current_revision_id is None


async def test_archiving_current_revision_does_not_skip_promote_check(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, base_id = await _base_then_bind(integration_context, control_db, admin_db)
    draft_source = _source("new.example.com", marker="manual")
    draft_source.pop("origin")
    draft = await _run(handler, draft_source)
    svc = RevisionService(integration_context)
    await svc.archive(_VENDOR, _NAME, _VERSION, base_id, identity=_WRITER)

    with pytest.raises(HostChangeRequiresOperatorError) as exc_info:
        await svc.promote(_VENDOR, _NAME, _VERSION, draft["revision_id"], identity=_WRITER)
    assert exc_info.value.current_hosts == ["https://old.example.com"]
    assert (await _api(registry_db)).current_revision_id is None


async def test_held_draft_is_not_routable_or_pinnable_until_promoted(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler, _ = await _base_then_bind(integration_context, control_db, admin_db)
    held = await _run(handler, _source("new.example.com", marker="moved"))
    rev_label = f"rev_{uuid.UUID(held['revision_id']).hex}"

    async with registry_db.session() as session:
        resolver = RegistryService(session)
        old = await resolver.resolve_operation(
            method="GET", url="https://old.example.com/v1/widgets"
        )
        assert old is not None
        assert old.api.vendor == _VENDOR
        assert (
            await resolver.resolve_operation(method="GET", url="https://new.example.com/v1/widgets")
            is None
        )
        # Not even the submitting caller may pin the held revision.
        pin = await resolver.resolve_revision_pin(
            vendor=_VENDOR, name=_NAME, version=_VERSION, rev_label=rev_label, identity=_WRITER
        )
        assert pin.outcome == RevisionPinOutcome.FORBIDDEN
        operator_pin = await resolver.resolve_revision_pin(
            vendor=_VENDOR, name=_NAME, version=_VERSION, rev_label=rev_label, identity=_OPERATOR
        )
        assert operator_pin.outcome == RevisionPinOutcome.RESOLVED

    await RevisionService(integration_context).promote(
        _VENDOR, _NAME, _VERSION, held["revision_id"], identity=_OPERATOR
    )

    async with registry_db.session() as session:
        moved = await RegistryService(session).resolve_operation(
            method="GET", url="https://new.example.com/v1/widgets"
        )
        assert moved is not None


async def test_reimport_host_change_with_unbound_credential_flows_automatically(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """A stored credential that no agent or toolkit binds does not hold the change."""
    handler = ImportHandler(integration_context)
    await _run(handler, _source("old.example.com", marker="base"))
    await _bind_credential(control_db, admin_db, bind=False)

    rev = await _run(handler, _source("new.example.com", marker="moved"))

    assert rev["state"] == ApiRevisionState.IMPORTED
    assert str((await _api(registry_db)).current_revision_id) == rev["revision_id"]
