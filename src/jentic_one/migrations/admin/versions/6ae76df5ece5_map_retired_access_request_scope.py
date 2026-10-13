"""map the retired owner:access-requests:read onto credentials:connect (theme 7)

Theme 7 retires ``owner:access-requests:read`` and puts ``credentials:connect``
in the agent baseline (and so in the DCR scope ceiling and ``scopes_supported``)
in its place. Rows written under the earlier baseline carry the retired string
and lack its successor, which breaks two things on an upgraded install:

- a DCR client registered before the swap (Claude, Cursor, ``mcp-remote``) keeps
  its stored ``allowed_scopes``, so ``/authorize`` answers ``invalid_scope`` as
  soon as the client asks for the advertised ``credentials:connect``;
- an agent created before the swap lacks ``credentials:connect``, which the MCP
  ``request_connection`` tool and the agent connect routes require.

The retired string marks a row written from the earlier baseline, so agents
and OAuth ceilings that carry it get the baseline's successor; everything else
only loses it:

1. **Agent grants** (``actor_permission_grants``, ``actor_type='agent'``): the
   retired row is rewritten in place to ``credentials:connect`` — its id is kept,
   so no KSUID is minted here — or deleted when the agent already holds it.
2. **Ceilings** — ``oauth_clients.allowed_scopes`` (a ``VARCHAR[]`` on
   PostgreSQL, JSON on SQLite) and the consent grants'
   ``oauth_client_grants.scopes`` (JSON): the retired entry is replaced by
   ``credentials:connect`` at the same position (dropped if already present).
   A ceiling without the retired entry is never touched, so an allowlist an
   admin narrowed stays narrowed.
3. **A deliberate removal wins.** An agent or OAuth client whose audit trail
   shows a human removing ``credentials:connect`` (an audited edit whose
   ``before`` list holds it and whose ``after`` list lacks it) only loses the
   retired string; it is named in the warning as not granted.
4. **Everything else** is only swept, never widened: user grants
   (``user_permission_grants``), non-agent actor grants, token snapshots
   (``access_tokens`` / ``refresh_tokens``) and the space-separated
   ``authorization_codes.scopes``. Agent tokens resolve their scopes live from
   the grants and ceilings above, so they pick up ``credentials:connect``
   without a re-consent.

Every change is counted and reported in one ``==> WARNING`` line, so the
operator learns which agents and OAuth clients gained ``credentials:connect``
and which were withheld.
A database with nothing to map stays quiet; a re-run finds nothing and is a
no-op.

``downgrade()`` changes nothing: the retired string grants nothing, so there is
no state to restore.

Revision ID: 6ae76df5ece5
Revises: d2e3f4a5b6c7
Create Date: 2026-10-13

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6ae76df5ece5"  # pragma: allowlist secret
down_revision: str | None = "d2e3f4a5b6c7"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Copied, not imported (migrations must stay runnable against the historical
#: code state); ``tests/unit/test_migration_access_request_scope_map.py`` pins
#: them against ``shared.auth.permission_catalog``.
RETIRED = "owner:access-requests:read"
SUCCESSOR = "credentials:connect"

#: Snapshots: the retired entry is dropped, never mapped.
_SWEPT_ARRAY_COLUMNS = (
    ("access_tokens", "scopes"),
    ("refresh_tokens", "scopes"),
)

#: Columns that are a native ``VARCHAR[]`` on PostgreSQL (``aa6b7c8d9e0f``)
#: rather than JSONB. SQLite stores them as JSON like the rest.
_PG_ARRAY_COLUMNS = frozenset({("oauth_clients", "allowed_scopes")})

#: The cheap LIKE prefilter; only the retired scope contains this substring.
_PROBE = "%access-requests%"

WARNING_PREFIX = "==> WARNING (access-request scope retirement):"

_MAX_IDS_IN_MESSAGE = 20


def _parse_scopes(value: object) -> list[str] | None:
    """Coerce a raw array column value to a list (both dialects).

    PostgreSQL decodes JSONB and ``VARCHAR[]`` to a list; SQLite returns JSON as
    TEXT. ``None`` (an unrestricted ``allowed_scopes``) and unparseable values
    come back as ``None`` — nothing to change.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return None


