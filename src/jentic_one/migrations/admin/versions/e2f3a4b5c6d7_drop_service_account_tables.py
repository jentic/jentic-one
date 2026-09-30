"""drop service_accounts + service_account_credentials (theme-8 phase 4)

The deletion cut of the service-account → agent migration. Three pieces:

1. **Gate (guard-and-raise, never skip).** Alembic would stamp a skipped
   revision and nothing would retry, so every refusal raises with the
   remediation steps. The drop proceeds only when either:

   - **Fresh install / never used:** ``service_accounts`` is empty *and*
     nothing in the admin DB still references a service account (no
     ``actor_scope_grants`` row with ``actor_type='service_account'`` or an
     ``sva_`` actor id, no ``agent_credential_bindings`` row keyed by an
     ``sva_`` id, no live SA access/refresh token); or
   - **Migrated, verified, acknowledged and swept:** the *latest*
     ``service_account_migration_acks`` row (written only by a passing
     ``jentic_one migrate-service-accounts --verify --acknowledge``) is at
     least as recent as the newest ``migrated_at`` stamp and records zero
     failures, **and** the verification is re-run here at drop time — the
     sentinel is necessary but not sufficient (rows can change after the
     acknowledgement). The re-verify refuses on any unstamped row, any
     stamped-but-unswept row (not archived, or still holding SA-keyed
     grants, bindings or a non-NULL digest), any post-stamp mutation (grant
     re-created, key rotated, binding added after the stamp), any successor
     digest drift (the ``list_digest_mismatches`` query, copied verbatim),
     and any lingering SA reference as above.

2. **Scope-data sweep.** The retired ``service-accounts:read`` /
   ``service-accounts:write`` / ``owner:service-accounts:read`` strings are
   purged from every stored grant/token surface, exactly like the theme-5
   6b sweep (``d1e2f3a4b5c6``): scalar grant rows are deleted, JSON arrays
   and the space-separated ``authorization_codes.scopes`` are rewritten
   in Python, LIKE-prefiltered so unaffected rows are never touched.

3. **Drop** ``service_account_credentials`` then ``service_accounts``.

What stays: ``service_account_migration_acks`` (upgrade evidence; the kept
``migrate-service-accounts`` CLI is a no-op once the tables are gone), the
``uq_agent_credentials_api_key_hash`` index (it guards agent keys), residual
revoked/expired SA token rows (every token resolver fails closed on
``actor_type='service_account'``), and historical ``sva_`` ids in audit,
event and control-DB columns (read paths label them, never resolve them).

``downgrade()`` recreates both tables **empty** in their final historical
shape (``j9k0l1m2n3o4`` + ``o4p5q6r7s8t9`` + ``q6r7s8t9u0v1`` nullable
``created_by`` + ``c0d1e2f3a4b5`` stamp columns). Rows come back only from a
database snapshot taken before the upgrade; the scope sweep is not reversed
(the strings granted nothing since theme-8 Phase 2).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-30

"""

import datetime as dt
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

# --- Verbatim copies of the Phase-1 verify SQL -----------------------------
# From ``control/repos/service_account_migration_repo.py``; pinned equal by
# ``tests/unit/control/test_drop_service_accounts_sql.py``.
_SUCCESSOR_DIGEST_DRIFT_FROM = (
    " FROM service_account_credentials sac"
    " JOIN service_accounts sa ON sa.id = sac.service_account_id"
    " LEFT JOIN agents a ON a.id = sa.migrated_to_actor_id"
    " LEFT JOIN agent_credentials ac ON ac.agent_id = sa.migrated_to_actor_id"
    " WHERE sac.api_key_hash IS NOT NULL"
    " AND sa.migrated_to_actor_id IS NOT NULL"
    " AND sa.migrated_to_actor_id != 'skipped'"
    " AND (ac.api_key_hash IS NULL OR ac.api_key_hash != sac.api_key_hash)"
)
_SUCCESSOR_DIGEST_DRIFT_SQL = (
    "SELECT sa.id AS service_account_id, sa.migrated_to_actor_id AS successor_agent_id"
    + _SUCCESSOR_DIGEST_DRIFT_FROM
)
_SUCCESSOR_SUPERSEDED_PREDICATE = (
    "(a.id IS NOT NULL AND (a.status = 'archived'"
    " OR (ac.rotated_at IS NOT NULL AND sa.migrated_at IS NOT NULL"
    " AND ac.rotated_at > sa.migrated_at)))"
)
#: ``ServiceAccountMigrationRepository.list_digest_mismatches``.
DIGEST_MISMATCH_SQL = (
    _SUCCESSOR_DIGEST_DRIFT_SQL + f" AND NOT {_SUCCESSOR_SUPERSEDED_PREDICATE}" + " ORDER BY sa.id"
)

