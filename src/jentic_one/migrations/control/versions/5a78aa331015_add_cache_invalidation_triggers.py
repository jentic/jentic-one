"""add cache-invalidation NOTIFY triggers to the control rule and credential tables

Creates ``jentic_one_notify_invalidation_control()`` in the control schema and an
``AFTER INSERT OR UPDATE OR DELETE`` row trigger (plus an ``AFTER TRUNCATE``
statement trigger) on every control table the broker caches authorization state
from. Each trigger sends ``pg_notify('jentic_one_invalidation', payload)``;
``NOTIFY`` is transactional, so a listener hears about a change only once it
commits, and never about a rolled-back one.

Payload (JSON, version 1)::

    {"v": 1, "s": <schema>, "t": <table>, "op": <TG_OP>,
     "k": {<key column>: [<distinct non-null old/new values>], ...}}

A payload over 7900 bytes (the ``NOTIFY`` limit is 8000) is replaced by
``{"v": 1, "s", "t", "op", "flush": true}`` — the listener then drops
everything cached from that table. ``TRUNCATE`` always sends the flush form.
An ``UPDATE`` that leaves the row unchanged — or changes only the
``updated_at``/``last_used_at`` bookkeeping timestamps — sends nothing.

The key columns per table are the ones the broker's caches can be keyed on
(binding pairs, rule-set ids, credential ids). The function body matches
the admin tree's ``jentic_one_notify_invalidation()``; it carries its own name
so each tree can be downgraded independently even when both share a schema.
PostgreSQL only: SQLite has no notification channel, and its caches rely on
their TTLs.

Revision ID: 5a78aa331015
Revises: dd4e5f6a7b8c
Create Date: 2026-10-17

"""

from collections.abc import Sequence

from alembic import op

revision: str = "5a78aa331015"  # pragma: allowlist secret
down_revision: str | None = "dd4e5f6a7b8c"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Must equal ``jentic_one.shared.invalidation.PG_INVALIDATION_CHANNEL``
#: (pinned by a unit test; kept literal so this revision never changes).
CHANNEL = "jentic_one_invalidation"
FUNCTION = "jentic_one_notify_invalidation_control"

#: table -> key columns reported in the payload.
TRIGGERED_TABLES: dict[str, tuple[str, ...]] = {
    "agent_permission_rules": ("agent_id", "credential_id"),
    "permission_rule_set_rules": ("rule_set_id",),
    "credentials": ("id",),
}

FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    old_row jsonb;
    new_row jsonb;
    keys jsonb := '{{}}'::jsonb;
    vals jsonb;
    col text;
    payload text;
BEGIN
    IF TG_LEVEL = 'STATEMENT' THEN
        payload := jsonb_build_object(
            'v', 1, 's', TG_TABLE_SCHEMA, 't', TG_TABLE_NAME, 'op', TG_OP, 'flush', true
        )::text;
        PERFORM pg_notify('{CHANNEL}', payload);
        RETURN NULL;
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        old_row := to_jsonb(OLD);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        new_row := to_jsonb(NEW);
    END IF;
    -- Bookkeeping timestamps never change what a cache holds: an UPDATE that
    -- touches only those (a grant's last_used_at on every token mint) is silent.
    IF TG_OP = 'UPDATE'
       AND old_row - ARRAY['updated_at', 'last_used_at']
           = new_row - ARRAY['updated_at', 'last_used_at'] THEN
        RETURN NULL;
    END IF;
    FOREACH col IN ARRAY TG_ARGV LOOP
        vals := '[]'::jsonb;
        IF old_row ->> col IS NOT NULL THEN
            vals := vals || jsonb_build_array(old_row ->> col);
        END IF;
        IF new_row ->> col IS NOT NULL
           AND new_row ->> col IS DISTINCT FROM old_row ->> col THEN
            vals := vals || jsonb_build_array(new_row ->> col);
        END IF;
        keys := keys || jsonb_build_object(col, vals);
    END LOOP;
    payload := jsonb_build_object(
        'v', 1, 's', TG_TABLE_SCHEMA, 't', TG_TABLE_NAME, 'op', TG_OP, 'k', keys
    )::text;
    IF octet_length(payload) > 7900 THEN
        payload := jsonb_build_object(
            'v', 1, 's', TG_TABLE_SCHEMA, 't', TG_TABLE_NAME, 'op', TG_OP, 'flush', true
        )::text;
    END IF;
    PERFORM pg_notify('{CHANNEL}', payload);
    RETURN NULL;
END;
$fn$
"""


def _row_trigger(table: str) -> str:
    return f"{table}_invalidation_row"


def _truncate_trigger(table: str) -> str:
    return f"{table}_invalidation_truncate"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(FUNCTION_SQL)
    for table, keys in TRIGGERED_TABLES.items():
        args = ", ".join(f"'{col}'" for col in keys)
        op.execute(
            f"CREATE TRIGGER {_row_trigger(table)} AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION {FUNCTION}({args})"
        )
        op.execute(
            f"CREATE TRIGGER {_truncate_trigger(table)} AFTER TRUNCATE "
            f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TRIGGERED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_truncate_trigger(table)} ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {_row_trigger(table)} ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
