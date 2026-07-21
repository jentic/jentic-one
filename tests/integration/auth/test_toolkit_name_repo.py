"""Integration tests for the cross-DB toolkit-name lookup (issue #686).

``ToolkitNameRepository`` resolves toolkit ids to names in the control DB via raw
SQL so the auth surface can enrich the /me whoami binding list without importing
the control ORM. These tests hit a real control database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text

from jentic_one.auth.repos import ToolkitNameRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_TK_A = "tk_name_lookup_a"
_TK_B = "tk_name_lookup_b"
_TK_MISSING = "tk_name_lookup_missing"


@pytest.fixture()
async def seeded_toolkits(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with control_db.session() as session:
        for tk_id, name in ((_TK_A, "Design news radar"), (_TK_B, "Payments toolkit")):
            await session.execute(
                text(
                    "INSERT INTO toolkits (id, name, created_by) "
                    "VALUES (:id, :name, :created_by) ON CONFLICT DO NOTHING"
                ),
                {"id": tk_id, "name": name, "created_by": "usr_name_lookup"},
            )
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(
            text("DELETE FROM toolkits WHERE id IN (:a, :b)"), {"a": _TK_A, "b": _TK_B}
        )
        await session.commit()


async def test_get_names_for_ids_resolves_known(
    control_db: DatabaseSession, seeded_toolkits: None
) -> None:
    async with control_db.session() as session:
        names = await ToolkitNameRepository.get_names_for_ids(session, [_TK_A, _TK_B])
    assert names == {_TK_A: "Design news radar", _TK_B: "Payments toolkit"}


async def test_get_names_for_ids_omits_missing(
    control_db: DatabaseSession, seeded_toolkits: None
) -> None:
    async with control_db.session() as session:
        names = await ToolkitNameRepository.get_names_for_ids(session, [_TK_A, _TK_MISSING])
    # A since-deleted (or never-existent) toolkit is omitted rather than raising,
    # so the caller degrades to name=None instead of failing the whole response.
    assert names == {_TK_A: "Design news radar"}
    assert _TK_MISSING not in names


async def test_get_names_for_ids_empty_input(control_db: DatabaseSession) -> None:
    async with control_db.session() as session:
        assert await ToolkitNameRepository.get_names_for_ids(session, []) == {}
