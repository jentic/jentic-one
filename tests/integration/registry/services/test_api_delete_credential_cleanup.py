"""Integration tests for cross-DB credential cleanup on API delete (#643, #1168).

Deleting an API from the registry must deactivate the control-plane credentials
that reference it by ``(api_vendor, api_name, api_version)`` — otherwise a later
re-import plus a new credential collides with ``409 ambiguous_credential`` and
the stale binding is stranded. Registry and Control are separate databases, so
the cleanup crosses the boundary via raw SQL (no cross-module ORM import).

Agent bindings to those credentials (admin DB) are suspended with reason
``api_deleted`` (#1168), so re-importing a spec under the same identity and
re-activating the credential does not hand the bindings back; the owner lifts
each suspension explicitly with ``:resume``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.broker.repos.rule_evaluator import RuleEvaluator
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.services.api_service import ApiService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

pytestmark = pytest.mark.integration

_VENDOR = "acme643.com"
_NAME = "pets-api"
_VERSION = "v1"

_IDENTITY = Identity(sub="usr_test", actor_type=ActorType.USER, permissions=["org:admin"])
_AGENT_PREFIX = "acme643-agent"
_TOOLKIT_ID = "tk_acme643"


@pytest.fixture()
async def clean_state(
    registry_db: DatabaseSession, control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(Api).where(Api.vendor == _VENDOR))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(
                delete(ToolkitPermissionRule).where(ToolkitPermissionRule.toolkit_id == _TOOLKIT_ID)
            )
            await session.execute(
                delete(ToolkitCredentialBinding).where(
                    ToolkitCredentialBinding.toolkit_id == _TOOLKIT_ID
                )
            )
            await session.execute(delete(Toolkit).where(Toolkit.id == _TOOLKIT_ID))
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


async def _seed_api(registry_db: DatabaseSession) -> None:
    async with registry_db.session() as session:
        session.add(Api(vendor=_VENDOR, name=_NAME, version=_VERSION))
        await session.commit()


async def _seed_credential(
    control_db: DatabaseSession,
    *,
    cred_id: str,
    api_name: str | None = _NAME,
    api_version: str | None = _VERSION,
) -> None:
    async with control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name=f"cred-{cred_id}",
                api_vendor=_VENDOR,
                api_name=api_name,
                api_version=api_version,
                created_by="usr_test",
            )
        )
        await session.commit()


async def _credential_active(control_db: DatabaseSession, cred_id: str) -> bool:
    async with control_db.session() as session:
        result = await session.execute(select(Credential).where(Credential.id == cred_id))
        cred = result.scalar_one()
        return cred.active


async def test_delete_api_deactivates_matching_credential(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Deleting an API deactivates a control credential for the same identity."""
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_match")

    assert await _credential_active(control_db, "cred_match") is True

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    assert await _credential_active(control_db, "cred_match") is False


