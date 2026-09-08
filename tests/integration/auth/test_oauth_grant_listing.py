"""Integration tests for the grant listing surfaces.

Covers the read side of the grant registry end-to-end at the service layer:
the per-agent "Connected clients" listing with its owner-or-admin
authorization matrix, the admin cross-view with its filters and keyset
pagination (including the id tiebreaker on ``created_at`` ties), the
display enrichment (client name + redirect-URI origin + consenting
``user_id`` — gap G10), the per-item ``can_revoke`` capability (the G10
list/revoke predicate divergence made explicit), and the per-client
active-grant counts folded into the admin client listing.

    Seed helpers and the ``clean_grants`` fixture are shared with the grant
channel tests via :mod:`tests.integration.auth.seeds` and this package's
``conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.repos import AgentRepository
from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.oauth_grant_admin_service import OAuthGrantAdminService
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    OAuthGrantAccessDeniedError,
)
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from tests.integration.auth import seeds

pytestmark = pytest.mark.integration

_CLIENT_ID = seeds.CLIENT_ID
_seed_user = seeds.seed_user
_seed_agent = seeds.seed_agent
_seed_client = seeds.seed_client

#: Cross-view caller for tests where the viewer's revoke capability is not the
#: subject under test (org:admin holds the revoke set, so can_revoke is True).
_VIEWER = Identity(sub="usr_l_viewer", email="", permissions=["org:admin"])


async def _force_created_at(ctx: Context, grant_ids: list[str], created_at: datetime) -> None:
    """Pin several grants to one ``created_at`` — a keyset-tie fixture.

    ``create_grant`` stamps distinct timestamps, so boundary ties (burst
    consents, coarse DB timestamp precision) are manufactured directly.
    """
    async with ctx.admin_db.session() as session:
        await session.execute(
            update(OAuthClientGrant)
            .where(OAuthClientGrant.id.in_(grant_ids))
            .values(created_at=created_at)
        )
        await session.commit()


# --- per-agent listing: authorization matrix ---------------------------------


async def test_list_grants_for_agent_owner_or_admin_matrix(
    integration_context: Context, clean_grants: None
) -> None:
    """Owner OK; stranger 403-mapped; each admin read permission OK; unknown
    agent 404-mapped — mirroring the ``:revoke`` semantics on the read side."""
    owner_id = await _seed_user(integration_context, "usr_l_owner")
    stranger_id = await _seed_user(integration_context, "usr_l_stranger")
    admin_id = await _seed_user(integration_context, "usr_l_admin")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)

    grant_id = await grant_svc.create_grant(
        user_id=owner_id, oauth_client_id=_CLIENT_ID, agent_id=agent_id, scopes=["apis:read"]
    )

    with pytest.raises(ActorNotFoundError):
        await grant_svc.list_grants_for_agent(
            "agt_missing", identity=Identity(sub=owner_id, email="")
        )

    with pytest.raises(OAuthGrantAccessDeniedError):
        await grant_svc.list_grants_for_agent(
            agent_id, identity=Identity(sub=stranger_id, email="")
        )

    page = await grant_svc.list_grants_for_agent(
        agent_id, identity=Identity(sub=owner_id, email="")
    )
    assert [g.id for g in page.data] == [grant_id]

    # Each admin permission unlocks the read: the write pair from :revoke plus
    # the read half that gates the admin cross-view.
    for permission in ("org:admin", "oauth-clients:write", "oauth-clients:read"):
        page = await grant_svc.list_grants_for_agent(
            agent_id,
            identity=Identity(sub=admin_id, email="", permissions=[permission]),
        )
        assert [g.id for g in page.data] == [grant_id]


async def test_list_grants_for_agent_enrichment_and_status_filter(
    integration_context: Context, clean_grants: None
) -> None:
    """Items carry the display fields — client name, redirect-URI origin,
    consenting ``user_id`` (G10) — and the status filter separates the active
    row from revoked history."""
    owner_id = await _seed_user(integration_context, "usr_l_enrich")
    agent_id = await _seed_agent(integration_context, owner_id=owner_id, scopes=["apis:read"])
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)
    owner = Identity(sub=owner_id, email="")

    # Two consents for the same pair: the pair-collapse revokes the first.
    await grant_svc.create_grant(
        user_id=owner_id, oauth_client_id=_CLIENT_ID, agent_id=agent_id, scopes=["apis:read"]
    )
    active_id = await grant_svc.create_grant(
        user_id=owner_id, oauth_client_id=_CLIENT_ID, agent_id=agent_id, scopes=["apis:read"]
    )

    active = await grant_svc.list_grants_for_agent(agent_id, identity=owner, status="active")
    assert [g.id for g in active.data] == [active_id]
    view = active.data[0]
    assert view.client_name == "Grant Channel App"
    assert view.client_origin == "https://mcpapp.example.com"
    assert view.user_id == owner_id  # the consenting owner, surfaced per G10
    assert view.agent_id == agent_id
    assert view.scopes == ["apis:read"]
    assert view.created_at is not None and view.revoked_at is None

    revoked = await grant_svc.list_grants_for_agent(agent_id, identity=owner, status="revoked")
    assert len(revoked.data) == 1
    assert revoked.data[0].revoked_at is not None

    everything = await grant_svc.list_grants_for_agent(agent_id, identity=owner)
    assert len(everything.data) == 2
    # Newest first.
    assert everything.data[0].id == active_id


# --- admin cross-view: filters + pagination ----------------------------------


async def test_admin_cross_view_filters(integration_context: Context, clean_grants: None) -> None:
    """GET /admin/oauth-grants filters: agent, consenting user, client, status
    — each narrowing the cross-view independently."""
    user_a = await _seed_user(integration_context, "usr_l_xa")
    user_b = await _seed_user(integration_context, "usr_l_xb")
    agent_a = await _seed_agent(
        integration_context, owner_id=user_a, scopes=["apis:read"], name="xview-agent-a"
    )
    agent_b = await _seed_agent(
        integration_context, owner_id=user_b, scopes=["apis:read"], name="xview-agent-b"
    )
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    other_client = await _seed_client(
        integration_context, allowed_scopes=["apis:read"], client_id="oc_grant_listing_other"
    )
    grant_svc = OAuthGrantService(integration_context)

    g_a = await grant_svc.create_grant(
        user_id=user_a, oauth_client_id=_CLIENT_ID, agent_id=agent_a, scopes=["apis:read"]
    )
    g_b = await grant_svc.create_grant(
        user_id=user_b, oauth_client_id=_CLIENT_ID, agent_id=agent_b, scopes=["apis:read"]
    )
    g_c = await grant_svc.create_grant(
        user_id=user_b, oauth_client_id=other_client, agent_id=agent_b, scopes=["apis:read"]
    )
    await grant_svc.revoke_grant(g_b, identity=Identity(sub=user_b, email=""))

    admin_svc = OAuthGrantAdminService(integration_context)

    everything = await admin_svc.list_grants(identity=_VIEWER)
    assert {g.id for g in everything.data} == {g_a, g_b, g_c}

    by_agent = await admin_svc.list_grants(identity=_VIEWER, agent_id=agent_a)
    assert [g.id for g in by_agent.data] == [g_a]

    by_user = await admin_svc.list_grants(identity=_VIEWER, user_id=user_b)
    assert {g.id for g in by_user.data} == {g_b, g_c}

    by_client = await admin_svc.list_grants(identity=_VIEWER, client_id=other_client)
    assert [g.id for g in by_client.data] == [g_c]

    active = await admin_svc.list_grants(identity=_VIEWER, status="active")
    assert {g.id for g in active.data} == {g_a, g_c}
    revoked = await admin_svc.list_grants(identity=_VIEWER, status="revoked")
    assert [g.id for g in revoked.data] == [g_b]

    with pytest.raises(InvalidInputError, match="status"):
        await admin_svc.list_grants(identity=_VIEWER, status="bogus")


async def test_admin_cross_view_keyset_pagination(
    integration_context: Context, clean_grants: None
) -> None:
    """limit/cursor walk the full set newest-first without overlap or loss."""
    user_id = await _seed_user(integration_context, "usr_l_page")
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)

    # Distinct (client, agent) pairs so the pair-collapse leaves all rows alive.
    expected: list[str] = []
    for i in range(3):
        agent_id = await _seed_agent(
            integration_context, owner_id=user_id, scopes=["apis:read"], name=f"page-agent-{i}"
        )
        expected.append(
            await grant_svc.create_grant(
                user_id=user_id,
                oauth_client_id=_CLIENT_ID,
                agent_id=agent_id,
                scopes=["apis:read"],
            )
        )

    admin_svc = OAuthGrantAdminService(integration_context)
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = await admin_svc.list_grants(identity=_VIEWER, limit=1, cursor=cursor)
        seen.extend(g.id for g in page.data)
        pages += 1
        if not page.has_more:
            assert page.next_cursor is None
            break
        assert page.next_cursor is not None
        cursor = page.next_cursor
    assert pages == 3
    assert seen == list(reversed(expected))  # newest first, no overlap/loss


async def test_admin_cross_view_pagination_walks_created_at_ties(
    integration_context: Context, clean_grants: None
) -> None:
    """Rows sharing a page-boundary ``created_at`` are not skipped: the cursor
    carries ``(created_at, id)`` and the repo applies the compound keyset
    (``created_at < c OR (created_at = c AND id < cid)``)."""
    user_id = await _seed_user(integration_context, "usr_l_tie")
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)

    grant_ids: list[str] = []
    for i in range(5):
        agent_id = await _seed_agent(
            integration_context, owner_id=user_id, scopes=["apis:read"], name=f"tie-agent-{i}"
        )
        grant_ids.append(
            await grant_svc.create_grant(
                user_id=user_id,
                oauth_client_id=_CLIENT_ID,
                agent_id=agent_id,
                scopes=["apis:read"],
            )
        )
    # Every row shares one timestamp, so EVERY page boundary is a tie.
    await _force_created_at(
        integration_context, grant_ids, datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    )

    admin_svc = OAuthGrantAdminService(integration_context)
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = await admin_svc.list_grants(identity=_VIEWER, limit=2, cursor=cursor)
        seen.extend(g.id for g in page.data)
        if not page.has_more:
            break
        cursor = page.next_cursor
    # All 5 rows walked exactly once (id DESC within the tied timestamp).
    assert seen == sorted(grant_ids, reverse=True)


async def test_list_grants_for_agent_pagination_walks_created_at_ties(
    integration_context: Context, clean_grants: None
) -> None:
    """The per-agent listing walks tied ``created_at`` rows without skips —
    the same compound keyset via the shared admin read service."""
    owner_id = await _seed_user(integration_context, "usr_l_tie_agent")
    agent_id = await _seed_agent(
        integration_context, owner_id=owner_id, scopes=["apis:read"], name="tie-one-agent"
    )
    grant_svc = OAuthGrantService(integration_context)

    # Distinct clients on ONE agent so the pair-collapse keeps all rows.
    grant_ids: list[str] = []
    for i in range(4):
        client_id = await _seed_client(
            integration_context,
            allowed_scopes=["apis:read"],
            client_id=f"oc_grant_listing_tie_{i}",
        )
        grant_ids.append(
            await grant_svc.create_grant(
                user_id=owner_id,
                oauth_client_id=client_id,
                agent_id=agent_id,
                scopes=["apis:read"],
            )
        )
    await _force_created_at(
        integration_context, grant_ids, datetime(2026, 8, 16, 9, 30, 0, tzinfo=UTC)
    )

    owner = Identity(sub=owner_id, email="")
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = await grant_svc.list_grants_for_agent(
            agent_id, identity=owner, limit=1, cursor=cursor
        )
        seen.extend(g.id for g in page.data)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert seen == sorted(grant_ids, reverse=True)


# --- per-item can_revoke capability (G10 list/revoke divergence) --------------


async def test_can_revoke_matrix_consenter_transfer_and_admin_arms(
    integration_context: Context, clean_grants: None
) -> None:
    """``can_revoke`` mirrors the ``:revoke`` predicate per item — including
    the two G10 divergence arms: the agent's post-transfer owner can LIST but
    not revoke, and a read-only admin can LIST but not revoke."""
    consenter_id = await _seed_user(integration_context, "usr_l_consenter")
    new_owner_id = await _seed_user(integration_context, "usr_l_new_owner")
    ro_admin_id = await _seed_user(integration_context, "usr_l_ro_admin")
    agent_id = await _seed_agent(
        integration_context, owner_id=consenter_id, scopes=["apis:read"], name="cap-agent"
    )
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    grant_svc = OAuthGrantService(integration_context)
    grant_id = await grant_svc.create_grant(
        user_id=consenter_id, oauth_client_id=_CLIENT_ID, agent_id=agent_id, scopes=["apis:read"]
    )

    # The consenting owner sees an actionable revoke.
    consenter = Identity(sub=consenter_id, email="")
    page = await grant_svc.list_grants_for_agent(agent_id, identity=consenter)
    assert [(g.id, g.can_revoke) for g in page.data] == [(grant_id, True)]

    # G10 transfer arm: hand the agent to a new owner. The new owner can list
    # (list keys on the agent's CURRENT owner) but can_revoke is False (revoke
    # keys on the grant's CONSENTING user) — and the revoke verb agrees.
    async with integration_context.admin_db.session() as session:
        await AgentRepository.update_agent(session, agent_id, owner_id=new_owner_id)
        await session.commit()

    new_owner = Identity(sub=new_owner_id, email="")
    page = await grant_svc.list_grants_for_agent(agent_id, identity=new_owner)
    assert [(g.id, g.can_revoke) for g in page.data] == [(grant_id, False)]
    with pytest.raises(OAuthGrantAccessDeniedError):
        await grant_svc.revoke_grant(grant_id, identity=new_owner)

    # Read-only-admin arm: `oauth-clients:read` unlocks the LIST but is not in
    # the revoke write set — can_revoke False, and the revoke verb agrees.
    ro_admin = Identity(sub=ro_admin_id, email="", permissions=["oauth-clients:read"])
    page = await grant_svc.list_grants_for_agent(agent_id, identity=ro_admin)
    assert [(g.id, g.can_revoke) for g in page.data] == [(grant_id, False)]
    with pytest.raises(OAuthGrantAccessDeniedError):
        await grant_svc.revoke_grant(grant_id, identity=ro_admin)

    # The write-set admins CAN revoke — capability True on both surfaces.
    for permission in ("org:admin", "oauth-clients:write"):
        admin = Identity(sub=ro_admin_id, email="", permissions=[permission])
        page = await grant_svc.list_grants_for_agent(agent_id, identity=admin)
        assert [(g.id, g.can_revoke) for g in page.data] == [(grant_id, True)]
        cross = await OAuthGrantAdminService(integration_context).list_grants(identity=admin)
        assert [(g.id, g.can_revoke) for g in cross.data] == [(grant_id, True)]

    # The consenting user still holds the revoke on the admin cross-view shape
    # too (were they ever granted the read permission to reach it).
    cross = await OAuthGrantAdminService(integration_context).list_grants(identity=consenter)
    assert [(g.id, g.can_revoke) for g in cross.data] == [(grant_id, True)]


# --- per-client active-grant counts -------------------------------------------


async def test_client_listing_carries_active_grant_count(
    integration_context: Context, clean_grants: None
) -> None:
    """The admin client list/get fold in per-client ACTIVE grant counts;
    revoked rows do not count and neighbours' grants do not bleed in."""
    user_id = await _seed_user(integration_context, "usr_l_count")
    agent_a = await _seed_agent(
        integration_context, owner_id=user_id, scopes=["apis:read"], name="count-agent-a"
    )
    agent_b = await _seed_agent(
        integration_context, owner_id=user_id, scopes=["apis:read"], name="count-agent-b"
    )
    await _seed_client(integration_context, allowed_scopes=["apis:read"])
    other_client = await _seed_client(
        integration_context, allowed_scopes=["apis:read"], client_id="oc_grant_listing_count"
    )
    grant_svc = OAuthGrantService(integration_context)

    await grant_svc.create_grant(
        user_id=user_id, oauth_client_id=_CLIENT_ID, agent_id=agent_a, scopes=["apis:read"]
    )
    revoked_id = await grant_svc.create_grant(
        user_id=user_id, oauth_client_id=_CLIENT_ID, agent_id=agent_b, scopes=["apis:read"]
    )
    await grant_svc.revoke_grant(revoked_id, identity=Identity(sub=user_id, email=""))
    # A neighbouring client's active grant must not inflate the first's count.
    await grant_svc.create_grant(
        user_id=user_id, oauth_client_id=other_client, agent_id=agent_b, scopes=["apis:read"]
    )

    client_svc = OAuthClientService(integration_context)
    views = {c.client_id: c for c in await client_svc.list_all()}
    assert views[_CLIENT_ID].active_grant_count == 1
    assert views[other_client].active_grant_count == 1

    # get() scopes its aggregate to the one requested client.
    got = await client_svc.get(views[_CLIENT_ID].id)
    assert got.active_grant_count == 1
