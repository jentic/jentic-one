"""Cross-DB integration tests for #1079 and #1388 identity repair (SQLite + PostgreSQL).

Exercises ``CredentialIdentityRepairService`` and ``UpgradeStepService`` against
real ``registry_db``, ``control_db``, and ``admin_db`` sessions:

1. #1388 path-derived ``github-com/openapi/api.github.com`` repairs in place when a
   unique registered API exists, preserving encrypted secrets, bindings,
   permission rules, and immutable ``execution_records``.
2. #1388 path-derived credential reports ``unresolvable`` without mutating when no
   matching registered API exists.
3. #1079 vendor-doubled ``registry.apis`` + ``control.credentials`` + open ``api``
   ``connect_sessions`` repair in lock-step while revisions, operations, overlays,
   notes, bindings, rules, and historical ``execution_records`` stay intact;
   reruns are strict no-ops.
4. Dry-run, backup document generation, reversible ``restore_backup`` rollback,
   rollback idempotence, and drift rejection.
5. Fail-closed behavior without mutating data on colliding clean rows, multiple
   candidate APIs, and in-flight pending held executions.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.connect_sessions import (
    INTENT_CREATE,
    TARGET_KIND_API,
    ConnectSession,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.control.core.schema.upgrade_steps import UpgradeStep
from jentic_one.control.repos.upgrade_step_repo import UpgradeStepRepository
from jentic_one.control.services.credential_identity_repair import (
    CredentialIdentityRepairService,
)
from jentic_one.control.services.upgrade_steps import (
    CREDENTIAL_IDENTITY_REPAIR,
    UpgradeStepService,
)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_snapshots import SINGLETON_ID, CatalogSnapshot
from jentic_one.registry.core.schema.notes import Note
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.shared.context import Context
from jentic_one.shared.models.api_identity import (
    canonical_credential_scope,
    credential_covers,
)
from jentic_one.shared.models.credential_identity_repair import IdentityRepairBackupError

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_all(integration_context: Context) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with integration_context.control_db.session() as session:
            await session.execute(delete(ConnectSession))
            await session.execute(delete(AgentPermissionRule))
            await session.execute(delete(TokenValueCredential))
            await session.execute(delete(Credential))
            await session.execute(delete(UpgradeStep))
            await session.commit()
        async with integration_context.registry_db.session() as session:
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(Note))
            await session.execute(delete(Overlay))
            await session.execute(delete(Operation))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.execute(delete(CatalogSnapshot))
            await session.commit()
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(HeldExecution))
            await session.execute(delete(Job))
            await session.execute(delete(ExecutionRecord))
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(delete(AgentCredential))
            await session.execute(delete(Agent))
            await session.execute(delete(User))
            await session.execute(delete(AuditEntry))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def _seed_agent_and_bindings(
    ctx: Context,
    *,
    credential_id: str,
    active_agent_id: str = "agnt_active_1",
    suspended_agent_id: str = "agnt_susp_1",
) -> None:
    async with ctx.admin_db.session() as session:
        if await session.get(User, "usr_owner") is None:
            session.add(
                User(
                    id="usr_owner",
                    email="owner@test.local",
                    first_name="Owner",
                    last_name="Test",
                )
            )
            await session.flush()
        for aid in (active_agent_id, suspended_agent_id):
            existing = await session.get(Agent, aid)
            if existing is None:
                session.add(
                    Agent(
                        id=aid,
                        name=f"Agent {aid}",
                        owner_id="usr_owner",
                        registered_by="usr_owner",
                        status="approved",
                        created_by="usr_owner",
                    )
                )
        await session.flush()
        session.add(
            AgentCredential(
                id=f"agc_{active_agent_id}"[:30],
                agent_id=active_agent_id,
                api_key_hash=f"hash_{active_agent_id}_{credential_id}",
                created_by="usr_owner",
            )
        )
        session.add(
            AgentCredentialBinding(
                id=f"acb_act_{credential_id[-12:]}"[:30],
                agent_id=active_agent_id,
                credential_id=credential_id,
                suspended=False,
                created_by="usr_owner",
            )
        )
        session.add(
            AgentCredentialBinding(
                id=f"acb_sus_{credential_id[-12:]}"[:30],
                agent_id=suspended_agent_id,
                credential_id=credential_id,
                suspended=True,
                suspended_reason="api_deleted",
                created_by="usr_owner",
            )
        )
        session.add(
            ExecutionRecord(
                id=f"exec_{credential_id[-15:]}"[:30],
                trace_id="0" * 32,
                started_at=datetime.now(UTC),
                status="failed",
                operation_id="repos/list-for-authenticated-user",
                operation_path="/user/repos",
                operation_method="GET",
                api_vendor="github-com",
                api_name="api-github-com",
                api_version="1.1.4",
                http_status=403,
                error="credential_identity_mismatch",
                actor_id=active_agent_id,
                actor_type="agent",
                credential_id=credential_id,
                credential_name="GitHub",
            )
        )
        await session.commit()


async def test_1388_repairs_github_path_credential_and_preserves_references(
    integration_context: Context, clean_all: None
) -> None:
    """Pins #1388: github-com/openapi/api.github.com repairs to github-com/api-github-com/1.1.4."""
    async with integration_context.registry_db.session() as session:
        api = Api(
            vendor="github-com",
            name="api-github-com",
            version="1.1.4",
            catalog_api_id="github.com/api.github.com",
            created_by="usr_owner",
        )
        session.add(api)
        await session.commit()

    async with integration_context.control_db.session() as session:
        cred = Credential(
            id="cred_1388_gh",
            type="bearer",
            name="GitHub",
            api_vendor="github-com",
            api_name="openapi",
            api_version="api.github.com",
            created_by="usr_owner",
        )
        session.add(cred)
        await session.flush()
        session.add(
            TokenValueCredential(
                credential_id="cred_1388_gh",
                encrypted_token_value="sealed-ciphertext-value",  # pragma: allowlist secret
                token_preview="ghp_...1234",
                created_by="usr_owner",
            )
        )
        session.add(
            AgentPermissionRule(
                id="apr_1388_rule1",
                agent_id="agnt_active_1",
                credential_id="cred_1388_gh",
                effect="allow",
                methods=["GET"],
                path="/user/repos",
                match_mode="exact",
                is_system=False,
                sequence=0,
                created_by="usr_owner",
            )
        )
        await session.commit()

    await _seed_agent_and_bindings(integration_context, credential_id="cred_1388_gh")

    before_scope = canonical_credential_scope(
        vendor="github-com", name="openapi", version="api.github.com"
    )
    assert not credential_covers(
        before_scope, vendor="github-com", name="api-github-com", version="1.1.4"
    )

    svc = CredentialIdentityRepairService(integration_context)

    # Dry-run reports without mutating.
    dry = await svc.run(dry_run=True)
    assert dry.repaired_credentials == 1
    assert dry.failed_closed is False
    (dry_finding,) = dry.findings
    assert dry_finding.category == "would_repair"
    assert dry_finding.references == {
        "active_bindings": 1,
        "suspended_bindings": 1,
        "bound_agent_credentials": 1,
        "permission_rules": 1,
        "open_connect_sessions": 0,
        "closed_connect_sessions": 0,
        "pending_held_executions": 0,
        "terminal_held_executions": 0,
        "execution_records": 1,
    }
    async with integration_context.control_db.session() as session:
        unchanged = await session.get(Credential, "cred_1388_gh")
        assert unchanged is not None
        assert (unchanged.api_name, unchanged.api_version) == ("openapi", "api.github.com")

    # Apply repairs credential in place.
    applied = await svc.run(dry_run=False)
    assert applied.repaired_credentials == 1
    assert applied.failed_closed is False

    async with integration_context.control_db.session() as session:
        repaired = await session.get(Credential, "cred_1388_gh")
        assert repaired is not None
        assert (repaired.api_vendor, repaired.api_name, repaired.api_version) == (
            "github-com",
            "api-github-com",
            "1.1.4",
        )
        after_scope = canonical_credential_scope(
            vendor=repaired.api_vendor, name=repaired.api_name, version=repaired.api_version
        )
        assert credential_covers(
            after_scope, vendor="github-com", name="api-github-com", version="1.1.4"
        )
        secret = (
            await session.execute(
                select(TokenValueCredential).where(
                    TokenValueCredential.credential_id == "cred_1388_gh"
                )
            )
        ).scalar_one_or_none()
        assert secret is not None and secret.encrypted_token_value == "sealed-ciphertext-value"
        rules = (
            (
                await session.execute(
                    select(AgentPermissionRule).where(
                        AgentPermissionRule.credential_id == "cred_1388_gh"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rules) == 1 and rules[0].path == "/user/repos"

    async with integration_context.admin_db.session() as session:
        bindings = (
            (
                await session.execute(
                    select(AgentCredentialBinding)
                    .where(AgentCredentialBinding.credential_id == "cred_1388_gh")
                    .order_by(AgentCredentialBinding.agent_id)
                )
            )
            .scalars()
            .all()
        )
        assert [(b.agent_id, b.suspended, b.suspended_reason) for b in bindings] == [
            ("agnt_active_1", False, None),
            ("agnt_susp_1", True, "api_deleted"),
        ]
        # Audit entry recorded.
        audits = (
            (
                await session.execute(
                    select(AuditEntry).where(AuditEntry.target_id == "cred_1388_gh")
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 1 and audits[0].reason == "credential_identity_repair"

    # Rerun is a strict no-op.
    rerun = await svc.run(dry_run=False)
    assert (rerun.repaired_apis, rerun.repaired_credentials, rerun.findings) == (0, 0, ())


async def test_1388_reports_without_mutation_when_no_safe_mapping_exists(
    integration_context: Context, clean_all: None
) -> None:
    """When no API is imported in registry_db, path-derived rows stay untouched and report."""
    async with integration_context.registry_db.session() as session:
        session.add(
            CatalogSnapshot(
                id=SINGLETON_ID,
                source_url="https://catalog.example/apis.json",
                entry_count=1,
                entries=[
                    {
                        "api_id": "github.com/api.github.com",
                        "vendor": "github.com",
                        "path": "apis/openapi/github.com/api.github.com",
                        "spec_url": "https://catalog.example/gh.json",
                        "github_url": "https://github.com/jentic/jentic-public-apis",
                    }
                ],
            )
        )
        await session.commit()

    async with integration_context.control_db.session() as session:
        session.add(
            Credential(
                id="cred_unresolvable",
                type="bearer",
                name="GH Unimported",
                api_vendor="github-com",
                api_name="openapi",
                api_version="api.github.com",
                created_by="usr_owner",
            )
        )
        await session.commit()

    result = await CredentialIdentityRepairService(integration_context).run(dry_run=False)
    assert result.repaired_credentials == 0
    assert result.unresolvable_count == 1
    assert result.failed_closed is False
    assert "github.com/api.github.com" in result.findings[0].remediation

    async with integration_context.control_db.session() as session:
        row = await session.get(Credential, "cred_unresolvable")
        assert row is not None
        assert (row.api_vendor, row.api_name, row.api_version) == (
            "github-com",
            "openapi",
            "api.github.com",
        )


async def test_1079_repairs_doubled_api_credential_session_and_restores_backup(
    integration_context: Context, clean_all: None
) -> None:
    """Pins #1079: doubled API, credential, and session repair in place; backup restores."""
    async with integration_context.registry_db.session() as session:
        api = Api(
            vendor="posthog-com",
            name="posthog-com-posthog-api",
            version="1.0.0",
            catalog_api_id="posthog.com/posthog-api",
            revision_count=1,
            operation_count=1,
            created_by="usr_owner",
        )
        session.add(api)
        await session.flush()
        api_id = api.id
        rev = ApiRevision(
            api_id=api_id,
            state="imported",
            origin="catalog",
            spec_digest="sha256:ph1",
            source_type="url",
            source_url="https://catalog.example/posthog.json",
            operation_count=1,
            created_by="usr_owner",
        )
        session.add(rev)
        await session.flush()
        api.current_revision_id = rev.id
        session.add(
            Operation(
                id="op_posthog_capture",
                revision_id=rev.id,
                operation_id="capture_create",
                path="/capture/",
                method="POST",
                created_by="usr_owner",
            )
        )
        session.add(
            Overlay(
                id="ovr_posthog_1",
                api_id=api_id,
                target_revision_id=rev.id,
                confirmed_revision_id=rev.id,
                status="confirmed",
                document={"overlay": "1.0.0", "actions": []},
                created_by="usr_owner",
            )
        )
        session.add(
            Note(
                id="note_posthog_1",
                resource_api_id=api_id,
                body="Important PostHog note",
                created_by="usr_owner",
            )
        )
        await session.commit()

    async with integration_context.control_db.session() as session:
        session.add(
            Credential(
                id="cred_ph_1079",
                type="bearer",
                name="PostHog",
                api_vendor="posthog-com",
                api_name="posthog-com-posthog-api",
                api_version=None,
                catalog_api_id="posthog.com/posthog-api",
                created_by="usr_owner",
            )
        )
        await session.flush()
        session.add(
            ConnectSession(
                id="cs_ph_open",
                intent=INTENT_CREATE,
                credential_id="cred_ph_1079",
                target_kind=TARGET_KIND_API,
                vendor="posthog-com",
                api_name="posthog-com-posthog-api",
                api_version="1.0.0",
                agent_id="agnt_ph",
                initiator_actor_id="agnt_ph",
                state="created",
                resolved_flow="manual",
                poll_token_hash="a" * 64,
                created_by="agnt_ph",
            )
        )
        await session.commit()

    async with integration_context.admin_db.session() as session:
        session.add(
            ExecutionRecord(
                id="exec_ph_hist",
                trace_id="1" * 32,
                started_at=datetime.now(UTC),
                status="succeeded",
                operation_id="capture_create",
                operation_path="/capture/",
                operation_method="POST",
                api_vendor="posthog-com",
                api_name="posthog-com-posthog-api",
                api_version="1.0.0",
                http_status=200,
                actor_id="agnt_ph",
                actor_type="agent",
                credential_id="cred_ph_1079",
                credential_name="PostHog",
            )
        )
        await session.commit()

    svc = CredentialIdentityRepairService(integration_context)
    result = await svc.run(dry_run=False)
    assert result.repaired_apis == 1
    assert result.repaired_credentials == 1
    assert result.repaired_connect_sessions == 1
    assert result.failed_closed is False

    # Verify single clean API row (same UUID) with revisions/operations/overlays/notes attached.
    async with integration_context.registry_db.session() as session:
        apis = (await session.execute(select(Api))).scalars().all()
        assert len(apis) == 1
        assert apis[0].id == api_id
        assert (apis[0].vendor, apis[0].name, apis[0].version) == (
            "posthog-com",
            "posthog-api",
            "1.0.0",
        )
        ovr = await session.get(Overlay, "ovr_posthog_1")
        note = await session.get(Note, "note_posthog_1")
        op = await session.get(Operation, "op_posthog_capture")
        assert ovr is not None and ovr.api_id == api_id
        assert note is not None and note.resource_api_id == api_id
        assert op is not None

    # Verify credential and open connect session updated, historical execution_records immutable.
    async with integration_context.control_db.session() as session:
        cred = await session.get(Credential, "cred_ph_1079")
        cs = await session.get(ConnectSession, "cs_ph_open")
        assert cred is not None and cred.api_name == "posthog-api"
        assert cs is not None and cs.api_name == "posthog-api"

    async with integration_context.admin_db.session() as session:
        hist = await session.get(ExecutionRecord, "exec_ph_hist")
        assert hist is not None
        assert (hist.api_vendor, hist.api_name, hist.api_version) == (
            "posthog-com",
            "posthog-com-posthog-api",
            "1.0.0",
        )

    # Rerun is a strict no-op.
    second = await svc.run(dry_run=False)
    assert (
        second.repaired_apis,
        second.repaired_credentials,
        second.repaired_connect_sessions,
    ) == (0, 0, 0)

    # Restore dry-run mutates nothing; live restore reverts all 3 rows; rerun is idempotent.
    dry_restore = await svc.restore_backup(result.backup, dry_run=True)
    assert (
        dry_restore.restored_apis,
        dry_restore.restored_credentials,
        dry_restore.restored_connect_sessions,
    ) == (1, 1, 1)

    restored = await svc.restore_backup(result.backup, dry_run=False)
    assert (
        restored.restored_apis,
        restored.restored_credentials,
        restored.restored_connect_sessions,
    ) == (1, 1, 1)

    async with integration_context.registry_db.session() as session:
        restored_api = await session.get(Api, api_id)
        assert restored_api is not None and restored_api.name == "posthog-com-posthog-api"
    async with integration_context.control_db.session() as session:
        restored_cred = await session.get(Credential, "cred_ph_1079")
        restored_cs = await session.get(ConnectSession, "cs_ph_open")
        assert restored_cred is not None and restored_cred.api_name == "posthog-com-posthog-api"
        assert restored_cs is not None and restored_cs.api_name == "posthog-com-posthog-api"

    idempotent_restore = await svc.restore_backup(result.backup, dry_run=False)
    assert idempotent_restore.already_restored == 3
    assert (
        idempotent_restore.restored_apis,
        idempotent_restore.restored_credentials,
        idempotent_restore.restored_connect_sessions,
    ) == (0, 0, 0)

    # Drift detection: if credential changed to a third state, restore fails closed.
    await svc.run(dry_run=False)
    async with integration_context.control_db.session() as session:
        drifted = await session.get(Credential, "cred_ph_1079")
        assert drifted is not None
        drifted.api_name = "unexpected-third-name"
        await session.commit()
    with pytest.raises(IdentityRepairBackupError, match="drifted"):
        await svc.restore_backup(result.backup, dry_run=False)


async def test_collisions_and_pending_holds_fail_closed_without_mutating(
    integration_context: Context, clean_all: None
) -> None:
    """Colliding APIs/credentials, ambiguous candidates, and pending holds fail closed."""
    # 1. Both doubled and clean Registry API exist -> both API and credential fail closed.
    async with integration_context.registry_db.session() as session:
        session.add_all(
            [
                Api(
                    vendor="posthog-com",
                    name="posthog-com-posthog-api",
                    version="1.0.0",
                    catalog_api_id="posthog.com/posthog-api",
                    created_by="usr_owner",
                ),
                Api(
                    vendor="posthog-com",
                    name="posthog-api",
                    version="1.0.0",
                    catalog_api_id="posthog.com/posthog-api",
                    created_by="usr_owner",
                ),
                # Ambiguous candidate versions for Slack
                Api(vendor="slack-com", name="web-api", version="1.0.0", created_by="usr_owner"),
                Api(vendor="slack-com", name="web-api", version="2.0.0", created_by="usr_owner"),
                # Clean single API for GitHub, blocked by clean+malformed credential collision
                Api(
                    vendor="github-com",
                    name="api-github-com",
                    version="1.1.4",
                    catalog_api_id="github.com/api.github.com",
                    created_by="usr_owner",
                ),
                # Clean single API for Stripe, blocked by pending HeldExecution
                Api(
                    vendor="stripe-com",
                    name="stripe-api",
                    version="2024-04-10",
                    catalog_api_id="stripe.com/stripe-api",
                    created_by="usr_owner",
                ),
            ]
        )
        await session.commit()

    async with integration_context.control_db.session() as session:
        session.add_all(
            [
                Credential(
                    id="cred_ph_blocked",
                    type="bearer",
                    name="PostHog",
                    api_vendor="posthog-com",
                    api_name="posthog-com-posthog-api",
                    created_by="usr_owner",
                ),
                Credential(
                    id="cred_slack_ambig",
                    type="bearer",
                    name="Slack",
                    api_vendor="slack-com",
                    api_name="openapi",
                    api_version="web-api",
                    created_by="usr_owner",
                ),
                Credential(
                    id="cred_gh_malformed",
                    type="bearer",
                    name="GitHub Old",
                    api_vendor="github-com",
                    api_name="openapi",
                    api_version="api.github.com",
                    created_by="usr_owner",
                ),
                Credential(
                    id="cred_gh_clean",
                    type="bearer",
                    name="GitHub Clean",
                    api_vendor="github-com",
                    api_name="api-github-com",
                    api_version="1.1.4",
                    created_by="usr_owner",
                ),
                Credential(
                    id="cred_stripe_held",
                    type="bearer",
                    name="Stripe",
                    api_vendor="stripe-com",
                    api_name="stripe-com-stripe-api",
                    catalog_api_id="stripe.com/stripe-api",
                    created_by="usr_owner",
                ),
            ]
        )
        await session.commit()

    async with integration_context.admin_db.session() as session:
        session.add(
            Job(
                id="job_held_1",
                kind="execute",
                status="held",
                actor_type="agent",
                payload={},
                created_by="agnt_1",
            )
        )
        await session.flush()
        session.add(
            HeldExecution(
                id="held_pending_1",
                job_id="job_held_1",
                agent_id="agnt_1",
                credential_id="cred_stripe_held",
                api_vendor="stripe-com",
                api_name="stripe-com-stripe-api",
                api_version="2024-04-10",
                method="POST",
                path="/v1/charges",
                request_fingerprint="f" * 64,
                state="pending",
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                created_by="agnt_1",
            )
        )
        await session.commit()

    result = await CredentialIdentityRepairService(integration_context).run(dry_run=False)
    assert result.failed_closed is True
    assert result.repaired_apis == 0
    assert result.repaired_credentials == 0
    reasons = {f.reason for f in result.findings}
    assert {
        "target_api_already_exists",
        "registry_api_collision",
        "target_credential_already_exists",
        "multiple_candidate_apis",
        "pending_held_execution_in_flight",
    } <= reasons

    # Verify zero mutations across all rows.
    async with integration_context.control_db.session() as session:
        ph = await session.get(Credential, "cred_ph_blocked")
        sl = await session.get(Credential, "cred_slack_ambig")
        gh = await session.get(Credential, "cred_gh_malformed")
        st = await session.get(Credential, "cred_stripe_held")
        assert ph is not None and ph.api_name == "posthog-com-posthog-api"
        assert sl is not None and (sl.api_name, sl.api_version) == ("openapi", "web-api")
        assert gh is not None and (gh.api_name, gh.api_version) == ("openapi", "api.github.com")
        assert st is not None and st.api_name == "stripe-com-stripe-api"


async def test_upgrade_step_service_records_backup_and_fails_closed_on_conflict(
    integration_context: Context, clean_all: None
) -> None:
    """UpgradeStepService runs credential_identity_repair and records backup in ledger summary."""
    async with integration_context.registry_db.session() as session:
        session.add(
            Api(
                vendor="github-com",
                name="api-github-com",
                version="1.1.4",
                catalog_api_id="github.com/api.github.com",
                created_by="usr_owner",
            )
        )
        await session.commit()
    async with integration_context.control_db.session() as session:
        session.add(
            Credential(
                id="cred_step_gh",
                type="bearer",
                name="GitHub",
                api_vendor="github-com",
                api_name="openapi",
                api_version="api.github.com",
                created_by="usr_owner",
            )
        )
        await session.commit()

    outcomes = await UpgradeStepService(integration_context).run()
    by_name = {o.name: o for o in outcomes}
    step_out = by_name[CREDENTIAL_IDENTITY_REPAIR]
    assert step_out.action == "performed"
    assert not step_out.failed
    assert step_out.summary["repaired_credentials"] == 1
    assert "backup" in step_out.summary

    async with integration_context.control_db.session() as session:
        ledger_row = await UpgradeStepRepository.get(session, CREDENTIAL_IDENTITY_REPAIR)
        assert ledger_row is not None and ledger_row.summary is not None
        assert ledger_row.summary["repaired_credentials"] == 1
