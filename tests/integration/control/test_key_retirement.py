"""Integration tests for the theme-5 Phase 4 toolkit-key retirement job.

Runs ``KeyRetirementService`` against real control + admin databases: seeds a
toolkit, its ``jntc_live_`` key, credential bindings, and pair rules on the
control side plus the owner user on the admin side, then asserts the job
creates the successor service account (account + credential digest + execute
grant + toolkit binding + per-credential bindings with copied rule sets) and
stamps ``migrated_actor_id``. Also covers the skip reasons, fallback-owner
resolution, and idempotency.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import delete, select, text

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.repos.toolkit_key_gen import generate_toolkit_key
from jentic_one.control.services.key_retirement import KeyRetirementService
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_OWNER = "usr_krtest_owner"
_FALLBACK_OWNER = "usr_krtest_fallback"
_FALLBACK_EMAIL = "krtest-fallback@test.local"


@pytest.fixture()
async def clean_tables(
    control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Remove every row this module seeds or the job creates, before and after."""

    async def _cleanup() -> None:
        async with control_db.session() as session:
            # The job scans every toolkit key, so stray rows from other
            # modules would leak into the outcome list — wipe them all.
            await session.execute(delete(ToolkitKey))
            await session.execute(
                text("DELETE FROM permission_rule_sets WHERE name LIKE 'theme5-key-retirement:%'")
            )
            await session.execute(text("DELETE FROM toolkits WHERE id LIKE 'tk_krtest%'"))
            await session.execute(text("DELETE FROM credentials WHERE id LIKE 'cred_krtest%'"))
            await session.commit()
        async with admin_db.session() as session:
            for table in (
                "agent_credential_bindings",
                "agent_toolkit_bindings",
                "actor_scope_grants",
            ):
                column = "agent_id" if table.startswith("agent_") else "actor_id"
                await session.execute(
                    text(
                        f"DELETE FROM {table} WHERE {column} IN "
                        "(SELECT id FROM service_accounts WHERE name LIKE 'toolkit-key:ck_krtest%')"
                    )
                )
            await session.execute(
                text(
                    "DELETE FROM service_account_credentials WHERE service_account_id IN "
                    "(SELECT id FROM service_accounts WHERE name LIKE 'toolkit-key:ck_krtest%')"
                )
            )
            await session.execute(
                text("DELETE FROM service_accounts WHERE name LIKE 'toolkit-key:ck_krtest%'")
            )
            await session.execute(
                text("DELETE FROM users WHERE id IN (:owner, :fallback)"),
                {"owner": _OWNER, "fallback": _FALLBACK_OWNER},
            )
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture()
async def seed_owner(admin_db: DatabaseSession, clean_tables: None) -> None:
    """Seed the admin-side users the job resolves owners against."""
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) VALUES "
                "(:owner, 'krtest-owner@test.local', 'Kay', 'Owner'), "
                "(:fallback, :fallback_email, 'Fay', 'Fallback') "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "owner": _OWNER,
                "fallback": _FALLBACK_OWNER,
                "fallback_email": _FALLBACK_EMAIL,
            },
        )
        await session.commit()


async def _seed_toolkit_with_key(
    control_db: DatabaseSession,
    *,
    suffix: str,
    active: bool = True,
    revoked: bool = False,
    with_lookup_hash: bool = True,
    key_created_by: str | None = _OWNER,
    toolkit_created_by: str | None = _OWNER,
) -> tuple[str, str, str]:
    """Seed a toolkit + key; return (toolkit_id, key_id, lookup_hash)."""
    _plaintext, hashed, preview, lookup = generate_toolkit_key()
    toolkit_id = f"tk_krtest{suffix}"
    key_id = f"ck_krtest{suffix}"
    async with control_db.session() as session:
        session.add(
            Toolkit(
                id=toolkit_id,
                name=f"kr-toolkit-{suffix}",
                active=active,
                created_by=toolkit_created_by,
            )
        )
        await session.flush()
        session.add(
            ToolkitKey(
                id=key_id,
                toolkit_id=toolkit_id,
                hashed_key=hashed,
                key_preview=preview,
                lookup_hash=lookup if with_lookup_hash else None,
                revoked=revoked,
                label=f"kr-key-{suffix}",
                created_by=key_created_by,
            )
        )
        await session.commit()
    return toolkit_id, key_id, lookup


