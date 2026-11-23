"""Integration tests for TokenLineageRepository paired access/refresh sweeps."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.repos import TokenLineageRepository, TokenSweep
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_token_rows(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Ensure access_tokens and refresh_tokens are empty before and after each test."""
    async with admin_db.transaction() as session:
        await session.execute(delete(AccessToken))
        await session.execute(delete(RefreshToken))
    yield
    async with admin_db.transaction() as session:
        await session.execute(delete(AccessToken))
        await session.execute(delete(RefreshToken))


def _hash(label: str) -> str:
    return label.ljust(64, "0")


def _access_row(
    label: str,
    *,
    actor_id: str = "usr_1",
    actor_type: str = "user",
    token_family_id: str = "tfam_1",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    oauth_client_id: str | None = None,
    oauth_grant_id: str | None = None,
) -> AccessToken:
    return AccessToken(
        token_hash=_hash(f"at-{label}"),
        actor_id=actor_id,
        actor_type=actor_type,
        scopes=["apis:read"],
        token_family_id=token_family_id,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(hours=1)),
        revoked_at=revoked_at,
        oauth_client_id=oauth_client_id,
        oauth_grant_id=oauth_grant_id,
        created_by=actor_id,
    )


def _refresh_row(
    label: str,
    *,
    actor_id: str = "usr_1",
    actor_type: str = "user",
    token_family_id: str = "tfam_1",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    oauth_client_id: str | None = None,
    oauth_grant_id: str | None = None,
) -> RefreshToken:
    return RefreshToken(
        token_hash=_hash(f"rt-{label}"),
        actor_id=actor_id,
        actor_type=actor_type,
        scopes=["apis:read"],
        token_family_id=token_family_id,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(days=7)),
        revoked_at=revoked_at,
        oauth_client_id=oauth_client_id,
        oauth_grant_id=oauth_grant_id,
        created_by=actor_id,
    )


async def _load_by_hash(
    admin_db: DatabaseSession,
) -> tuple[dict[str, AccessToken], dict[str, RefreshToken]]:
    async with admin_db.session() as session:
        at_rows = (await session.execute(select(AccessToken))).scalars().all()
        rt_rows = (await session.execute(select(RefreshToken))).scalars().all()
    return {r.token_hash: r for r in at_rows}, {r.token_hash: r for r in rt_rows}


async def test_revoke_family_revokes_live_rows_and_preserves_revoked_and_unrelated(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    prior_revoked_at = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_row("fam-live-1", token_family_id="tfam_target"),
                _access_row("fam-live-2", token_family_id="tfam_target"),
                _access_row(
                    "fam-already-revoked",
                    token_family_id="tfam_target",
                    revoked_at=prior_revoked_at,
                ),
                _access_row("fam-other", token_family_id="tfam_other"),
                _refresh_row("fam-live-1", token_family_id="tfam_target"),
                _refresh_row(
                    "fam-already-revoked",
                    token_family_id="tfam_target",
                    revoked_at=prior_revoked_at,
                ),
                _refresh_row("fam-other", token_family_id="tfam_other"),
            ]
        )

    async with admin_db.transaction() as session:
        sweep = await TokenLineageRepository.revoke_family(session, "tfam_target")

    assert sweep == TokenSweep(access=2, refresh=1)
    assert sweep.access == 2
    assert sweep.refresh == 1

    at_map, rt_map = await _load_by_hash(admin_db)
    assert at_map[_hash("at-fam-live-1")].revoked_at is not None
    assert at_map[_hash("at-fam-live-2")].revoked_at is not None
    assert at_map[_hash("at-fam-already-revoked")].revoked_at == prior_revoked_at
    assert at_map[_hash("at-fam-other")].revoked_at is None

    assert rt_map[_hash("rt-fam-live-1")].revoked_at is not None
    assert rt_map[_hash("rt-fam-already-revoked")].revoked_at == prior_revoked_at
    assert rt_map[_hash("rt-fam-other")].revoked_at is None

    first_at_revoked = at_map[_hash("at-fam-live-1")].revoked_at
    first_rt_revoked = rt_map[_hash("rt-fam-live-1")].revoked_at

    # Idempotent re-sweep must not refresh timestamps on already-revoked rows.
    async with admin_db.transaction() as session:
        resweep = await TokenLineageRepository.revoke_family(session, "tfam_target")

    assert resweep == TokenSweep(access=0, refresh=0)
    at_map_after, rt_map_after = await _load_by_hash(admin_db)
    assert at_map_after[_hash("at-fam-live-1")].revoked_at == first_at_revoked
    assert rt_map_after[_hash("rt-fam-live-1")].revoked_at == first_rt_revoked