#: ``count_unstamped``: rows the migration never reached.
_UNSTAMPED_SQL = "SELECT count(*) FROM service_accounts WHERE migrated_to_actor_id IS NULL"

#: ``list_sweepable`` (ungated): stamped rows the sweep has not finished.
_UNSWEPT_SQL = (
    "SELECT count(*) FROM service_accounts sa"
    " WHERE sa.migrated_to_actor_id IS NOT NULL"
    " AND (sa.status != 'archived'"
    "  OR EXISTS (SELECT 1 FROM actor_scope_grants g"
    "   WHERE g.actor_id = sa.id AND g.actor_type = 'service_account')"
    "  OR EXISTS (SELECT 1 FROM agent_credential_bindings cb WHERE cb.agent_id = sa.id)"
    "  OR EXISTS (SELECT 1 FROM service_account_credentials sac"
    "   WHERE sac.service_account_id = sa.id AND sac.api_key_hash IS NOT NULL))"
)

#: ``count_post_stamp_mutations``: the three NF-3 arms.
_POST_STAMP_SQL = (
    (
        "SELECT count(*) FROM actor_scope_grants g"
        " JOIN service_accounts sa ON sa.id = g.actor_id"
        " WHERE g.actor_type = 'service_account'"
        " AND sa.migrated_at IS NOT NULL"
        " AND g.created_at > sa.migrated_at"
    ),
    (
        "SELECT count(*) FROM service_account_credentials sac"
        " JOIN service_accounts sa ON sa.id = sac.service_account_id"
        " WHERE sa.migrated_at IS NOT NULL"
        " AND sac.rotated_at IS NOT NULL"
        " AND sac.rotated_at > sa.migrated_at"
    ),
    (
        "SELECT count(*) FROM agent_credential_bindings b"
        " JOIN service_accounts sa ON sa.id = b.agent_id"
        " WHERE sa.migrated_at IS NOT NULL"
        " AND b.created_at > sa.migrated_at"
    ),
)

# --- Lingering references (independent of the SA rows) ---------------------
_SA_GRANTS_SQL = (
    "SELECT count(*) FROM actor_scope_grants"
    " WHERE actor_type = 'service_account' OR substr(actor_id, 1, 4) = 'sva_'"
)
_SA_BINDINGS_SQL = (
    "SELECT count(*) FROM agent_credential_bindings WHERE substr(agent_id, 1, 4) = 'sva_'"
)
_LIVE_SA_TOKENS_SQL = tuple(
    f"SELECT count(*) FROM {table}"
    " WHERE actor_type = 'service_account'"
    " AND revoked_at IS NULL AND expires_at > :now"
    for table in ("access_tokens", "refresh_tokens")
)

_LATEST_ACK_SQL = (
    "SELECT acknowledged_at, unstamped_count, grant_twin_missing_count,"
    " unrevoked_token_count, digest_mismatch_count, post_stamp_mutation_count"
    " FROM service_account_migration_acks"
    " ORDER BY acknowledged_at DESC, id DESC LIMIT 1"
)
_LATEST_STAMP_SQL = "SELECT max(migrated_at) FROM service_accounts"

_RUNBOOK = (
    "On this release: run `jentic_one migrate-service-accounts` (migrates any "
    "row still unstamped), then `jentic_one migrate-service-accounts "
    "--sweep-migrated`, then `jentic_one migrate-service-accounts --verify "
    "--acknowledge`, and re-run `python -m jentic_one.migrations.run`. See "
    "'Upgrading to 0.41.0' in docs/development/releasing.md."
)


def _scalar(bind: sa.engine.Connection, sql: str, params: dict[str, object] | None = None) -> int:
    return int(bind.execute(sa.text(sql), params or {}).scalar_one() or 0)


