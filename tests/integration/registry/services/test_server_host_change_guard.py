"""Integration tests for the server-host change guard (real registry/control/admin DBs).

A catalog re-import or a draft promote that changes the server hosts of an API
with credentials bound to an agent is held for an organisation admin
(``org:admin``):

- catalog re-import without approval → kept as a DRAFT, current revision unchanged;
- admin-approved re-import, unchanged hosts, or no bindings → flows as before;
- promote of a host-changing draft → 403 without ``org:admin`` (``credentials:write``
  is not enough), allowed with it;
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
from jentic_one.broker.core.errors import UnauthorizedRevisionPinError
from jentic_one.broker.services.discovery import discover_via_pins
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
from jentic_one.shared.broker.protocols import RevisionPinOutcome, RevisionPinResult
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, ApiRevisionState, StoredCredentialType
from jentic_one.wiring import InProcessRegistryResolver

pytestmark = pytest.mark.integration

_VENDOR = "hostguard"
_NAME = "widgets"
_VERSION = "1.0.0"
_AGENT_PREFIX = "hostguard-agent"

_WRITER = Identity(sub="usr_writer", actor_type=ActorType.USER, permissions=["apis:write"])
#: Holds ``credentials:write``, which does *not* approve a host change. Every
#: assertion below expects this identity to be refused; ``_ADMIN`` is the one
#: that may promote or pin.
_CREDENTIALS_WRITER = Identity(
    sub="usr_credentials_writer",
    actor_type=ActorType.USER,
    permissions=["apis:write", "credentials:write"],
)
_ADMIN = Identity(sub="usr_admin", actor_type=ActorType.USER, permissions=["org:admin"])

#: Phrases that would reveal whether the API has a bound credential or an open
#: connect session. Matched as phrases, never as bare words: the refusal detail
#: interpolates host names, so "bound" alone matches ``outbound.example.com``.
_BINDING_PHRASES = ("bound credential", "open session", "credential binding", "connect session")


def _leaks_binding(detail: str) -> bool:
    """True when ``detail`` names a credential binding or connect session."""
    lowered = detail.lower()
    return any(phrase in lowered for phrase in _BINDING_PHRASES)


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
        db=None,
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


async def test_admin_approved_reimport_becomes_current(
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


async def test_promote_held_draft_requires_org_admin(
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
    # ``credentials:write`` does not approve sending other users' credentials elsewhere.
    with pytest.raises(HostChangeRequiresOperatorError):
        await svc.promote(
            _VENDOR, _NAME, _VERSION, held["revision_id"], identity=_CREDENTIALS_WRITER
        )
    assert str((await _api(registry_db)).current_revision_id) == base_id

    view = await svc.promote(_VENDOR, _NAME, _VERSION, held["revision_id"], identity=_ADMIN)

    assert str(view.current_revision_id) == held["revision_id"]
    # A promoted catalog draft goes live as IMPORTED, so the next catalog
    # re-import archives it rather than colliding with it.
    assert await _state(registry_db, held["revision_id"]) == ApiRevisionState.IMPORTED
    assert await _state(registry_db, base_id) == ApiRevisionState.ARCHIVED

    nxt = await _run(handler, _source("new.example.com", marker="next"))
    assert nxt["state"] == ApiRevisionState.IMPORTED
    assert await _state(registry_db, held["revision_id"]) == ApiRevisionState.ARCHIVED


async def test_promote_manual_draft_host_change_requires_org_admin(
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

    for identity in (_WRITER, _CREDENTIALS_WRITER):
        with pytest.raises(HostChangeRequiresOperatorError) as refused:
            await svc.promote(_VENDOR, _NAME, _VERSION, draft["revision_id"], identity=identity)
        # The detail states the rule, not whether anyone has bound the API's
        # credentials. Phrase-level, not bare "bound"/"session": the message
        # interpolates host names, so a host such as ``outbound.example.com``
        # would trip a substring check.
        detail = str(refused.value)
        assert "org:admin" in detail
        assert not _leaks_binding(detail)
    assert await _state(registry_db, draft["revision_id"]) == ApiRevisionState.DRAFT

    await svc.promote(_VENDOR, _NAME, _VERSION, draft["revision_id"], identity=_ADMIN)
    assert await _state(registry_db, draft["revision_id"]) == ApiRevisionState.PUBLISHED


async def test_promote_without_host_change_needs_no_admin(
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
        credentials_writer_pin = await resolver.resolve_revision_pin(
            vendor=_VENDOR,
            name=_NAME,
            version=_VERSION,
            rev_label=rev_label,
            identity=_CREDENTIALS_WRITER,
        )
        assert credentials_writer_pin.outcome == RevisionPinOutcome.FORBIDDEN
        admin_pin = await resolver.resolve_revision_pin(
            vendor=_VENDOR, name=_NAME, version=_VERSION, rev_label=rev_label, identity=_ADMIN
        )
        assert admin_pin.outcome == RevisionPinOutcome.RESOLVED

    await RevisionService(integration_context).promote(
        _VENDOR, _NAME, _VERSION, held["revision_id"], identity=_ADMIN
    )

    async with registry_db.session() as session:
        moved = await RegistryService(session).resolve_operation(
            method="GET", url="https://new.example.com/v1/widgets"
        )
        assert moved is not None


async def test_own_draft_with_new_hosts_is_not_pinnable_on_a_bound_api(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """A submitter's own draft (no origin) is held by the host guard when pinned.

    Pinning routes calls through the draft without a promote, so a draft that
    moves the API to new hosts would carry its bound credentials there. Only an
    organisation admin may pin it (``credentials:write`` is not enough); a draft
    on the live hosts still resolves for its owner.
    """
    handler, _ = await _base_then_bind(integration_context, control_db, admin_db)
    # The submitter itself, also holding ``credentials:write`` or ``org:admin``.
    writer_credentials = Identity(
        sub=_WRITER.sub, actor_type=ActorType.USER, permissions=_CREDENTIALS_WRITER.permissions
    )
    writer_admin = Identity(
        sub=_WRITER.sub, actor_type=ActorType.USER, permissions=_ADMIN.permissions
    )
    moved_source = _source("new.example.com", marker="manual-moved")
    moved_source.pop("origin")
    moved = await _run(handler, moved_source)
    same_source = _source("old.example.com", marker="manual-same")
    same_source.pop("origin")
    same = await _run(handler, same_source)
    assert moved["state"] == same["state"] == ApiRevisionState.DRAFT

    async def _pin(revision_id: str, identity: Identity) -> RevisionPinResult:
        async with registry_db.session() as session:
            return await RegistryService(session, ctx=integration_context).resolve_revision_pin(
                vendor=_VENDOR,
                name=_NAME,
                version=_VERSION,
                rev_label=f"rev_{uuid.UUID(revision_id).hex}",
                identity=identity,
            )

    held = await _pin(moved["revision_id"], _WRITER)
    assert held.outcome is RevisionPinOutcome.HOST_CHANGE_HELD
    assert held.revision_id == uuid.UUID(moved["revision_id"])
    assert (
        await _pin(moved["revision_id"], writer_credentials)
    ).outcome is RevisionPinOutcome.HOST_CHANGE_HELD
    assert (await _pin(moved["revision_id"], writer_admin)).outcome is RevisionPinOutcome.RESOLVED
    assert (await _pin(same["revision_id"], _WRITER)).outcome is RevisionPinOutcome.RESOLVED

    # The broker refuses the held draft when it is the one serving the URL.
    resolver = InProcessRegistryResolver(registry_db, ctx=integration_context)
    pins = {(_VENDOR, _NAME, _VERSION): f"rev_{uuid.UUID(moved['revision_id']).hex}"}
    for identity in (_WRITER, writer_credentials):
        with pytest.raises(UnauthorizedRevisionPinError) as refused:
            await discover_via_pins(
                resolver,
                method="GET",
                url="https://new.example.com/v1/widgets",
                pins=pins,
                identity=identity,
            )
        assert refused.value.type == "host_change_requires_operator"
        assert not _leaks_binding(refused.value.detail)
    routed = await discover_via_pins(
        resolver,
        method="GET",
        url="https://new.example.com/v1/widgets",
        pins=pins,
        identity=writer_admin,
    )
    assert routed is not None and routed[1] == uuid.UUID(moved["revision_id"])


async def test_own_draft_with_new_hosts_is_pinnable_when_nothing_is_bound(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    handler = ImportHandler(integration_context)
    await _run(handler, _source("old.example.com", marker="base"))
    await _bind_credential(control_db, admin_db, bind=False)
    moved_source = _source("new.example.com", marker="manual-moved")
    moved_source.pop("origin")
    moved = await _run(handler, moved_source)
    async with registry_db.session() as session:
        pin = await RegistryService(session, ctx=integration_context).resolve_revision_pin(
            vendor=_VENDOR,
            name=_NAME,
            version=_VERSION,
            rev_label=f"rev_{uuid.UUID(moved['revision_id']).hex}",
            identity=_WRITER,
        )
    assert pin.outcome is RevisionPinOutcome.RESOLVED


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