def _parse_object(value: object) -> dict[str, object]:
    """Coerce a raw JSON-object column value (an audit ``before``/``after``) to a dict."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _mapped(scopes: list[str]) -> list[str]:
    """``scopes`` with the retired entry replaced by its successor, in place."""
    out: list[str] = []
    for scope in scopes:
        if scope == RETIRED:
            scope = SUCCESSOR
        if scope == SUCCESSOR and SUCCESSOR in out:
            continue
        out.append(scope)
    return out


def _swept(scopes: list[str]) -> list[str]:
    return [scope for scope in scopes if scope != RETIRED]


def _removed_successor(
    bind: sa.engine.Connection, target_type: str, ids: list[str], keys: tuple[str, ...]
) -> set[str]:
    """The ``ids`` whose audit trail shows a human removing the successor.

    An audited edit whose ``before`` list (under any of ``keys``) holds
    ``credentials:connect`` and whose ``after`` list lacks it is a deliberate
    revocation; mapping the retired string would hand it back.
    """
    if not ids:
        return set()
    pg = bind.dialect.name == "postgresql"
    probe = "CAST(before AS TEXT)" if pg else "before"
    stmt = sa.text(
        "SELECT target_id, before, after FROM audit_entries"
        f" WHERE target_type = :target_type AND target_id IN :ids AND {probe} LIKE :probe"
    ).bindparams(sa.bindparam("ids", expanding=True))
    removed: set[str] = set()
    rows = bind.execute(stmt, {"target_type": target_type, "ids": ids, "probe": f"%{SUCCESSOR}%"})
    for row in rows:
        before, after = _parse_object(row.before), _parse_object(row.after)
        for key in keys:
            held = _parse_scopes(before.get(key))
            kept = _parse_scopes(after.get(key)) if key in after else None
            if (
                held is not None
                and kept is not None
                and SUCCESSOR in held
                and SUCCESSOR not in kept
            ):
                removed.add(str(row.target_id))
    return removed


def _map_agent_grants(bind: sa.engine.Connection) -> tuple[list[str], list[str]]:
    """Move each agent's retired grant onto the successor.

    Returns ``(gained, withheld)``: the agents that gained the successor, and
    those whose audit trail shows it revoked, which only lose the retired row.
    The row is rewritten in place only where the agent lacks the successor: the
    unique ``(actor_id, permission)`` constraint rules it out where it holds one,
    and :func:`_sweep_scalar_grants` deletes the leftover.
    """
    candidates = [
        str(row.actor_id)
        for row in bind.execute(
            sa.text(
                "SELECT actor_id FROM actor_permission_grants"
                " WHERE actor_type = 'agent' AND permission = :retired"
                " AND NOT EXISTS (SELECT 1 FROM actor_permission_grants held"
                "  WHERE held.actor_id = actor_permission_grants.actor_id"
                "  AND held.permission = :successor)"
                " ORDER BY actor_id"
            ),
            {"retired": RETIRED, "successor": SUCCESSOR},
        )
    ]
    # Agent permission edits are audited with ``permissions`` lists (``scopes``
    # on rows that predate the permission rename).
    removed = _removed_successor(bind, "agent", candidates, ("permissions", "scopes"))
    gained = [agent for agent in candidates if agent not in removed]
    if gained:
        stmt = sa.text(
            "UPDATE actor_permission_grants SET permission = :successor"
            " WHERE actor_type = 'agent' AND permission = :retired AND actor_id IN :ids"
        ).bindparams(sa.bindparam("ids", expanding=True))
        bind.execute(stmt, {"retired": RETIRED, "successor": SUCCESSOR, "ids": gained})
    return gained, [agent for agent in candidates if agent in removed]


def _sweep_scalar_grants(bind: sa.engine.Connection) -> None:
    """Delete what is left: users' grants, non-agent actors, agents already holding it."""
    for table in ("actor_permission_grants", "user_permission_grants"):
        bind.execute(
            sa.text(f"DELETE FROM {table} WHERE permission = :retired"), {"retired": RETIRED}
        )


def _rewrite_arrays(
    bind: sa.engine.Connection,
    table: str,
    column: str,
    *,
    map_successor: bool,
    withhold: frozenset[str] = frozenset(),
) -> list[str]:
    """Rewrite one array column; return the ids of the rows that gained the successor.

    Rows in ``withhold`` are swept even when ``map_successor`` is set.
    """
    pg = bind.dialect.name == "postgresql"
    probe = f"CAST({column} AS TEXT)" if pg else column
    rows = bind.execute(
        sa.text(f"SELECT id, {column} AS scopes FROM {table} WHERE {probe} LIKE :probe"),
        {"probe": _PROBE},
    ).all()
    pg_array = pg and (table, column) in _PG_ARRAY_COLUMNS
    if pg_array:
        update = sa.text(f"UPDATE {table} SET {column} = :scopes WHERE id = :id").bindparams(
            sa.bindparam("scopes", type_=sa.ARRAY(sa.String()))
        )
    else:
        assignment = f"{column} = CAST(:scopes AS JSONB)" if pg else f"{column} = :scopes"
        update = sa.text(f"UPDATE {table} SET {assignment} WHERE id = :id")
    gained: list[str] = []
    for row in rows:
        scopes = _parse_scopes(row.scopes)
        if scopes is None or RETIRED not in scopes:
            continue
        mapping = map_successor and str(row.id) not in withhold
        new = _mapped(scopes) if mapping else _swept(scopes)
        if mapping and SUCCESSOR not in scopes:
            gained.append(str(row.id))
        bind.execute(update, {"id": row.id, "scopes": new if pg_array else json.dumps(new)})
    return gained


