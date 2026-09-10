"""Integration tests for bind-time credential resolution (``EffectsRepository``).

Exercises ``resolve_credentials_for_api`` and its admin-DB push-down companion
``list_bound_credential_ids_for_owned_agents`` against real DBs. Theme-5
Phase 3 successors of ``resolve_toolkits_for_api``: the candidate axis is the
credential itself, scoped by the decider's owner axis (own credentials plus
credentials bound to agents they own — hard problem 8), with the cross-DB seam
kept as a pushed-down id list (hard problem 9). Stored credential identities
are canonical (slugified vendor/name), so a *raw* reference axis
(``GitHub.com``) must be canonicalized at the SQL boundary or it would match
nothing — the regression these tests guard (#656).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, text

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_VENDOR_SLUG = "github-com"
_OWNER_A = "usr_effres_owner_a"
_OWNER_B = "usr_effres_owner_b"


@pytest.fixture()
async def clean_tables(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR_SLUG))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_credential(
    control_db: DatabaseSession,
    *,
    name: str,
    api_name: str | None,
    api_version: str | None,
    created_by: str | None = None,
) -> str:
    """Seed a canonical credential; return its id."""
    credential = Credential(
        type="token_value",
        name=name,
        api_vendor=_VENDOR_SLUG,
        api_name=api_name,
        api_version=api_version,
        created_by=created_by,
    )
    async with control_db.session() as session:
        session.add(credential)
        await session.flush()
        cred_id = credential.id
        await session.commit()
    return cred_id


async def test_resolve_canonicalizes_raw_reference_vendor_and_name(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """A raw ``GitHub.com``/``Repos-API`` reference resolves the canonical stored row."""
    cred_id = await _seed_credential(
        control_db, name="cred-eff-canon", api_name="repos-api", api_version="v3"
    )

    async with control_db.session() as session:
        credentials = await EffectsRepository.resolve_credentials_for_api(
            session, vendor="GitHub.com", name="Repos-API", version="v3", owner_ids=None
        )

    assert credentials == [cred_id]


async def test_resolve_wildcard_credential_matches_name_scoped_reference(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """A vendor-wide (NULL name) credential is resolved for a name-scoped reference."""
    cred_id = await _seed_credential(
        control_db, name="cred-eff-wild", api_name=None, api_version=None
    )

    async with control_db.session() as session:
        credentials = await EffectsRepository.resolve_credentials_for_api(
            session, vendor="github.com", name="repos-api", version="v3", owner_ids=None
        )

    assert credentials == [cred_id]


async def test_resolve_prefers_exact_name_over_wildcard(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """An exact-name credential is preferred over a vendor-wide wildcard (#775).

    Preferring exactness keeps a name-specific reference from resolving
    ambiguous when a broad NULL-name credential coexists with the precise one.
    """
    exact_id = await _seed_credential(
        control_db, name="cred-eff-exact", api_name="repos-api", api_version="v3"
    )
    await _seed_credential(control_db, name="cred-eff-any", api_name=None, api_version=None)

    async with control_db.session() as session:
        credentials = await EffectsRepository.resolve_credentials_for_api(
            session, vendor="github.com", name="repos-api", version="v3", owner_ids=None
        )

    assert credentials == [exact_id]


async def test_resolve_owner_scoped_excludes_foreign_credentials(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """With ``owner_ids`` set, another owner's covering credential is invisible —
    a non-admin decider must never resolve (and so silently bind to) a
    credential they don't govern."""
    mine = await _seed_credential(
        control_db,
        name="cred-eff-mine",
        api_name="repos-api",
        api_version=None,
        created_by=_OWNER_A,
    )
    await _seed_credential(
        control_db,
        name="cred-eff-theirs",
        api_name="repos-api",
        api_version=None,
        created_by=_OWNER_B,
    )

    async with control_db.session() as session:
        credentials = await EffectsRepository.resolve_credentials_for_api(
            session,
            vendor="github.com",
            name="repos-api",
            version=None,
            owner_ids=[_OWNER_A],
        )

    assert credentials == [mine]


async def test_resolve_bound_pushdown_widens_owner_scope(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """A foreign credential in ``bound_credential_ids`` IS a candidate: the
    decider governs it through an agent they own that is bound to it (the
    binding-widened owner axis of hard problem 8, pushed down as a plain id
    list so the control query never references an admin table)."""
    theirs = await _seed_credential(
        control_db,
        name="cred-eff-widened",
        api_name="repos-api",
        api_version=None,
        created_by=_OWNER_B,
    )

    async with control_db.session() as session:
        without_pushdown = await EffectsRepository.resolve_credentials_for_api(
            session,
            vendor="github.com",
            name="repos-api",
            version=None,
            owner_ids=[_OWNER_A],
        )
        with_pushdown = await EffectsRepository.resolve_credentials_for_api(
            session,
            vendor="github.com",
            name="repos-api",
            version=None,
            owner_ids=[_OWNER_A],
            bound_credential_ids=[theirs],
        )

    assert without_pushdown == []
    assert with_pushdown == [theirs]


async def test_resolve_empty_owner_axis_returns_no_candidates(
    control_db: DatabaseSession, clean_tables: None
) -> None:
    """Empty owner list + empty push-down = no candidates (never a full scan).

    ``owner_ids=None`` is the deliberate org-admin escape hatch; an empty list
    is a real (ownerless) axis and must resolve nothing.
    """
    await _seed_credential(
        control_db,
        name="cred-eff-unowned-viewer",
        api_name="repos-api",
        api_version=None,
        created_by=_OWNER_A,
    )
    async with control_db.session() as session:
        credentials = await EffectsRepository.resolve_credentials_for_api(
            session,
            vendor="github.com",
            name="repos-api",
            version=None,
            owner_ids=[],
            bound_credential_ids=[],
        )
    assert credentials == []


# --- list_bound_credential_ids_for_owned_agents (admin DB push-down) ---


@pytest.fixture()
async def seed_owned_agent_bindings(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Two owners, one agent each, each bound to a distinct credential id.

    Owner A's binding is SUSPENDED — suspension is a broker-derivation cut-off,
    not a governance-ownership change, so it must still count for resolution.
    """

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text(
                    "DELETE FROM agent_credential_bindings "
                    "WHERE agent_id IN ('agnt_effres_a', 'agnt_effres_b')"
                )
            )
            await session.execute(
                text("DELETE FROM agents WHERE id IN ('agnt_effres_a', 'agnt_effres_b')")
            )
            await session.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": _OWNER_A, "b": _OWNER_B}
            )
            await session.commit()

    await _cleanup()
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) VALUES "
                "(:a, 'effres-a@test.local', 'Anna', 'A'), "
                "(:b, 'effres-b@test.local', 'Ben', 'B') "
                "ON CONFLICT DO NOTHING"
            ),
            {"a": _OWNER_A, "b": _OWNER_B},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status) VALUES "
                "('agnt_effres_a', 'effres-agent-a', :a, :a, 'active'), "
                "('agnt_effres_b', 'effres-agent-b', :b, :b, 'active') "
                "ON CONFLICT DO NOTHING"
            ),
            {"a": _OWNER_A, "b": _OWNER_B},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings "
                "(id, agent_id, credential_id, suspended) VALUES "
                "('acb_effres_a', 'agnt_effres_a', 'cred_effres_a', true), "
                "('acb_effres_b', 'agnt_effres_b', 'cred_effres_b', false) "
                "ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()
    yield
    await _cleanup()


async def test_list_bound_credential_ids_scopes_by_agent_owner(
    admin_db: DatabaseSession, seed_owned_agent_bindings: None
) -> None:
    """Only credentials bound to agents the given owners own are returned —
    and a suspended binding still counts (the owner can still govern it)."""
    async with admin_db.session() as session:
        for_a = await EffectsRepository.list_bound_credential_ids_for_owned_agents(
            session, owner_ids=[_OWNER_A]
        )
        for_both = await EffectsRepository.list_bound_credential_ids_for_owned_agents(
            session, owner_ids=[_OWNER_A, _OWNER_B]
        )
        for_none = await EffectsRepository.list_bound_credential_ids_for_owned_agents(
            session, owner_ids=[]
        )

    assert for_a == ["cred_effres_a"]  # suspended, still counted
    assert sorted(for_both) == ["cred_effres_a", "cred_effres_b"]
    assert for_none == []