async def test_revoke_for_actor_filters_by_actor_id_and_actor_type(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    prior_revoked_at = datetime(2025, 2, 1, 8, 30, 0, tzinfo=UTC)
    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_row("actor-live", actor_id="usr_target", actor_type="user"),
                _access_row(
                    "actor-revoked",
                    actor_id="usr_target",
                    actor_type="user",
                    revoked_at=prior_revoked_at,
                ),
                _access_row("actor-diff-type", actor_id="usr_target", actor_type="agent"),
                _access_row("actor-other", actor_id="usr_other", actor_type="user"),
                _refresh_row("actor-live-1", actor_id="usr_target", actor_type="user"),
                _refresh_row("actor-live-2", actor_id="usr_target", actor_type="user"),
                _refresh_row(
                    "actor-revoked",
                    actor_id="usr_target",
                    actor_type="user",
                    revoked_at=prior_revoked_at,
                ),
                _refresh_row("actor-diff-type", actor_id="usr_target", actor_type="agent"),
                _refresh_row("actor-other", actor_id="usr_other", actor_type="user"),
            ]
        )

    async with admin_db.transaction() as session:
        sweep = await TokenLineageRepository.revoke_for_actor(
            session, "usr_target", actor_type="user"
        )

    assert sweep == TokenSweep(access=1, refresh=2)

    at_map, rt_map = await _load_by_hash(admin_db)
    assert at_map[_hash("at-actor-live")].revoked_at is not None
    assert at_map[_hash("at-actor-revoked")].revoked_at == prior_revoked_at
    assert at_map[_hash("at-actor-diff-type")].revoked_at is None
    assert at_map[_hash("at-actor-other")].revoked_at is None

    assert rt_map[_hash("rt-actor-live-1")].revoked_at is not None
    assert rt_map[_hash("rt-actor-live-2")].revoked_at is not None
    assert rt_map[_hash("rt-actor-revoked")].revoked_at == prior_revoked_at
    assert rt_map[_hash("rt-actor-diff-type")].revoked_at is None
    assert rt_map[_hash("rt-actor-other")].revoked_at is None


async def test_revoke_by_grant_revokes_matching_grant_only(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    prior_revoked_at = datetime(2025, 3, 10, 9, 0, 0, tzinfo=UTC)
    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_row("grant-live-1", oauth_grant_id="ocg_target"),
                _access_row("grant-live-2", oauth_grant_id="ocg_target"),
                _access_row(
                    "grant-revoked",
                    oauth_grant_id="ocg_target",
                    revoked_at=prior_revoked_at,
                ),
                _access_row("grant-other", oauth_grant_id="ocg_other"),
                _access_row("grant-none", oauth_grant_id=None),
                _refresh_row("grant-live", oauth_grant_id="ocg_target"),
                _refresh_row(
                    "grant-revoked",
                    oauth_grant_id="ocg_target",
                    revoked_at=prior_revoked_at,
                ),
                _refresh_row("grant-other", oauth_grant_id="ocg_other"),
                _refresh_row("grant-none", oauth_grant_id=None),
            ]
        )

    async with admin_db.transaction() as session:
        sweep = await TokenLineageRepository.revoke_by_grant(session, "ocg_target")

    assert sweep == TokenSweep(access=2, refresh=1)

    at_map, rt_map = await _load_by_hash(admin_db)
    assert at_map[_hash("at-grant-live-1")].revoked_at is not None
    assert at_map[_hash("at-grant-live-2")].revoked_at is not None
    assert at_map[_hash("at-grant-revoked")].revoked_at == prior_revoked_at
    assert at_map[_hash("at-grant-other")].revoked_at is None
    assert at_map[_hash("at-grant-none")].revoked_at is None

    assert rt_map[_hash("rt-grant-live")].revoked_at is not None
    assert rt_map[_hash("rt-grant-revoked")].revoked_at == prior_revoked_at
    assert rt_map[_hash("rt-grant-other")].revoked_at is None
    assert rt_map[_hash("rt-grant-none")].revoked_at is None


async def test_revoke_by_client_revokes_matching_client_only(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    prior_revoked_at = datetime(2025, 4, 20, 14, 15, 0, tzinfo=UTC)
    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_row("client-live", oauth_client_id="oc_target"),
                _access_row(
                    "client-revoked",
                    oauth_client_id="oc_target",
                    revoked_at=prior_revoked_at,
                ),
                _access_row("client-other", oauth_client_id="oc_other"),
                _access_row("client-none", oauth_client_id=None),
                _refresh_row("client-live-1", oauth_client_id="oc_target"),
                _refresh_row("client-live-2", oauth_client_id="oc_target"),
                _refresh_row(
                    "client-revoked",
                    oauth_client_id="oc_target",
                    revoked_at=prior_revoked_at,
                ),
                _refresh_row("client-other", oauth_client_id="oc_other"),
                _refresh_row("client-none", oauth_client_id=None),
            ]
        )

    async with admin_db.transaction() as session:
        sweep = await TokenLineageRepository.revoke_by_client(session, "oc_target")

    assert sweep == TokenSweep(access=1, refresh=2)

    at_map, rt_map = await _load_by_hash(admin_db)
    assert at_map[_hash("at-client-live")].revoked_at is not None
    assert at_map[_hash("at-client-revoked")].revoked_at == prior_revoked_at
    assert at_map[_hash("at-client-other")].revoked_at is None
    assert at_map[_hash("at-client-none")].revoked_at is None

    assert rt_map[_hash("rt-client-live-1")].revoked_at is not None
    assert rt_map[_hash("rt-client-live-2")].revoked_at is not None
    assert rt_map[_hash("rt-client-revoked")].revoked_at == prior_revoked_at
    assert rt_map[_hash("rt-client-other")].revoked_at is None
    assert rt_map[_hash("rt-client-none")].revoked_at is None