async def test_delete_api_leaves_other_apis_credentials_active(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Only credentials matching the deleted API identity are deactivated."""
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_match")
    await _seed_credential(control_db, cred_id="cred_other_version", api_version="v2")

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    assert await _credential_active(control_db, "cred_match") is False
    # A credential for a different version of the API is untouched.
    assert await _credential_active(control_db, "cred_other_version") is True


async def _seed_agent(
    admin_db: DatabaseSession, *, tag: str, credential_ids: list[str], suspended: bool = False
) -> str:
    async with admin_db.session() as session:
        agent = Agent(name=f"{_AGENT_PREFIX}-{tag}", registered_by="usr_test")
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


async def _binding(
    admin_db: DatabaseSession, agent_id: str, credential_id: str
) -> AgentCredentialBinding:
    async with admin_db.session() as session:
        result = await session.execute(
            select(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .where(AgentCredentialBinding.credential_id == credential_id)
        )
        return result.scalar_one()


async def _set_credential_active(control_db: DatabaseSession, cred_id: str) -> None:
    async with control_db.session() as session:
        cred = (
            await session.execute(select(Credential).where(Credential.id == cred_id))
        ).scalar_one()
        cred.active = True
        await session.commit()


async def _derived_ids(ctx: Context, agent_id: str) -> set[str]:
    resolver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    derivation = await resolver.derive_credentials(
        agent_id=agent_id, vendor=_VENDOR, name=_NAME, version=_VERSION
    )
    return {c.credential_id for c in derivation.credentials}


async def test_delete_api_suspends_agent_bindings(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Bindings to the deleted API's credentials are suspended with a reason and audited."""
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_exact")
    await _seed_credential(control_db, cred_id="cred_inactive")
    await _seed_credential(control_db, cred_id="cred_vendor_wide", api_name=None, api_version=None)
    async with control_db.session() as session:
        inactive = (
            await session.execute(select(Credential).where(Credential.id == "cred_inactive"))
        ).scalar_one()
        inactive.active = False
        await session.commit()
    agent_id = await _seed_agent(
        admin_db, tag="a", credential_ids=["cred_exact", "cred_inactive", "cred_vendor_wide"]
    )
    manual_id = await _seed_agent(admin_db, tag="m", credential_ids=["cred_exact"], suspended=True)

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    for cred_id in ("cred_exact", "cred_inactive"):
        binding = await _binding(admin_db, agent_id, cred_id)
        assert binding.suspended is True
        assert binding.suspended_reason == "api_deleted"
    # A vendor-wide credential also covers other APIs of the vendor: untouched.
    wide = await _binding(admin_db, agent_id, "cred_vendor_wide")
    assert wide.suspended is False
    assert wide.suspended_reason is None
    # A binding the owner had already suspended keeps its manual (NULL) reason.
    manual = await _binding(admin_db, manual_id, "cred_exact")
    assert manual.suspended is True
    assert manual.suspended_reason is None

    async with admin_db.session() as session:
        entries = (
            (
                await session.execute(
                    select(AuditEntry)
                    .where(AuditEntry.target_type == AuditTargetType.CREDENTIAL_BINDING)
                    .where(AuditEntry.target_parent_id == agent_id)
                    .where(AuditEntry.reason == "api_deleted")
                )
            )
            .scalars()
            .all()
        )
        assert {e.target_id for e in entries} == {"cred_exact", "cred_inactive"}
        assert all(e.action == AuditAction.DISABLE for e in entries)
        api_delete = (
            await session.execute(
                select(AuditEntry)
                .where(AuditEntry.target_type == AuditTargetType.API)
                .where(AuditEntry.action == AuditAction.DELETE)
                .order_by(AuditEntry.occurred_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert api_delete.after == {
            "deactivated_credentials": 1,
            "removed_toolkit_bindings": [],
            "suspended_bindings": 2,
        }


async def test_reimport_does_not_readopt_bindings_until_resumed(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Re-import + credential re-activation leaves the binding out until ``:resume``."""
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_exact")
    agent_id = await _seed_agent(admin_db, tag="r", credential_ids=["cred_exact"])
    assert await _derived_ids(integration_context, agent_id) == {"cred_exact"}

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    # A different spec is imported under the same identity and the credential
    # is switched back on: the old binding must not come back by itself.
    await _seed_api(registry_db)
    await _set_credential_active(control_db, "cred_exact")
    assert await _derived_ids(integration_context, agent_id) == set()

    # Explicit owner/admin action restores it and clears the reason.
    view = await AgentService(integration_context).resume_credential(
        agent_id, credential_id="cred_exact", identity=_IDENTITY
    )
    assert view.suspended is False
    assert view.suspended_reason is None
    assert await _derived_ids(integration_context, agent_id) == {"cred_exact"}


async def test_manual_resuspend_keeps_api_deleted_reason(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Suspending an already ``api_deleted`` binding again does not erase the reason."""
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_exact")
    agent_id = await _seed_agent(admin_db, tag="s", credential_ids=["cred_exact"])

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)
    await AgentService(integration_context).unbind_credential(
        agent_id, credential_id="cred_exact", purge=False, identity=_IDENTITY
    )

    binding = await _binding(admin_db, agent_id, "cred_exact")
    assert binding.suspended is True
    assert binding.suspended_reason == "api_deleted"


async def _seed_toolkit(control_db: DatabaseSession, rules: dict[str, str]) -> None:
    """Bind each credential to one toolkit with a single allow rule on ``path``."""
    async with control_db.session() as session:
        session.add(Toolkit(id=_TOOLKIT_ID, name="acme643-toolkit", created_by="usr_test"))
        await session.flush()
        for credential_id, path in rules.items():
            session.add(
                ToolkitCredentialBinding(
                    toolkit_id=_TOOLKIT_ID, credential_id=credential_id, created_by="usr_test"
                )
            )
            session.add(
                ToolkitPermissionRule(
                    toolkit_id=_TOOLKIT_ID,
                    credential_id=credential_id,
                    effect="allow",
                    methods=["GET"],
                    path=path,
                    match_mode="regex",
                    sequence=0,
                    created_by="usr_test",
                )
            )
        await session.commit()


async def _toolkit_pairs(control_db: DatabaseSession) -> set[str]:
    async with control_db.session() as session:
        bound = (
            await session.execute(
                select(ToolkitCredentialBinding.credential_id).where(
                    ToolkitCredentialBinding.toolkit_id == _TOOLKIT_ID
                )
            )
        ).scalars()
        return set(bound)


async def _toolkit_rule_credentials(control_db: DatabaseSession) -> set[str]:
    async with control_db.session() as session:
        rules = (
            await session.execute(
                select(ToolkitPermissionRule.credential_id).where(
                    ToolkitPermissionRule.toolkit_id == _TOOLKIT_ID
                )
            )
        ).scalars()
        return set(rules)


async def _toolkit_allows(control_db: DatabaseSession, path: str) -> bool:
    evaluation = await RuleEvaluator(control_db, cache_ttl_seconds=0).evaluate(
        toolkit_id=_TOOLKIT_ID, method="GET", path=path, operation_id=None, api_vendor=_VENDOR
    )
    return evaluation.allowed


async def test_delete_api_removes_toolkit_bindings(
    integration_context: Context,
    registry_db: DatabaseSession,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_state: None,
) -> None:
    """Legacy toolkit bindings to the deleted API's credentials go, with their rules.

    The toolkit path pools a toolkit's rules per vendor, so a surviving rule
    authored for the deleted API would keep authorizing requests through the
    toolkit's vendor-wide credential, and re-activating the exact credential
    would bring its toolkit access straight back.
    """
    await _seed_api(registry_db)
    await _seed_credential(control_db, cred_id="cred_exact")
    await _seed_credential(control_db, cred_id="cred_vendor_wide", api_name=None, api_version=None)
    await _seed_toolkit(control_db, {"cred_exact": "/pets.*", "cred_vendor_wide": "/stores.*"})
    assert await _toolkit_allows(control_db, "/pets/1") is True

    await ApiService(integration_context).delete(_VENDOR, _NAME, _VERSION, identity=_IDENTITY)

    assert await _toolkit_pairs(control_db) == {"cred_vendor_wide"}
    assert await _toolkit_rule_credentials(control_db) == {"cred_vendor_wide"}
    # The deleted API's rule no longer lends itself to the vendor-wide
    # credential; that credential's own rule still applies.
    assert await _toolkit_allows(control_db, "/pets/1") is False
    assert await _toolkit_allows(control_db, "/stores/1") is True

    async with admin_db.session() as session:
        api_delete = (
            await session.execute(
                select(AuditEntry)
                .where(AuditEntry.target_type == AuditTargetType.API)
                .where(AuditEntry.action == AuditAction.DELETE)
                .order_by(AuditEntry.occurred_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert api_delete.after is not None
        assert api_delete.after["removed_toolkit_bindings"] == [
            {"toolkit_id": _TOOLKIT_ID, "credential_id": "cred_exact"}
        ]