def _withheld_clients(bind: sa.engine.Connection) -> frozenset[str]:
    """``oauth_clients`` rows carrying the retired scope whose audit trail shows the
    successor removed from ``allowed_scopes``."""
    pg = bind.dialect.name == "postgresql"
    probe = "CAST(allowed_scopes AS TEXT)" if pg else "allowed_scopes"
    ids = [
        str(row.id)
        for row in bind.execute(
            sa.text(f"SELECT id FROM oauth_clients WHERE {probe} LIKE :probe"), {"probe": _PROBE}
        )
    ]
    return frozenset(_removed_successor(bind, "oauth_client", ids, ("allowed_scopes",)))


def _sweep_authorization_codes(bind: sa.engine.Connection) -> None:
    """``authorization_codes.scopes`` is a space-separated string, not JSON."""
    rows = bind.execute(
        sa.text("SELECT id, scopes FROM authorization_codes WHERE scopes LIKE :probe"),
        {"probe": _PROBE},
    ).all()
    update = sa.text("UPDATE authorization_codes SET scopes = :scopes WHERE id = :id")
    for row in rows:
        scopes = str(row.scopes or "").split()
        if RETIRED in scopes:
            bind.execute(update, {"id": row.id, "scopes": " ".join(_swept(scopes))})


def _client_ids(bind: sa.engine.Connection, row_ids: list[str]) -> list[str]:
    """The public ``client_id`` of each ``oauth_clients`` row, for the warning."""
    if not row_ids:
        return []
    stmt = sa.text(
        "SELECT client_id FROM oauth_clients WHERE id IN :ids ORDER BY client_id"
    ).bindparams(sa.bindparam("ids", expanding=True))
    return [str(row.client_id) for row in bind.execute(stmt, {"ids": row_ids})]


def _id_list(ids: Sequence[str]) -> str:
    if not ids:
        return "-"
    shown = ", ".join(ids[:_MAX_IDS_IN_MESSAGE])
    extra = len(ids) - _MAX_IDS_IN_MESSAGE
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def warning_line(
    agents: list[str],
    clients: list[str],
    consent_grants: int,
    withheld: Sequence[str] = (),
) -> str:
    """The operator-facing summary of what gained ``credentials:connect``."""
    line = (
        f"{WARNING_PREFIX} {RETIRED} is retired and {SUCCESSOR} takes its place in "
        f"the agent baseline. Granted {SUCCESSOR} to {len(agents)} agent(s) and added it "
        f"to the allowed scopes of {len(clients)} OAuth client(s) and "
        f"{consent_grants} consent grant(s) that still carried {RETIRED}, so MCP "
        "clients registered before the swap can authorize again and these agents can "
        "start a connect session (each still needs a human to approve it). Revoke it "
        "per agent or per client to opt out. "
        f"Agents: {_id_list(agents)}. OAuth clients: {_id_list(clients)}."
    )
    if withheld:
        line += (
            f" Not granted, because the audit log shows {SUCCESSOR} removed from them: "
            f"{_id_list(withheld)}."
        )
    return line


def upgrade() -> None:
    bind = op.get_bind()

    agents, withheld_agents = _map_agent_grants(bind)
    _sweep_scalar_grants(bind)
    withheld_client_rows = _withheld_clients(bind)
    client_rows = _rewrite_arrays(
        bind, "oauth_clients", "allowed_scopes", map_successor=True, withhold=withheld_client_rows
    )
    consent_grants = _rewrite_arrays(bind, "oauth_client_grants", "scopes", map_successor=True)
    for table, column in _SWEPT_ARRAY_COLUMNS:
        _rewrite_arrays(bind, table, column, map_successor=False)
    _sweep_authorization_codes(bind)

    clients = _client_ids(bind, client_rows)
    withheld = withheld_agents + _client_ids(bind, sorted(withheld_client_rows))
    if agents or clients or consent_grants or withheld:
        print(warning_line(agents, clients, len(consent_grants), withheld), flush=True)


def downgrade() -> None:
    """No-op: the retired string grants nothing, so there is nothing to restore."""
