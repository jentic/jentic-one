"""Integration tests for the theme-5 Phase 6a toolkit-flattening job.

Runs ``ToolkitFlatteningService`` against real control + admin databases,
seeded with the plan's R-01 awkward shapes: the same ``(agent, credential)``
pair reachable via two toolkits with divergent rules, same-vendor pooled
rules whose per-pair replay differs, an inactive toolkit with bound agents,
dangling ids on both sides of the cross-DB join, a live ``jntc_live_`` key
row, a converted identity whose scopes exceed ``capabilities:execute``, and
two same-named same-API credentials. The same suite runs on SQLite (via
``JENTIC_TEST_BACKEND=sqlite``) and Postgres — the dual-dialect invariance
check.
"""

from __future__ import annotations

import datetime as dt
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
from jentic_one.control.core.schema.toolkit_flattening_acks import ToolkitFlatteningAck
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.services.toolkit_flattening import Finding, ToolkitFlatteningService
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_OWNER = "usr_fltest_owner"
_AGENT_A = "agnt_fltest_a"
_AGENT_B = "agnt_fltest_b"
_GHOST_AGENT = "agnt_fltest_ghost"
_MIGRATED_SVA = "sva_fltest_migr"

_TK_A = "tk_fltest_a"
_TK_B = "tk_fltest_b"
_TK_INACTIVE = "tk_fltest_inact"
_TK_GHOST = "tk_fltest_ghost"

_CRED_ONE = "cred_fltest_one"
_CRED_TWO = "cred_fltest_two"
_CRED_DUP1 = "cred_fltest_dup1"
_CRED_DUP2 = "cred_fltest_dup2"

_ATB_BOUND_AT = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
_TCB_BOUND_AT = dt.datetime(2026, 2, 1, 12, 0, tzinfo=dt.UTC)

#: Every (agent, credential) pair the seed graph derives.
_EXPECTED_PAIRS = {
    (_AGENT_A, _CRED_ONE),  # via tk_a AND tk_b, divergent rules → conflict
    (_AGENT_A, _CRED_TWO),  # via tk_a only, rule-less pair with pooled drift
    (_AGENT_A, _CRED_DUP1),  # same-named same-API twins (multi-account)
    (_AGENT_A, _CRED_DUP2),
    (_AGENT_B, _CRED_TWO),  # via the INACTIVE toolkit — migrated + loud
}


