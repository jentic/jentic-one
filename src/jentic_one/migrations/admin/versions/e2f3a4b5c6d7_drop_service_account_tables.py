"""drop service_accounts + service_account_credentials (theme-8 phase 4)

The deletion cut of the service-account → agent migration. The migration
runner (``python -m jentic_one.migrations.run``) retires the service accounts
**before** this revision: on a full upgrade it brings admin to
``d1e2f3a4b5c6``, migrates every remaining service account to a successor
agent, verifies the copy, and sweeps the SA-side originals (it must run there:
the retirement also writes the control DB, which this migration cannot
reliably reach, and it uses application code). This revision then:

1. **Locks** ``service_accounts`` and ``service_account_credentials`` (Postgres
   ``SHARE ROW EXCLUSIVE``) so nothing can change them between the check and
   the drop.
2. **Gate (guard-and-raise, never skip).** Alembic would stamp a skipped
   revision and nothing would retry, so a refusal raises, naming the rows.
   The drop proceeds only when every service account is stamped (migrated,
   or skip-stamped when it was not active) and swept — archived, with no
   SA-keyed grant or binding row and a NULL SA-side digest. A fresh install
   (no rows) passes trivially. A partial or targeted upgrade that skipped the
   runner's retirement refuses here on any unfinished row.
3. **Cleanup** of what can no longer resolve: grant rows keyed by a service
   account (``actor_type='service_account'`` or an ``sva_`` actor id with no
   SA row), ``sva_``-keyed credential bindings, and every SA access/refresh
   token row (all revoked by the sweep; nothing references them).
4. **Scope-data sweep.** The retired ``service-accounts:read`` /
   ``service-accounts:write`` / ``owner:service-accounts:read`` strings are
   purged from every stored grant/token surface, exactly like the theme-5
   6b sweep (``d1e2f3a4b5c6``): scalar grant rows are deleted, JSON arrays
   and the space-separated ``authorization_codes.scopes`` are rewritten
   in Python, LIKE-prefiltered so unaffected rows are never touched.
5. **Drop** ``service_account_migration_acks`` (the retired 0.40
   acknowledgement sentinel), ``service_account_credentials`` and
   ``service_accounts``.

What stays: the ``uq_agent_credentials_api_key_hash`` index (it guards agent
keys) and historical ``sva_`` ids in audit, event and control-DB columns (read
paths label them, never resolve them).

``downgrade()`` **raises**: the drop is irreversible. The service-account
data was migrated to agents and dropped, and recreating empty tables would
restore nothing. Rolling back means restoring the admin database (and the
control database, for the swept ``sva_`` inline rules) from the snapshot
taken before the upgrade — see "Rollback (theme-8 Phase 4)" in
``docs/development/releasing.md``.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-30

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"  # pragma: allowlist secret
down_revision: str | None = "d1e2f3a4b5c6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Scope strings retired with the service-account surface (mirrors
#: ``jentic_one.control.repos.service_account_migration_repo.THEME8_RETIRED_SCOPES``
#: — copied, not imported: migrations must stay runnable against the
#: historical code state; ``tests/unit/control/test_drop_service_accounts_sql.py``
#: pins the copies equal).
_RETIRED_SCOPES = frozenset(
    {"service-accounts:read", "service-accounts:write", "owner:service-accounts:read"}
)

#: Every retired scope contains this substring — the cheap LIKE prefilter.
_RETIRED_SCOPE_PROBE = "%service-accounts%"

_SCALAR_SCOPE_TABLES = (
    ("actor_scope_grants", "scope"),
    ("user_permission_grants", "permission"),
)

_JSON_SCOPE_TABLES = (
    ("access_tokens", "scopes"),
    ("refresh_tokens", "scopes"),
    ("oauth_client_grants", "scopes"),
    ("oauth_clients", "allowed_scopes"),
)

_LOCK_SQL = "LOCK TABLE service_accounts, service_account_credentials IN SHARE ROW EXCLUSIVE MODE"

#: Rows the retirement never stamped.
_UNSTAMPED_SQL = "SELECT id FROM service_accounts WHERE migrated_to_actor_id IS NULL ORDER BY id"

#: ``ServiceAccountMigrationRepository.list_sweepable`` — verbatim copy of
#: ``SWEEPABLE_SQL``, pinned equal by
#: ``tests/unit/control/test_drop_service_accounts_sql.py``.
SWEEPABLE_SQL = (
    "SELECT sa.id, sa.migrated_to_actor_id, sa.migrated_at, sa.status"
    " FROM service_accounts sa"
    " WHERE sa.migrated_to_actor_id IS NOT NULL"
    " AND (sa.status != 'archived'"
    "  OR EXISTS (SELECT 1 FROM actor_scope_grants g"
    "   WHERE g.actor_id = sa.id AND g.actor_type = 'service_account')"
    "  OR EXISTS (SELECT 1 FROM agent_credential_bindings cb WHERE cb.agent_id = sa.id)"
    "  OR EXISTS (SELECT 1 FROM service_account_credentials sac"
    "   WHERE sac.service_account_id = sa.id AND sac.api_key_hash IS NOT NULL))"
    " ORDER BY sa.id"
)

#: The runner's retirement is the only fix; a hand-run partial upgrade skips it.
_RUNBOOK = (
    "Run the full upgrade — `python -m jentic_one.migrations.run` with no --db or "
    "--target — which migrates, verifies and sweeps the remaining service accounts "
    "before this revision; if its verification refuses, it names the rows to fix. "
    "See 'Upgrading to 0.41.0' in docs/development/releasing.md."
)

#: Cleanup (after the gate): references nothing can resolve once the tables go.
_CLEANUP_SQL = (
    "DELETE FROM actor_scope_grants"
    " WHERE actor_type = 'service_account' OR substr(actor_id, 1, 4) = 'sva_'",
    "DELETE FROM agent_credential_bindings WHERE substr(agent_id, 1, 4) = 'sva_'",
    "DELETE FROM access_tokens WHERE actor_type = 'service_account'",
    "DELETE FROM refresh_tokens WHERE actor_type = 'service_account'",
)

_MAX_IDS_IN_MESSAGE = 20

_IRREVERSIBLE = (
    "e2f3a4b5c6d7 is irreversible: the service-account data was migrated to agents "
    "and dropped; restore the admin database from the pre-upgrade snapshot (and the "
    "control database, for the swept sva_ inline rules). Nothing was changed. See "
    "'Rollback (theme-8 Phase 4)' in docs/development/releasing.md."
)


def _ids(bind: sa.engine.Connection, sql: str) -> list[str]:
    return [str(row[0]) for row in bind.execute(sa.text(sql)).all()]


def _describe(label: str, ids: list[str]) -> str:
    shown = ", ".join(ids[:_MAX_IDS_IN_MESSAGE])
    more = f" (+{len(ids) - _MAX_IDS_IN_MESSAGE} more)" if len(ids) > _MAX_IDS_IN_MESSAGE else ""
    return f"{len(ids)} {label}: {shown}{more}"


def _assert_gate(bind: sa.engine.Connection) -> None:
    """Guard-and-raise: every service account is stamped and swept."""
    problems = []
    unstamped = _ids(bind, _UNSTAMPED_SQL)
    if unstamped:
        problems.append(_describe("unmigrated service account(s)", unstamped))
    unswept = _ids(bind, SWEEPABLE_SQL)
    if unswept:
        problems.append(_describe("migrated but unswept service account(s)", unswept))
    if not problems:
        return
    raise RuntimeError(
        "Refusing to drop the service-account tables (theme-8 Phase 4): "
        + "; ".join(problems)
        + ". Nothing was dropped. "
        + _RUNBOOK
    )


def _parse_scopes(value: object) -> list[str] | None:
    """Coerce a raw JSON-array column value to a list (both dialects)."""
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


def _sweep_scalar_tables(bind: sa.engine.Connection) -> None:
    for table, column in _SCALAR_SCOPE_TABLES:
        stmt = sa.text(f"DELETE FROM {table} WHERE {column} IN :retired").bindparams(
            sa.bindparam("retired", expanding=True)
        )
        bind.execute(stmt, {"retired": sorted(_RETIRED_SCOPES)})


def _sweep_json_tables(bind: sa.engine.Connection) -> None:
    pg = bind.dialect.name == "postgresql"
    for table, column in _JSON_SCOPE_TABLES:
        probe = f"CAST({column} AS TEXT)" if pg else column
        rows = bind.execute(
            sa.text(f"SELECT id, {column} AS scopes FROM {table} WHERE {probe} LIKE :probe"),
            {"probe": _RETIRED_SCOPE_PROBE},
        ).all()
        assignment = f"{column} = CAST(:scopes AS JSONB)" if pg else f"{column} = :scopes"
        update = sa.text(f"UPDATE {table} SET {assignment} WHERE id = :id")
        for row in rows:
            scopes = _parse_scopes(row.scopes)
            if scopes is None:
                continue
            kept = [scope for scope in scopes if scope not in _RETIRED_SCOPES]
            if kept == scopes:
                continue
            bind.execute(update, {"id": row.id, "scopes": json.dumps(kept)})


def _sweep_authorization_codes(bind: sa.engine.Connection) -> None:
    """``authorization_codes.scopes`` is a space-separated string, not JSON."""
    rows = bind.execute(
        sa.text("SELECT id, scopes FROM authorization_codes WHERE scopes LIKE :probe"),
        {"probe": _RETIRED_SCOPE_PROBE},
    ).all()
    update = sa.text("UPDATE authorization_codes SET scopes = :scopes WHERE id = :id")
    for row in rows:
        scopes = str(row.scopes or "").split()
        kept = [scope for scope in scopes if scope not in _RETIRED_SCOPES]
        if kept == scopes:
            continue
        bind.execute(update, {"id": row.id, "scopes": " ".join(kept)})


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Held to the end of this revision's transaction: no writer can slip
        # an SA row, digest or rotation in between the gate and the drop.
        bind.execute(sa.text(_LOCK_SQL))
    _assert_gate(bind)

    for sql in _CLEANUP_SQL:
        bind.execute(sa.text(sql))
    _sweep_scalar_tables(bind)
    _sweep_json_tables(bind)
    _sweep_authorization_codes(bind)

    # Indexes go with their tables on both dialects (see d1e2f3a4b5c6 for why
    # by-name drops are fragile on SQLite batch-rebuilt tables).
    op.drop_table("service_account_migration_acks")
    op.drop_table("service_account_credentials")
    op.drop_table("service_accounts")


def downgrade() -> None:
    """Irreversible: refuse, never recreate empty service-account tables.

    The upgrade migrated every service account to a successor agent and then
    dropped the data; empty tables would restore nothing and only make the
    rollback look complete. The only way back is the admin-database snapshot
    taken before the upgrade. Raised before any DDL, so a refused downgrade
    leaves the schema exactly at this revision.
    """
    raise RuntimeError(_IRREVERSIBLE)
