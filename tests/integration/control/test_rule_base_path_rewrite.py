"""Integration tests for the ``rewrite-rule-base-paths`` job (#1424).

Seeds real registry (api → live revision → server + operations), control
(credentials, inline rules, a shared rule set) and admin (bindings) rows, runs
``RuleBasePathRewriteService`` with the real registry reader, and asserts
which rules are rewritten, which are reported, and that a re-run is a no-op.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)
from jentic_one.control.services.rule_base_path_rewrite import RuleBasePathRewriteService
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.services.api_path_shapes import RegistryApiPathShapeReader
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import StoredCredentialType

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    ctx = integration_context

    async def _truncate() -> None:
        async with ctx.admin_db.session() as session:
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(
                delete(AuditEntry).where(AuditEntry.reason == "rule_base_path_rewrite")
            )
            await session.commit()
        async with ctx.control_db.session() as session:
            await session.execute(delete(AgentPermissionRule))
            await session.execute(delete(PermissionRuleSetRule))
            await session.execute(delete(PermissionRuleSet))
            await session.execute(delete(Credential))
            await session.commit()
        async with ctx.registry_db.session() as session:
            await session.execute(delete(ServerVariable))
            await session.execute(delete(Server))
            await session.execute(delete(Operation))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api(
    ctx: Context,
    *,
    vendor: str,
    server_url: str,
    variables: dict[str, list[str]] | None,
    templates: list[str],
) -> None:
    api = Api(vendor=vendor, name="api", version="1.0.0")
    async with ctx.registry_db.session() as session:
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="published", source_type="url")
        session.add(revision)
        await session.flush()
        api.current_revision_id = revision.id
        server = Server(revision_id=revision.id, url=server_url)
        session.add(server)
        await session.flush()
        for name, enum in (variables or {}).items():
            session.add(
                ServerVariable(server_id=server.id, name=name, default_value=enum[0], enum=enum)
            )
        await OperationRepository.bulk_create(
            session,
            revision.id,
            [OperationInput(path=t, method="GET") for t in templates],
            created_by="usr_test",
        )
        await session.commit()


async def _seed_credential(ctx: Context, vendor: str) -> str:
    credential = Credential(
        type=StoredCredentialType.API_KEY,
        name=f"{vendor}-key",
        api_vendor=vendor,
        api_name="api",
        api_version="1.0.0",
    )
    async with ctx.control_db.session() as session:
        session.add(credential)
        await session.commit()
        return credential.id


async def _seed_rule(
    ctx: Context, credential_id: str, *, path: str, mode: str, sequence: int, agent_id: str
) -> str:
    rule = AgentPermissionRule(
        agent_id=agent_id,
        credential_id=credential_id,
        effect="allow",
        methods=["GET"],
        path=path,
        match_mode=mode,
        sequence=sequence,
    )
    async with ctx.control_db.session() as session:
        session.add(rule)
        await session.commit()
        return rule.id


async def _bind(
    ctx: Context, agent_id: str, credential_id: str, rule_set_id: str | None = None
) -> str:
    binding_id = generate_ksuid("acb")
    async with ctx.admin_db.session() as session:
        session.add(
            AgentCredentialBinding(
                id=binding_id,
                agent_id=agent_id,
                credential_id=credential_id,
                rule_set_id=rule_set_id,
            )
        )
        await session.commit()
    return binding_id


async def _rule_paths(ctx: Context) -> dict[str, str | None]:
    async with ctx.control_db.session() as session:
        inline = (
            await session.execute(select(AgentPermissionRule.id, AgentPermissionRule.path))
        ).all()
        shared = (
            await session.execute(select(PermissionRuleSetRule.id, PermissionRuleSetRule.path))
        ).all()
    paths: dict[str, str | None] = {}
    for rid, path in [*inline, *shared]:
        paths[rid] = path
    return paths


def _svc(ctx: Context) -> RuleBasePathRewriteService:
    return RuleBasePathRewriteService(ctx, shapes=RegistryApiPathShapeReader(ctx))


async def test_rewrites_base_path_rules_and_reports_the_rest(
    integration_context: Context, clean_tables: None
) -> None:
    ctx = integration_context
    await _seed_api(
        ctx,
        vendor="widgets-example-com",
        server_url="http://{host}:18765/{region}",
        variables={"region": ["eu", "us"]},
        templates=["/widgets", "/widgets/{id}"],
    )
    await _seed_api(
        ctx,
        vendor="petstore-example-com",
        server_url="https://petstore.example.com/api/v3",
        variables=None,
        templates=["/pet/{petId}"],
    )
    widgets_cred = await _seed_credential(ctx, "widgets-example-com")
    pet_cred = await _seed_credential(ctx, "petstore-example-com")
    await _bind(ctx, "agt_a", widgets_cred)
    pet_binding = await _bind(ctx, "agt_a", pet_cred)

    templated = await _seed_rule(
        ctx, widgets_cred, path="/eu/widgets", mode="prefix", sequence=0, agent_id="agt_a"
    )
    relative = await _seed_rule(
        ctx, widgets_cred, path="/widgets", mode="prefix", sequence=1, agent_id="agt_a"
    )
    static = await _seed_rule(
        ctx, pet_cred, path="/api/v3/pet/9", mode="exact", sequence=0, agent_id="agt_a"
    )
    regex = await _seed_rule(
        ctx, pet_cred, path="/api/v3/pet/.*", mode="regex", sequence=1, agent_id="agt_a"
    )

    # Shared rule set attached to bindings on both APIs: its APIs disagree.
    async with ctx.control_db.session() as session:
        rule_set = PermissionRuleSet(name="mixed")
        session.add(rule_set)
        await session.flush()
        shared_rule = PermissionRuleSetRule(
            rule_set_id=rule_set.id,
            effect="allow",
            methods=["GET"],
            path="/api/v3/pet",
            match_mode="prefix",
            sequence=0,
        )
        session.add(shared_rule)
        await session.commit()
        rule_set_id, shared_rule_id = rule_set.id, shared_rule.id
    for agent_id, cred in (("agt_b", widgets_cred), ("agt_c", pet_cred)):
        await _bind(ctx, agent_id, cred, rule_set_id)
    # A rule set attached to nothing, and an inline rule whose binding is gone.
    async with ctx.control_db.session() as session:
        unattached = PermissionRuleSet(name="unattached")
        session.add(unattached)
        await session.flush()
        unattached_rule = PermissionRuleSetRule(
            rule_set_id=unattached.id,
            effect="allow",
            methods=["GET"],
            path="/api/v3/pet",
            match_mode="prefix",
            sequence=0,
        )
        session.add(unattached_rule)
        await session.commit()
        unattached_rule_id = unattached_rule.id
    orphan = await _seed_rule(
        ctx, pet_cred, path="/api/v3/pet", mode="prefix", sequence=0, agent_id="agt_gone"
    )

    preview = await _svc(ctx).run(diff_only=True)
    assert preview.rewritten == 1
    assert await _rule_paths(ctx) == {
        templated: "/eu/widgets",
        relative: "/widgets",
        static: "/api/v3/pet/9",
        regex: "/api/v3/pet/.*",
        shared_rule_id: "/api/v3/pet",
        unattached_rule_id: "/api/v3/pet",
        orphan: "/api/v3/pet",
    }, "--diff-only must not write"

    run = await _svc(ctx).run(diff_only=False)

    assert run.rewritten == 1
    assert await _rule_paths(ctx) == {
        # A server-variable base is never auto-stripped: ``/widgets`` would
        # allow every region where ``/eu/widgets`` allowed only ``eu``.
        templated: "/eu/widgets",
        relative: "/widgets",
        static: "/pet/9",
        regex: "/api/v3/pet/.*",
        shared_rule_id: "/api/v3/pet",
        unattached_rule_id: "/api/v3/pet",
        orphan: "/api/v3/pet",
    }
    skipped = {
        f.detail["rule_id"]: f.detail["reason"] for f in run.findings if f.category == "skipped"
    }
    assert skipped == {
        templated: "server_variable_base",
        regex: "regex_not_rewritable",
        shared_rule_id: "rule_set_mixed_apis",
        unattached_rule_id: "rule_set_not_attached",
        orphan: "binding_not_found",
    }
    assert run.skipped == len(skipped)

    # The rewrite is audited against the binding id, not an agent:credential pair.
    async with ctx.admin_db.session() as session:
        audits = (
            (
                await session.execute(
                    select(AuditEntry).where(AuditEntry.reason == "rule_base_path_rewrite")
                )
            )
            .scalars()
            .all()
        )
    assert [(a.target_id, a.target_parent_id) for a in audits] == [(pet_binding, "agt_a")]
    assert audits[0].before == {"rule_id": static, "path": "/api/v3/pet/9"}
    assert audits[0].after == {"rule_id": static, "path": "/pet/9"}

    again = await _svc(ctx).run(diff_only=False)
    assert again.rewritten == 0


async def test_failed_audit_rolls_back_the_rewrite(
    integration_context: Context, clean_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No policy change without its audit record: the audit write gates the update."""
    ctx = integration_context
    await _seed_api(
        ctx,
        vendor="petstore-example-com",
        server_url="https://petstore.example.com/api/v3",
        variables=None,
        templates=["/pet/{petId}"],
    )
    cred = await _seed_credential(ctx, "petstore-example-com")
    await _bind(ctx, "agt_a", cred)
    rule = await _seed_rule(
        ctx, cred, path="/api/v3/pet", mode="prefix", sequence=0, agent_id="agt_a"
    )

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit store down")

    monkeypatch.setattr("jentic_one.control.services.rule_base_path_rewrite.record_audit", _boom)

    run = await _svc(ctx).run(diff_only=False)

    assert run.rewritten == 0
    assert [(f.category, f.detail["reason"]) for f in run.findings] == [
        ("conflict", "write_failed")
    ]
    assert (await _rule_paths(ctx))[rule] == "/api/v3/pet"
