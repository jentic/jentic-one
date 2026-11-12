"""Cross-DB fault-injection tests for credential-binding purge fail-closed behavior.

Exercises ``AgentService.unbind_credential(..., purge=True)`` against real
file-backed SQLite ``admin_db`` and ``control_db`` ``DatabaseSession`` instances
(no DB or repository mocking). A SQLite ``BEFORE DELETE`` trigger on
``agent_credential_bindings`` aborts the final admin-DB delete transaction after
the initial suspension transaction and control-DB inline-rule delete transaction
have already committed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Table, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateTable

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentCredentialBindingRepository
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.broker.core.errors import CredentialBindingSuspendedError
from jentic_one.broker.core.setup import BrokerCacheInvalidator
from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.repos import (
    AgentRuleEvaluator,
    ApiKeyResolver,
    CredentialBindingResolver,
    InProcessTokenResolver,
)
from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
from jentic_one.broker.services.execution.authorization import derive_credential_bindings
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import RuleVerdict
from jentic_one.shared.config import AppConfig, DatabaseConfig, DatabasesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.models.actors import ActorStatus
from jentic_one.shared.schemas import APIReference

_OWNER_ID = "usr_owner_purge_test"
_AGENT_ID = "agnt_purge_fail_closed"
_CREDENTIAL_ID = "cred_purge_fail_closed"
_RULE_SET_ID = "prs_shared_allow_set"
_API = APIReference(vendor="stripe.com", name="main", version="2025-01-01")


def _create_sqlite_tables(sync_conn: Connection, *tables: Table) -> None:
    """Create ORM tables on SQLite, temporarily stripping Postgres-only server defaults."""
    for table in tables:
        saved = {col: col.server_default for col in table.columns}
        for col in table.columns:
            col.server_default = None
        try:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
        finally:
            for col, default in saved.items():
                col.server_default = default


def _owner_identity() -> Identity:
    return Identity(
        sub=_OWNER_ID,
        email="owner@example.com",
        permissions=["agents:write", "agents:read", "credentials:write", "credentials:read"],
    )


def _agent_identity() -> Identity:
    return Identity(
        sub=_AGENT_ID,
        permissions=["capabilities:execute"],
    )


@pytest.fixture()
async def cross_db_ctx(tmp_path: Path) -> AsyncGenerator[Context, None]:
    """Connected Context backed by real SQLite admin_db and control_db files."""
    cfg = AppConfig(
        databases=DatabasesConfig(
            admin=DatabaseConfig(backend="sqlite", path=str(tmp_path / "admin.db")),
            control=DatabaseConfig(backend="sqlite", path=str(tmp_path / "control.db")),
            registry=DatabaseConfig(backend="sqlite", path=str(tmp_path / "registry.db")),
        )
    )
    ctx = Context(cfg, allowed_dbs={"admin", "control"}, refresh_providers_on_boot=False)
    await ctx.startup()

    admin_tables = (
        cast(Table, User.__table__),
        cast(Table, Agent.__table__),
        cast(Table, AgentCredential.__table__),
        cast(Table, AgentCredentialBinding.__table__),
        cast(Table, AuditEntry.__table__),
        cast(Table, Event.__table__),
    )
    control_tables = (
        cast(Table, OAuthAppRegistration.__table__),
        cast(Table, Credential.__table__),
        cast(Table, AgentPermissionRule.__table__),
        cast(Table, PermissionRuleSet.__table__),
        cast(Table, PermissionRuleSetRule.__table__),
    )
    async with ctx.admin_db.engine.begin() as conn:
        await conn.run_sync(_create_sqlite_tables, *admin_tables)
    async with ctx.control_db.engine.begin() as conn:
        await conn.run_sync(_create_sqlite_tables, *control_tables)

    async with ctx.admin_db.transaction() as session:
        session.add(
            User(
                id=_OWNER_ID,
                email="owner@example.com",
                first_name="Test",
                last_name="Owner",
                active=True,
                auth_provider="local",
                invite_state="accepted",
                created_by=_OWNER_ID,
            )
        )
        await session.flush()
        session.add(
            Agent(
                id=_AGENT_ID,
                name="purge-test-agent",
                owner_id=_OWNER_ID,
                registered_by=_OWNER_ID,
                status=ActorStatus.ACTIVE,
                created_by=_OWNER_ID,
            )
        )
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=_CREDENTIAL_ID,
                type="bearer",
                name="stripe-prod",
                api_vendor=_API.vendor,
                api_name=_API.name,
                api_version=_API.version,
                provider="token_value",
                state="connected",
                active=True,
                created_by=_OWNER_ID,
            )
        )

    try:
        yield ctx
    finally:
        await ctx.shutdown()
        await ctx.close_state_backend()


def _wire_broker_caches(
    ctx: Context,
) -> tuple[CachingCredentialDeriver, AgentRuleEvaluator]:
    """Attach real broker derivation and rule caches to ``ctx`` for invalidation."""
    raw_deriver = CredentialBindingResolver(ctx.admin_db, ctx.control_db)
    caching_deriver = CachingCredentialDeriver(raw_deriver, cache_ttl_seconds=300.0)
    rule_evaluator = AgentRuleEvaluator(ctx.control_db, cache_ttl_seconds=300.0)
    invalidator = BrokerCacheInvalidator(
        opaque=CachedTokenValidator(resolver=InProcessTokenResolver(ctx.admin_db)),
        api_key=CachedTokenValidator(resolver=ApiKeyResolver(ctx.admin_db)),
        actor_status=None,
        rule_evaluator=rule_evaluator,
        credential_deriver=caching_deriver,
    )
    ctx.register_broker_cache_invalidator(invalidator)
    return caching_deriver, rule_evaluator


async def _install_binding_delete_failure_trigger(ctx: Context) -> None:
    """Install a SQLite trigger that aborts DELETE on agent_credential_bindings."""
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "CREATE TRIGGER fail_binding_delete "
                "BEFORE DELETE ON agent_credential_bindings "
                "BEGIN "
                "    SELECT RAISE(ABORT, 'simulated admin binding delete failure'); "
                "END"
            )
        )


async def _remove_binding_delete_failure_trigger(ctx: Context) -> None:
    """Drop the fault-injection trigger so a retry can complete the purge."""
    async with ctx.admin_db.transaction() as session:
        await session.execute(text("DROP TRIGGER IF EXISTS fail_binding_delete"))


@pytest.mark.asyncio
async def test_purge_partial_failure_fails_closed_for_attached_rule_set_and_retries_cleanly(
    cross_db_ctx: Context,
) -> None:
    """When admin-DB binding delete fails after control-DB cleanup, a binding with an
    attached shared rule set is left suspended and uncached (denying access immediately
    for both warm and cold broker resolvers), and a subsequent retry finishes the purge.
    """
    caching_deriver, warm_evaluator = _wire_broker_caches(cross_db_ctx)
    svc = AgentService(cross_db_ctx)

    async with cross_db_ctx.control_db.transaction() as session:
        session.add(
            PermissionRuleSet(
                id=_RULE_SET_ID,
                name="shared-allow-charges",
                curated=False,
                created_by=_OWNER_ID,
            )
        )
        await session.flush()
        session.add(
            PermissionRuleSetRule(
                id="prr_allow_charges",
                rule_set_id=_RULE_SET_ID,
                effect="allow",
                methods=["GET"],
                path="/v1/charges",
                match_mode="exact",
                is_system=False,
                sequence=0,
                created_by=_OWNER_ID,
            )
        )
        session.add(
            AgentPermissionRule(
                id="apr_dormant_inline",
                agent_id=_AGENT_ID,
                credential_id=_CREDENTIAL_ID,
                effect="allow",
                methods=["GET"],
                path="/v1/charges",
                match_mode="exact",
                is_system=False,
                sequence=0,
                created_by=_OWNER_ID,
            )
        )

    async with cross_db_ctx.admin_db.transaction() as session:
        session.add(
            AgentCredentialBinding(
                id="acb_shared_ruleset",
                agent_id=_AGENT_ID,
                credential_id=_CREDENTIAL_ID,
                rule_set_id=_RULE_SET_ID,
                bound_at=datetime.now(UTC),
                suspended=False,
                created_by=_OWNER_ID,
            )
        )

    # Warm the broker caches before purging.
    warm_before = await derive_credential_bindings(
        deriver=caching_deriver,
        identity=_agent_identity(),
        api=_API,
        instance="/v1/charges",
        ctx=cross_db_ctx,
        method="GET",
        path="/v1/charges",
    )
    assert len(warm_before.credentials) == 1
    assert warm_before.credentials[0].credential_id == _CREDENTIAL_ID
    assert warm_before.credentials[0].rule_set_id == _RULE_SET_ID

    eval_before = await warm_evaluator.evaluate(
        agent_id=_AGENT_ID,
        credential_id=_CREDENTIAL_ID,
        rule_set_id=_RULE_SET_ID,
        method="GET",
        path="/v1/charges",
        operation_id="getCharges",
    )
    assert eval_before.verdict == RuleVerdict.ALLOW

    # Inject a failure on the final admin_db DELETE statement.
    await _install_binding_delete_failure_trigger(cross_db_ctx)
    with pytest.raises(DatabaseIntegrityError):
        await svc.unbind_credential(
            _AGENT_ID,
            credential_id=_CREDENTIAL_ID,
            purge=True,
            identity=_owner_identity(),
        )

    # 1. Surviving binding row has suspended=True committed in admin_db.
    async with cross_db_ctx.admin_db.session() as session:
        surviving = await AgentCredentialBindingRepository.get(
            session, agent_id=_AGENT_ID, credential_id=_CREDENTIAL_ID
        )
    assert surviving is not None
    assert surviving.rule_set_id == _RULE_SET_ID
    assert surviving.suspended is True

    # 2. Dormant inline rules were deleted in control_db.
    async with cross_db_ctx.control_db.session() as session:
        remaining_inline = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM agent_permission_rules "
                    "WHERE agent_id = :agent_id AND credential_id = :credential_id"
                ),
                {"agent_id": _AGENT_ID, "credential_id": _CREDENTIAL_ID},
            )
        ).scalar_one()
    assert remaining_inline == 0

    # 3. Both warm (cache-invalidated in finally:) and cold broker derivation deny immediately.
    cold_deriver = CredentialBindingResolver(cross_db_ctx.admin_db, cross_db_ctx.control_db)
    for deriver in (caching_deriver, cold_deriver):
        derivation = await deriver.derive_credentials(
            agent_id=_AGENT_ID,
            vendor=_API.vendor,
            name=_API.name,
            version=_API.version,
        )
        assert derivation.credentials == ()
        assert derivation.binding_suspended is True
        with pytest.raises(CredentialBindingSuspendedError):
            await derive_credential_bindings(
                deriver=deriver,
                identity=_agent_identity(),
                api=_API,
                instance="/v1/charges",
                ctx=cross_db_ctx,
                method="GET",
                path="/v1/charges",
            )

    # 4. Removing the fault and retrying purge completes cleanly and removes the row.
    await _remove_binding_delete_failure_trigger(cross_db_ctx)
    await svc.unbind_credential(
        _AGENT_ID,
        credential_id=_CREDENTIAL_ID,
        purge=True,
        identity=_owner_identity(),
    )

    async with cross_db_ctx.admin_db.session() as session:
        after_retry = await AgentCredentialBindingRepository.get(
            session, agent_id=_AGENT_ID, credential_id=_CREDENTIAL_ID
        )
    assert after_retry is None

    post_purge_derivation = await caching_deriver.derive_credentials(
        agent_id=_AGENT_ID,
        vendor=_API.vendor,
        name=_API.name,
        version=_API.version,
    )
    assert post_purge_derivation.credentials == ()
    assert post_purge_derivation.binding_suspended is False


@pytest.mark.asyncio
async def test_purge_partial_failure_fails_closed_for_inline_rules_and_invalidates_warm_caches(
    cross_db_ctx: Context,
) -> None:
    """When admin-DB binding delete fails after control-DB inline-rule delete, the binding
    is committed as suspended and warm inline-rule + derivation caches are evicted in finally:.
    """
    caching_deriver, warm_evaluator = _wire_broker_caches(cross_db_ctx)
    svc = AgentService(cross_db_ctx)

    async with cross_db_ctx.control_db.transaction() as session:
        session.add(
            AgentPermissionRule(
                id="apr_inline_allow",
                agent_id=_AGENT_ID,
                credential_id=_CREDENTIAL_ID,
                effect="allow",
                methods=["POST"],
                path="/v1/refunds",
                match_mode="exact",
                is_system=False,
                sequence=0,
                created_by=_OWNER_ID,
            )
        )

    async with cross_db_ctx.admin_db.transaction() as session:
        session.add(
            AgentCredentialBinding(
                id="acb_inline_only",
                agent_id=_AGENT_ID,
                credential_id=_CREDENTIAL_ID,
                rule_set_id=None,
                bound_at=datetime.now(UTC),
                suspended=False,
                created_by=_OWNER_ID,
            )
        )

    # Warm both caches prior to the failing purge.
    pre_derivation = await caching_deriver.derive_credentials(
        agent_id=_AGENT_ID,
        vendor=_API.vendor,
        name=_API.name,
        version=_API.version,
    )
    assert len(pre_derivation.credentials) == 1
    pre_eval = await warm_evaluator.evaluate(
        agent_id=_AGENT_ID,
        credential_id=_CREDENTIAL_ID,
        rule_set_id=None,
        method="POST",
        path="/v1/refunds",
        operation_id="createRefund",
    )
    assert pre_eval.verdict == RuleVerdict.ALLOW

    await _install_binding_delete_failure_trigger(cross_db_ctx)
    with pytest.raises(DatabaseIntegrityError):
        await svc.unbind_credential(
            _AGENT_ID,
            credential_id=_CREDENTIAL_ID,
            purge=True,
            identity=_owner_identity(),
        )

    # Warm and cold derivation both deny with binding_suspended=True.
    cold_deriver = CredentialBindingResolver(cross_db_ctx.admin_db, cross_db_ctx.control_db)
    for deriver in (caching_deriver, cold_deriver):
        derivation = await deriver.derive_credentials(
            agent_id=_AGENT_ID,
            vendor=_API.vendor,
            name=_API.name,
            version=_API.version,
        )
        assert derivation.credentials == ()
        assert derivation.binding_suspended is True

    # Warm and cold rule evaluators both deny with rules_loaded == 0.
    cold_evaluator = AgentRuleEvaluator(cross_db_ctx.control_db, cache_ttl_seconds=300.0)
    for evaluator in (warm_evaluator, cold_evaluator):
        verdict = await evaluator.evaluate(
            agent_id=_AGENT_ID,
            credential_id=_CREDENTIAL_ID,
            rule_set_id=None,
            method="POST",
            path="/v1/refunds",
            operation_id="createRefund",
        )
        assert verdict.verdict == RuleVerdict.DENY
        assert verdict.rules_loaded == 0

    # Retry after fault clears completes the purge cleanly.
    await _remove_binding_delete_failure_trigger(cross_db_ctx)
    await svc.unbind_credential(
        _AGENT_ID,
        credential_id=_CREDENTIAL_ID,
        purge=True,
        identity=_owner_identity(),
    )

    async with cross_db_ctx.admin_db.session() as session:
        assert (
            await AgentCredentialBindingRepository.get(
                session, agent_id=_AGENT_ID, credential_id=_CREDENTIAL_ID
            )
            is None
        )
