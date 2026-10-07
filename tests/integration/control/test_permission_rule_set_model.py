"""Integration tests for the PermissionRuleSet / PermissionRuleSetRule models."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_rule_sets(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Empty both rule-set tables before and after each test."""
    async with control_db.session() as session:
        await session.execute(delete(PermissionRuleSetRule))
        await session.execute(delete(PermissionRuleSet))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(PermissionRuleSetRule))
        await session.execute(delete(PermissionRuleSet))
        await session.commit()


async def test_round_trip_defaults_and_id_prefixes(
    control_db: DatabaseSession, clean_rule_sets: None
) -> None:
    """A set and its rules persist with ``prs_``/``prr_`` ids and column defaults."""
    async with control_db.session() as session:
        rule_set = PermissionRuleSet(name="read-only-stripe", description="GETs only")
        session.add(rule_set)
        await session.flush()
        rule = PermissionRuleSetRule(
            rule_set_id=rule_set.id,
            effect="allow",
            methods=["GET"],
            path="/v1/.*",
            sequence=0,
        )
        session.add(rule)
        await session.commit()
        set_id, rule_id = rule_set.id, rule.id

    async with control_db.session() as session:
        got_set = (
            await session.execute(select(PermissionRuleSet).where(PermissionRuleSet.id == set_id))
        ).scalar_one()
        got_rule = (
            await session.execute(
                select(PermissionRuleSetRule).where(PermissionRuleSetRule.id == rule_id)
            )
        ).scalar_one()
    assert got_set.id.startswith("prs_")
    assert got_set.name == "read-only-stripe"
    assert got_rule.id.startswith("prr_")
    assert got_rule.match_mode == "regex"
    assert got_rule.is_system is False
    assert got_rule.sequence == 0


async def test_set_name_is_unique(control_db: DatabaseSession, clean_rule_sets: None) -> None:
    """Two sets with the same name violate ``uq_permission_rule_sets_name``."""
    async with control_db.session() as session:
        session.add(PermissionRuleSet(name="dupe"))
        await session.commit()
    with pytest.raises(IntegrityError):
        async with control_db.session() as session:
            session.add(PermissionRuleSet(name="dupe"))
            await session.commit()


async def test_duplicate_sequence_in_set_rejected(
    control_db: DatabaseSession, clean_rule_sets: None
) -> None:
    """Two rules with the same sequence in one set violate the unique constraint

    (``uq_permission_rule_set_rules_seq`` — evaluation order must be
    backend-independent, mirroring the inline-rules constraint)."""
    async with control_db.session() as session:
        rule_set = PermissionRuleSet(name="seq-test")
        session.add(rule_set)
        await session.flush()
        set_id = rule_set.id
        session.add(PermissionRuleSetRule(rule_set_id=set_id, effect="deny", path=".*", sequence=0))
        await session.commit()
    with pytest.raises(IntegrityError):
        async with control_db.session() as session:
            session.add(
                PermissionRuleSetRule(
                    rule_set_id=set_id, effect="allow", methods=["GET"], sequence=0
                )
            )
            await session.commit()


async def test_same_sequence_across_sets_allowed(
    control_db: DatabaseSession, clean_rule_sets: None
) -> None:
    """The sequence constraint is per set — two sets may both have sequence 0."""
    async with control_db.session() as session:
        set_a = PermissionRuleSet(name="set-a")
        set_b = PermissionRuleSet(name="set-b")
        session.add_all([set_a, set_b])
        await session.flush()
        session.add_all(
            [
                PermissionRuleSetRule(rule_set_id=set_a.id, effect="deny", path=".*", sequence=0),
                PermissionRuleSetRule(rule_set_id=set_b.id, effect="deny", path=".*", sequence=0),
            ]
        )
        await session.commit()

    async with control_db.session() as session:
        count = len((await session.execute(select(PermissionRuleSetRule))).scalars().all())
    assert count == 2


async def test_rules_cascade_with_set_deletion(
    control_db: DatabaseSession, clean_rule_sets: None
) -> None:
    """Deleting a set removes its rules via the FK CASCADE."""
    async with control_db.session() as session:
        rule_set = PermissionRuleSet(name="cascade-test")
        session.add(rule_set)
        await session.flush()
        set_id = rule_set.id
        session.add(PermissionRuleSetRule(rule_set_id=set_id, effect="deny", path=".*", sequence=0))
        await session.commit()

    async with control_db.session() as session:
        await session.execute(delete(PermissionRuleSet).where(PermissionRuleSet.id == set_id))
        await session.commit()

    async with control_db.session() as session:
        remaining = (
            (
                await session.execute(
                    select(PermissionRuleSetRule).where(PermissionRuleSetRule.rule_set_id == set_id)
                )
            )
            .scalars()
            .all()
        )
    assert remaining == []