async def test_delete_for_actor_deletes_actor_and_grant_lineage_rows(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    prior_revoked_at = datetime(2025, 5, 1, 0, 0, 0, tzinfo=UTC)
    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_row("actor-live", actor_id="agnt_target", actor_type="agent"),
                _access_row(
                    "actor-revoked",
                    actor_id="agnt_target",
                    actor_type="agent",
                    revoked_at=prior_revoked_at,
                ),
                # Held by another actor_id, but minted under one of grant_ids (pins or_).
                _access_row(
                    "grant-lineage",
                    actor_id="usr_delegator",
                    actor_type="user",
                    oauth_grant_id="ocg_del_1",
                ),
                _access_row(
                    "unrelated",
                    actor_id="agnt_other",
                    actor_type="agent",
                    oauth_grant_id="ocg_keep",
                ),
                _refresh_row("actor-live", actor_id="agnt_target", actor_type="agent"),
                _refresh_row(
                    "grant-lineage",
                    actor_id="usr_delegator",
                    actor_type="user",
                    oauth_grant_id="ocg_del_2",
                ),
                _refresh_row(
                    "unrelated",
                    actor_id="agnt_other",
                    actor_type="agent",
                    oauth_grant_id="ocg_keep",
                ),
            ]
        )

    async with admin_db.transaction() as session:
        sweep = await TokenLineageRepository.delete_for_actor(
            session, "agnt_target", grant_ids=["ocg_del_1", "ocg_del_2"]
        )

    assert sweep == TokenSweep(access=3, refresh=2)

    at_map, rt_map = await _load_by_hash(admin_db)
    assert set(at_map) == {_hash("at-unrelated")}
    assert set(rt_map) == {_hash("rt-unrelated")}


async def test_delete_for_actor_without_grant_ids_deletes_only_actor_rows(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_row("actor-only", actor_id="agnt_solo", oauth_grant_id="ocg_1"),
                _access_row("other-same-grant", actor_id="usr_other", oauth_grant_id="ocg_1"),
                _refresh_row("actor-only", actor_id="agnt_solo", oauth_grant_id="ocg_1"),
                _refresh_row("other-same-grant", actor_id="usr_other", oauth_grant_id="ocg_1"),
            ]
        )

    async with admin_db.transaction() as session:
        sweep = await TokenLineageRepository.delete_for_actor(session, "agnt_solo")

    assert sweep == TokenSweep(access=1, refresh=1)
    at_map, rt_map = await _load_by_hash(admin_db)
    assert set(at_map) == {_hash("at-other-same-grant")}
    assert set(rt_map) == {_hash("rt-other-same-grant")}


async def test_delete_expired_deletes_only_rows_strictly_before_cutoff(
    admin_db: DatabaseSession, clean_token_rows: None
) -> None:
    cutoff = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    before_cutoff = cutoff - timedelta(seconds=1)
    after_cutoff = cutoff + timedelta(seconds=1)

    async with admin_db.transaction() as session:
        session.add_all(
            [
                _access_row("expired-live", expires_at=before_cutoff),
                _access_row(
                    "expired-revoked",
                    expires_at=before_cutoff,
                    revoked_at=before_cutoff,
                ),
                _access_row("at-cutoff", expires_at=cutoff),
                _access_row("after-cutoff", expires_at=after_cutoff),
                _refresh_row("expired-live", expires_at=before_cutoff),
                _refresh_row("at-cutoff", expires_at=cutoff),
                _refresh_row("after-cutoff", expires_at=after_cutoff),
            ]
        )

    async with admin_db.transaction() as session:
        sweep = await TokenLineageRepository.delete_expired(session, before=cutoff)

    assert sweep == TokenSweep(access=2, refresh=1)

    at_map, rt_map = await _load_by_hash(admin_db)
    assert set(at_map) == {_hash("at-at-cutoff"), _hash("at-after-cutoff")}
    assert set(rt_map) == {_hash("rt-at-cutoff"), _hash("rt-after-cutoff")}