async def _bind_credential(
    control_db: DatabaseSession,
    *,
    toolkit_id: str,
    suffix: str,
    rules: list[tuple[str, str]] | None = None,
) -> str:
    """Bind a fresh credential to the toolkit with the given (effect, path) rules."""
    credential_id = f"cred_krtest{suffix}"
    async with control_db.session() as session:
        session.add(
            Credential(
                id=credential_id,
                type="token_value",
                name=f"kr-cred-{suffix}",
                api_vendor="krtest.local",
                created_by=_OWNER,
            )
        )
        await session.flush()
        session.add(
            ToolkitCredentialBinding(
                toolkit_id=toolkit_id, credential_id=credential_id, created_by=_OWNER
            )
        )
        for sequence, (effect, path) in enumerate(rules or []):
            session.add(
                ToolkitPermissionRule(
                    toolkit_id=toolkit_id,
                    credential_id=credential_id,
                    effect=effect,
                    path=path,
                    match_mode="regex",
                    sequence=sequence,
                    created_by=_OWNER,
                )
            )
        await session.commit()
    return credential_id


async def _admin_rows(
    admin_db: DatabaseSession, query: str, params: dict[str, object]
) -> list[Any]:
    async with admin_db.session() as session:
        return list((await session.execute(text(query), params)).all())


async def _migrated_actor_id(control_db: DatabaseSession, key_id: str) -> str | None:
    async with control_db.session() as session:
        key = await session.get(ToolkitKey, key_id)
        assert key is not None
        return key.migrated_actor_id


async def test_happy_path_creates_all_successor_artifacts(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    seed_owner: None,
) -> None:
    """One resolvable key → account, digest, grant, both binding kinds, rule copy, stamp."""
    toolkit_id, key_id, lookup = await _seed_toolkit_with_key(control_db, suffix="hp")
    ruled_cred = await _bind_credential(
        control_db,
        toolkit_id=toolkit_id,
        suffix="hpruled",
        rules=[("allow", "/repos/.*"), ("deny", "/admin/.*")],
    )
    rule_less_cred = await _bind_credential(control_db, toolkit_id=toolkit_id, suffix="hpbare")

    outcomes = await KeyRetirementService(integration_context).run()

    by_key = {o.key_id: o for o in outcomes}
    outcome = by_key[key_id]
    assert outcome.action == "migrated"
    assert outcome.reason is None
    sva_id = outcome.service_account_id
    assert sva_id is not None and sva_id.startswith("sva_")
    assert set(outcome.bound_credential_ids) == {ruled_cred, rule_less_cred}
    assert outcome.rule_less_credential_ids == (rule_less_cred,)

    # 1) The service account, named for the key, active, owned by the key's creator.
    accounts = await _admin_rows(
        admin_db,
        "SELECT id, name, status, owner_id FROM service_accounts WHERE name = :name",
        {"name": f"toolkit-key:{key_id}"},
    )
    assert len(accounts) == 1
    assert accounts[0].id == sva_id
    assert accounts[0].status == "active"
    assert accounts[0].owner_id == _OWNER

    # 2) The credential row carries the key's SHA-256 lookup digest.
    creds = await _admin_rows(
        admin_db,
        "SELECT api_key_hash FROM service_account_credentials WHERE service_account_id = :sva",
        {"sva": sva_id},
    )
    assert [row.api_key_hash for row in creds] == [lookup]

    # 3) Exactly the execute grant — never the default agent scope set.
    grants = await _admin_rows(
        admin_db,
        "SELECT scope, actor_type FROM actor_scope_grants WHERE actor_id = :sva",
        {"sva": sva_id},
    )
    assert [(row.scope, row.actor_type) for row in grants] == [
        ("capabilities:execute", "service_account")
    ]

    # 4) The flag-off path: one toolkit binding for the successor actor.
    toolkit_bindings = await _admin_rows(
        admin_db,
        "SELECT toolkit_id FROM agent_toolkit_bindings WHERE agent_id = :sva",
        {"sva": sva_id},
    )
    assert [row.toolkit_id for row in toolkit_bindings] == [toolkit_id]

    # 5) The flag-on path: one credential binding per bound pair, rule-less → NULL set.
    cred_bindings = await _admin_rows(
        admin_db,
        "SELECT credential_id, rule_set_id FROM agent_credential_bindings "
        "WHERE agent_id = :sva ORDER BY credential_id",
        {"sva": sva_id},
    )
    bindings_by_cred = {row.credential_id: row.rule_set_id for row in cred_bindings}
    assert set(bindings_by_cred) == {ruled_cred, rule_less_cred}
    assert bindings_by_cred[rule_less_cred] is None

    # 6) The ruled pair's set is a control-DB copy of its rules, in sequence order.
    rule_set_id = bindings_by_cred[ruled_cred]
    async with control_db.session() as session:
        rule_set = await session.get(PermissionRuleSet, rule_set_id)
        assert rule_set is not None
        assert rule_set.name == f"theme5-key-retirement:{toolkit_id}:{ruled_cred}"
        copied = (
            (
                await session.execute(
                    select(PermissionRuleSetRule)
                    .where(PermissionRuleSetRule.rule_set_id == rule_set_id)
                    .order_by(PermissionRuleSetRule.sequence)
                )
            )
            .scalars()
            .all()
        )
    assert [(rule.effect, rule.path) for rule in copied] == [
        ("allow", "/repos/.*"),
        ("deny", "/admin/.*"),
    ]

    # 7) The key is stamped with its successor.
    assert await _migrated_actor_id(control_db, key_id) == sva_id