@pytest.fixture()
async def clean_tables(
    control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Remove every row this module seeds or the job creates, before and after.

    The job scans the whole toolkit graph and the whole binding table, so the
    legacy control tables and ``agent_toolkit_bindings`` are wiped outright
    (the key-retirement suite sets the precedent for whole-table wipes on
    job-scanned tables); everything else is cleaned by this module's
    prefixes.
    """

    async def _cleanup() -> None:
        async with control_db.session() as session:
            await session.execute(delete(ToolkitPermissionRule))
            await session.execute(delete(ToolkitKey))
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Toolkit))
            await session.execute(delete(ToolkitFlatteningAck))
            await session.execute(
                text("DELETE FROM permission_rule_sets WHERE name LIKE 'theme5-flattening:%'")
            )
            await session.execute(text("DELETE FROM credentials WHERE id LIKE 'cred_fltest%'"))
            await session.commit()
        async with admin_db.session() as session:
            await session.execute(text("DELETE FROM agent_toolkit_bindings"))
            await session.execute(
                text(
                    "DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_fltest%'"
                    " OR created_by = 'system:theme5-flattening'"
                )
            )
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE actor_id LIKE 'sva_fltest%'")
            )
            await session.execute(
                text("DELETE FROM audit_entries WHERE actor_id = 'system:theme5-flattening'")
            )
            await session.execute(text("DELETE FROM service_accounts WHERE id LIKE 'sva_fltest%'"))
            await session.execute(text("DELETE FROM agents WHERE id LIKE 'agnt_fltest%'"))
            await session.execute(text("DELETE FROM users WHERE id = :owner"), {"owner": _OWNER})
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


async def _seed_graph(control_db: DatabaseSession, admin_db: DatabaseSession) -> None:
    """Seed the full R-01 awkward-shape fixture (see module docstring)."""
    async with control_db.session() as session:
        session.add(Toolkit(id=_TK_A, name="fl-toolkit-a", active=True, created_by=_OWNER))
        session.add(Toolkit(id=_TK_B, name="fl-toolkit-b", active=True, created_by=_OWNER))
        session.add(
            Toolkit(id=_TK_INACTIVE, name="fl-toolkit-inact", active=False, created_by=_OWNER)
        )
        for cred_id, name in (
            (_CRED_ONE, "fl-cred-one"),
            (_CRED_TWO, "fl-cred-two"),
            # Two same-named same-API credentials (multi-account twins).
            (_CRED_DUP1, "fl-dup"),
            (_CRED_DUP2, "fl-dup"),
        ):
            session.add(
                Credential(
                    id=cred_id,
                    type="token_value",
                    name=name,
                    api_vendor="fltest.local",
                    api_name="fl-api",
                    created_by=_OWNER,
                )
            )
        await session.flush()
        for toolkit_id, cred_id in (
            (_TK_A, _CRED_ONE),
            (_TK_A, _CRED_TWO),
            (_TK_B, _CRED_ONE),
            (_TK_B, _CRED_DUP1),
            (_TK_B, _CRED_DUP2),
            (_TK_INACTIVE, _CRED_TWO),
        ):
            session.add(
                ToolkitCredentialBinding(
                    toolkit_id=toolkit_id,
                    credential_id=cred_id,
                    bound_at=_TCB_BOUND_AT,
                    created_by=_OWNER,
                )
            )
        # Divergent per-pair rules for (agent_a, cred_one)'s two paths, and
        # the same-vendor pooled shape: (tk_a, cred_one) has rules while
        # (tk_a, cred_two) has none, so cred_two's legacy pooled list
        # borrowed cred_one's rules — per-pair replay differs.
        rules = [
            (_TK_A, _CRED_ONE, "allow", "/repos/.*", 0),
            (_TK_A, _CRED_ONE, "deny", "/admin/.*", 1),
            (_TK_B, _CRED_ONE, "deny", "/repos/.*", 0),
            (_TK_INACTIVE, _CRED_TWO, "allow", "/inactive/.*", 0),
        ]
        for toolkit_id, cred_id, effect, path, sequence in rules:
            session.add(
                ToolkitPermissionRule(
                    toolkit_id=toolkit_id,
                    credential_id=cred_id,
                    effect=effect,
                    path=path,
                    match_mode="regex",
                    sequence=sequence,
                    created_by=_OWNER,
                )
            )
        # A live (unrevoked, unmigrated) jntc_live_ key, and a migrated one
        # whose successor actor carries scopes beyond capabilities:execute.
        session.add(
            ToolkitKey(
                id="ck_fltest_live",
                toolkit_id=_TK_A,
                hashed_key="argon2-fltest",
                key_preview="jntc_live_fl...",
                lookup_hash="fltest-lookup-live",
                label="fl-live-key",
                created_by=_OWNER,
            )
        )
        session.add(
            ToolkitKey(
                id="ck_fltest_migr",
                toolkit_id=_TK_A,
                hashed_key="argon2-fltest-2",
                key_preview="jntc_live_fm...",
                lookup_hash="fltest-lookup-migr",
                label="fl-migrated-key",
                revoked=True,
                migrated_actor_id=_MIGRATED_SVA,
                created_by=_OWNER,
            )
        )
        await session.commit()

    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name)"
                " VALUES (:id, 'fltest-owner@test.local', 'Fl', 'Owner')"
            ),
            {"id": _OWNER},
        )
        for agent_id, name in ((_AGENT_A, "fl-agent-a"), (_AGENT_B, "fl-agent-b")):
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, status)"
                    " VALUES (:id, :name, :owner, 'approved')"
                ),
                {"id": agent_id, "name": name, "owner": _OWNER},
            )
        await session.execute(
            text(
                "INSERT INTO service_accounts"
                " (id, name, description, owner_id, registered_by, status, created_by)"
                " VALUES (:id, 'toolkit-key:ck_fltest_migr', 'fl', :owner, :owner, 'active',"
                " :owner)"
            ),
            {"id": _MIGRATED_SVA, "owner": _OWNER},
        )
        for i, scope in enumerate(("capabilities:execute", "agents:read")):
            await session.execute(
                text(
                    "INSERT INTO actor_scope_grants"
                    " (id, actor_id, actor_type, scope, granted_by, created_by)"
                    " VALUES (:id, :actor, 'service_account', :scope, :owner, :owner)"
                ),
                {"id": f"asg_fltest_{i}", "actor": _MIGRATED_SVA, "scope": scope, "owner": _OWNER},
            )
        bindings = [
            ("atb_fltest_1", _AGENT_A, _TK_A),
            ("atb_fltest_2", _AGENT_A, _TK_B),
            ("atb_fltest_3", _AGENT_B, _TK_INACTIVE),
            # Dangling on both axes of the cross-DB join.
            ("atb_fltest_4", _AGENT_A, _TK_GHOST),
            ("atb_fltest_5", _GHOST_AGENT, _TK_A),
        ]
        for atb_id, agent_id, toolkit_id in bindings:
            await session.execute(
                text(
                    "INSERT INTO agent_toolkit_bindings"
                    " (id, agent_id, toolkit_id, bound_at, created_by)"
                    " VALUES (:id, :agent, :toolkit, :bound_at, :owner)"
                ),
                {
                    "id": atb_id,
                    "agent": agent_id,
                    "toolkit": toolkit_id,
                    "bound_at": _ATB_BOUND_AT.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
                    if admin_db.backend.dialect_name == "sqlite"
                    else _ATB_BOUND_AT,
                    "owner": _OWNER,
                },
            )
        await session.commit()


def _by_category(findings: list[Finding]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        grouped.setdefault(finding.category, []).append(finding.detail)
    return grouped


async def _direct_bindings(admin_db: DatabaseSession) -> dict[tuple[str, str], Any]:
    async with admin_db.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT agent_id, credential_id, rule_set_id, bound_at, created_by, suspended"
                    " FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_fltest%'"
                )
            )
        ).all()
    return {(row.agent_id, row.credential_id): row for row in rows}


def _as_dt(value: Any) -> dt.datetime:
    """Normalize a raw-SQL timestamp (str on SQLite, datetime on Postgres)."""
    parsed: dt.datetime = dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


async def test_flattening_creates_all_pairs_with_expected_semantics(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """One run migrates every reachable pair — conflicts default-deny, the
    inactive-toolkit pair included, provenance stamped — and reports every
    R-01 shape in its category."""
    await _seed_graph(control_db, admin_db)

    result = await ToolkitFlatteningService(integration_context).run()

    assert result.pairs_total == len(_EXPECTED_PAIRS)
    assert result.created == len(_EXPECTED_PAIRS)
    assert result.already_present == 0

    bindings = await _direct_bindings(admin_db)
    assert set(bindings) == _EXPECTED_PAIRS
    for row in bindings.values():
        assert row.created_by == "system:theme5-flattening"
        assert not row.suspended
        # max(source rows' timestamps): the tcb stamp (2026-02) beats the atb (2026-01).
        assert _as_dt(row.bound_at) == _TCB_BOUND_AT

    # The conflicting pair and the rule-less pairs bind default-deny (no set).
    for pair in _EXPECTED_PAIRS - {(_AGENT_B, _CRED_TWO)}:
        assert bindings[pair].rule_set_id is None, pair
    # The inactive-toolkit pair carries its copied rule set.
    inactive_set_id = bindings[(_AGENT_B, _CRED_TWO)].rule_set_id
    assert inactive_set_id is not None
    async with control_db.session() as session:
        rule_set = await session.get(PermissionRuleSet, inactive_set_id)
        assert rule_set is not None
        assert rule_set.name == f"theme5-flattening:{_TK_INACTIVE}:{_CRED_TWO}"
        copied = (
            (
                await session.execute(
                    select(PermissionRuleSetRule)
                    .where(PermissionRuleSetRule.rule_set_id == inactive_set_id)
                    .order_by(PermissionRuleSetRule.sequence)
                )
            )
            .scalars()
            .all()
        )
    assert [(r.effect, r.path) for r in copied] == [("allow", "/inactive/.*")]

    report = _by_category(result.findings)
    assert len(report["binding_created"]) == len(_EXPECTED_PAIRS)

    # Same-pair conflict: both contributing lists embedded verbatim.
    (conflict,) = report["rule_conflict"]
    assert (conflict["agent_id"], conflict["credential_id"]) == (_AGENT_A, _CRED_ONE)
    assert conflict["resolution"] == "default_deny"
    contributing = {c["toolkit_id"]: c["rules"] for c in conflict["contributing"]}
    assert [(r["effect"], r["path"]) for r in contributing[_TK_A]] == [
        ("allow", "/repos/.*"),
        ("deny", "/admin/.*"),
    ]
    assert [(r["effect"], r["path"]) for r in contributing[_TK_B]] == [("deny", "/repos/.*")]

    # Pooled-vs-per-pair drift: the rule-less same-vendor siblings that used
    # to borrow pooled rules (cred_two via tk_a; both dup twins via tk_b).
    drifted = {
        (d["agent_id"], d["credential_id"], d["toolkit_id"]) for d in report["pooled_rule_drift"]
    }
    assert drifted == {
        (_AGENT_A, _CRED_TWO, _TK_A),
        (_AGENT_A, _CRED_DUP1, _TK_B),
        (_AGENT_A, _CRED_DUP2, _TK_B),
    }
    for drift in report["pooled_rule_drift"]:
        assert drift["per_pair_rules"] == []
        assert drift["pooled_rules"], "the borrowed pooled list must be embedded"

    (inactive,) = report["inactive_toolkit_binding"]
    assert (inactive["agent_id"], inactive["toolkit_id"]) == (_AGENT_B, _TK_INACTIVE)

    dangling = {(d["missing"], d["missing_id"]) for d in report["dangling_reference"]}
    assert dangling == {("toolkit", _TK_GHOST), ("actor", _GHOST_AGENT)}
    # Dangling rows derive no binding.
    assert not any(agent == _GHOST_AGENT for agent, _ in bindings)

    (live_key,) = report["active_toolkit_key"]
    assert live_key["key_id"] == "ck_fltest_live"
    assert "hashed_key" not in live_key and "lookup_hash" not in live_key

    (scopes,) = report["scope_exceeds_execute"]
    assert scopes["service_account_id"] == _MIGRATED_SVA
    assert scopes["excess_scopes"] == ["agents:read"]

    # One audit entry per derived binding, system-actor attributed.
    async with admin_db.session() as session:
        audit_rows = (
            await session.execute(
                text(
                    "SELECT target_id, target_parent_id FROM audit_entries"
                    " WHERE actor_id = 'system:theme5-flattening'"
                    " AND target_type = 'credential_binding' AND action = 'grant'"
                )
            )
        ).all()
    assert len(audit_rows) == len(_EXPECTED_PAIRS)


async def test_second_run_and_diff_only_create_nothing(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """Double-run-and-diff: a second run (and a diff-only pass) report zero
    creations and duplicate no rows."""
    await _seed_graph(control_db, admin_db)
    service = ToolkitFlatteningService(integration_context)

    first = await service.run()
    second = await service.run()
    diff = await service.run(diff_only=True)

    assert first.created == len(_EXPECTED_PAIRS)
    assert second.created == 0
    assert second.already_present == len(_EXPECTED_PAIRS)
    assert diff.created == 0
    assert not any(f.category == "binding_created" for f in second.findings)
    assert not any(f.category == "binding_would_create" for f in diff.findings)
    assert len(await _direct_bindings(admin_db)) == len(_EXPECTED_PAIRS)
    async with control_db.session() as session:
        rule_sets = (
            (
                await session.execute(
                    select(PermissionRuleSet).where(
                        PermissionRuleSet.name.like("theme5-flattening:%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rule_sets) == 1  # only the inactive-toolkit pair has rules


async def test_diff_only_previews_without_writing(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """--diff-only reports what a run WOULD create and touches neither DB."""
    await _seed_graph(control_db, admin_db)

    result = await ToolkitFlatteningService(integration_context).run(diff_only=True)

    assert result.diff_only
    assert result.created == len(_EXPECTED_PAIRS)
    report = _by_category(result.findings)
    assert len(report["binding_would_create"]) == len(_EXPECTED_PAIRS)
    assert "binding_created" not in report
    assert await _direct_bindings(admin_db) == {}
    async with control_db.session() as session:
        rule_sets = (
            (
                await session.execute(
                    select(PermissionRuleSet).where(
                        PermissionRuleSet.name.like("theme5-flattening:%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rule_sets == []


async def _ack_rows(control_db: DatabaseSession) -> list[ToolkitFlatteningAck]:
    async with control_db.session() as session:
        return list((await session.execute(select(ToolkitFlatteningAck))).scalars().all())


async def test_verify_gates_acknowledgement_on_coverage(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """--verify fails (and --acknowledge is refused, writing no sentinel)
    until the flatten has run; afterwards it passes and the acknowledgement
    records the counts 6b will cite."""
    await _seed_graph(control_db, admin_db)
    service = ToolkitFlatteningService(integration_context)

    before = await service.verify(acknowledge=True)
    assert not before.passed
    assert before.missing_pair_count == len(_EXPECTED_PAIRS)
    assert not before.acknowledged
    assert await _ack_rows(control_db) == []
    missing = {
        (d.detail["agent_id"], d.detail["credential_id"])
        for d in before.findings
        if d.category == "verify_missing_binding"
    }
    assert missing == _EXPECTED_PAIRS

    await service.run()

    after = await service.verify(acknowledge=True)
    assert after.passed
    assert after.legacy_pair_count == len(_EXPECTED_PAIRS)
    assert after.missing_pair_count == 0
    assert after.acknowledged
    (ack,) = await _ack_rows(control_db)
    assert ack.legacy_pair_count == len(_EXPECTED_PAIRS)
    assert ack.direct_binding_count >= len(_EXPECTED_PAIRS)
    assert ack.report_finding_count == len(after.findings)
    assert ack.tool_version
    assert ack.created_by == "system:theme5-flattening"


async def test_verify_reports_rule_mismatch_without_failing(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    clean_tables: None,
) -> None:
    """Per-pair rule-list divergence is a report entry, not a verify failure."""
    await _seed_graph(control_db, admin_db)
    service = ToolkitFlatteningService(integration_context)
    await service.run()

    # An operator empties the flattened rule set post-run.
    async with control_db.session() as session:
        await session.execute(
            delete(PermissionRuleSetRule).where(
                PermissionRuleSetRule.rule_set_id.in_(
                    select(PermissionRuleSet.id).where(
                        PermissionRuleSet.name.like("theme5-flattening:%")
                    )
                )
            )
        )
        await session.commit()

    result = await service.verify()

    assert result.passed
    mismatches = [f.detail for f in result.findings if f.category == "verify_rule_mismatch"]
    assert [(m["agent_id"], m["credential_id"]) for m in mismatches] == [(_AGENT_B, _CRED_TWO)]
    assert [(r["effect"], r["path"]) for r in mismatches[0]["expected_rules"]] == [
        ("allow", "/inactive/.*")
    ]
    assert mismatches[0]["actual_rules"] == []