def _as_utc(value: object) -> dt.datetime | None:
    """Normalise a raw SELECT datetime (aware on pg, TEXT on SQLite) to aware UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _lingering_references(bind: sa.engine.Connection) -> dict[str, int]:
    now_param = sa.bindparam("now", type_=sa.DateTime(timezone=True))
    live_tokens = sum(
        int(
            bind.execute(
                sa.text(sql).bindparams(now_param), {"now": dt.datetime.now(dt.UTC)}
            ).scalar_one()
            or 0
        )
        for sql in _LIVE_SA_TOKENS_SQL
    )
    return {
        "service-account grant rows": _scalar(bind, _SA_GRANTS_SQL),
        "sva_-keyed credential binding rows": _scalar(bind, _SA_BINDINGS_SQL),
        "live service-account session tokens": live_tokens,
    }


def _ack_problems(bind: sa.engine.Connection) -> list[str]:
    ack = bind.execute(sa.text(_LATEST_ACK_SQL)).first()
    if ack is None:
        return [
            "no acknowledgement row exists in service_account_migration_acks "
            "(the Phase-1 `--verify --acknowledge` has not run against this database)"
        ]
    problems: list[str] = []
    failures = (
        int(ack.unstamped_count)
        + int(ack.grant_twin_missing_count)
        + int(ack.unrevoked_token_count)
        + int(ack.digest_mismatch_count)
        + int(ack.post_stamp_mutation_count)
    )
    if failures:
        problems.append(f"the latest acknowledgement records {failures} verification failure(s)")
    acked_at = _as_utc(ack.acknowledged_at)
    latest_stamp = _as_utc(bind.execute(sa.text(_LATEST_STAMP_SQL)).scalar_one())
    if acked_at is not None and latest_stamp is not None and latest_stamp > acked_at:
        problems.append(
            "a service account was stamped after the latest acknowledgement "
            f"({latest_stamp.isoformat()} > {acked_at.isoformat()})"
        )
    return problems


def _reverify_problems(bind: sa.engine.Connection) -> list[str]:
    counts = {
        "unstamped service account(s)": _scalar(bind, _UNSTAMPED_SQL),
        "stamped but unswept service account(s)": _scalar(bind, _UNSWEPT_SQL),
        "post-stamp mutation(s)": sum(_scalar(bind, sql) for sql in _POST_STAMP_SQL),
        "successor digest mismatch(es)": len(bind.execute(sa.text(DIGEST_MISMATCH_SQL)).all()),
    }
    return [f"{n} {label}" for label, n in counts.items() if n]


def _assert_gate(bind: sa.engine.Connection) -> None:
    """Guard-and-raise: fresh install, or acknowledged + clean re-verify."""
    sa_rows = _scalar(bind, "SELECT count(*) FROM service_accounts")
    lingering = [f"{n} {label}" for label, n in _lingering_references(bind).items() if n]
    if sa_rows == 0 and not lingering:
        return
    problems = [*_ack_problems(bind), *_reverify_problems(bind), *lingering]
    if not problems:
        return
    raise RuntimeError(
        "Refusing to drop the service-account tables (theme-8 Phase 4): "
        + "; ".join(problems)
        + ". "
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
    _assert_gate(bind)

    _sweep_scalar_tables(bind)
    _sweep_json_tables(bind)
    _sweep_authorization_codes(bind)

    # Indexes go with their tables on both dialects (see d1e2f3a4b5c6 for why
    # by-name drops are fragile on SQLite batch-rebuilt tables).
    op.drop_table("service_account_credentials")
    op.drop_table("service_accounts")


def downgrade() -> None:
    """Recreate both tables empty, in their final historical shape.

    Rows come back only from a pre-upgrade database snapshot; the scope sweep
    is not reversed. The ``migrate-service-accounts`` CLI on the pre-Phase-4
    image works against the recreated (empty) tables.
    """
    pg = op.get_bind().dialect.name == "postgresql"
    op.create_table(
        "service_accounts",
        sa.Column(
            "id",
            sa.String(30),
            server_default=sa.func.generate_ksuid("sva") if pg else None,
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column(
            "owner_id",
            sa.String(30),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("registered_by", sa.String(30), nullable=False),
        sa.Column(
            "approved_by",
            sa.String(30),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("denial_reason", sa.String(1024), nullable=True),
        sa.Column("denied_by", sa.String(30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("migrated_to_actor_id", sa.String(30), nullable=True),
        sa.Column("migrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_service_accounts_owner_id", "service_accounts", ["owner_id"])
    op.create_index("ix_service_accounts_status", "service_accounts", ["status"])
    op.create_index("ix_service_accounts_created_at", "service_accounts", ["created_at"])
    op.create_index("ix_service_accounts_created_by", "service_accounts", ["created_by"])
    op.create_index(
        "ix_service_accounts_migrated_to_actor_id", "service_accounts", ["migrated_to_actor_id"]
    )

    op.create_table(
        "service_account_credentials",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column(
            "service_account_id",
            sa.String(30),
            sa.ForeignKey("service_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_secret_hash", sa.String(128), nullable=True),
        sa.Column("api_key_hash", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sa_credentials_service_account_id",
        "service_account_credentials",
        ["service_account_id"],
        unique=True,
    )
    op.create_index(
        "ix_service_account_credentials_created_at",
        "service_account_credentials",
        ["created_at"],
    )
    op.create_index(
        "ix_service_account_credentials_created_by",
        "service_account_credentials",
        ["created_by"],
    )