async def test_unresolvable_keys_are_skipped_with_reasons(
    integration_context: Context,
    control_db: DatabaseSession,
    seed_owner: None,
) -> None:
    """A key that cannot authenticate today is never migrated — one reason each."""
    _tk1, revoked_key, _ = await _seed_toolkit_with_key(control_db, suffix="rev", revoked=True)
    _tk2, inactive_key, _ = await _seed_toolkit_with_key(control_db, suffix="ina", active=False)
    _tk3, hashless_key, _ = await _seed_toolkit_with_key(
        control_db, suffix="nolh", with_lookup_hash=False
    )
    _tk4, orphan_key, _ = await _seed_toolkit_with_key(
        control_db, suffix="orph", key_created_by="usr_krtest_ghost", toolkit_created_by=None
    )

    outcomes = await KeyRetirementService(integration_context).run()

    by_key = {o.key_id: (o.action, o.reason) for o in outcomes}
    assert by_key[revoked_key] == ("skipped", "revoked")
    assert by_key[inactive_key] == ("skipped", "toolkit_inactive")
    assert by_key[hashless_key] == ("skipped", "no_lookup_hash")
    assert by_key[orphan_key] == ("skipped", "owner_unresolved")
    for key_id in (revoked_key, inactive_key, hashless_key, orphan_key):
        assert await _migrated_actor_id(control_db, key_id) is None


async def test_fallback_owner_email_resolves_ownerless_key(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    seed_owner: None,
) -> None:
    """An operator-supplied email owns keys whose creators are not users."""
    _toolkit_id, key_id, _ = await _seed_toolkit_with_key(
        control_db, suffix="fb", key_created_by="usr_krtest_ghost", toolkit_created_by=None
    )

    outcomes = await KeyRetirementService(integration_context).run(
        fallback_owner_email=_FALLBACK_EMAIL
    )

    outcome = {o.key_id: o for o in outcomes}[key_id]
    assert outcome.action == "migrated"
    accounts = await _admin_rows(
        admin_db,
        "SELECT owner_id FROM service_accounts WHERE name = :name",
        {"name": f"toolkit-key:{key_id}"},
    )
    assert [row.owner_id for row in accounts] == [_FALLBACK_OWNER]


async def test_unknown_fallback_owner_email_raises(
    integration_context: Context,
    seed_owner: None,
) -> None:
    """A fallback email matching no user fails the whole run up front."""
    with pytest.raises(ValueError, match="does not match any user"):
        await KeyRetirementService(integration_context).run(
            fallback_owner_email="krtest-nobody@test.local"
        )


async def test_rerun_is_idempotent(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    seed_owner: None,
) -> None:
    """A second run short-circuits on the stamp and duplicates no admin rows."""
    toolkit_id, key_id, _ = await _seed_toolkit_with_key(control_db, suffix="idem")
    await _bind_credential(
        control_db, toolkit_id=toolkit_id, suffix="idem", rules=[("allow", "/v1/.*")]
    )
    service = KeyRetirementService(integration_context)

    first = {o.key_id: o for o in await service.run()}[key_id]
    second = {o.key_id: o for o in await service.run()}[key_id]

    assert first.action == "migrated"
    assert second.action == "already_migrated"
    assert second.service_account_id == first.service_account_id

    sva_id = first.service_account_id
    for query in (
        "SELECT id FROM service_accounts WHERE name = :name",
        "SELECT id FROM service_account_credentials WHERE service_account_id = :sva",
        "SELECT id FROM actor_scope_grants WHERE actor_id = :sva",
        "SELECT id FROM agent_toolkit_bindings WHERE agent_id = :sva",
        "SELECT id FROM agent_credential_bindings WHERE agent_id = :sva",
    ):
        rows = await _admin_rows(admin_db, query, {"name": f"toolkit-key:{key_id}", "sva": sva_id})
        assert len(rows) == 1, query
    async with control_db.session() as session:
        rule_sets = (
            (
                await session.execute(
                    select(PermissionRuleSet).where(
                        PermissionRuleSet.name.like(f"theme5-key-retirement:{toolkit_id}:%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rule_sets) == 1
