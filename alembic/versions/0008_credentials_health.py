"""Credential health Tier-1 schema additions.

Adds the columns + table that unblock the credentials revamp UI:

  - credentials.last_used_at   REAL  — written best-effort by the broker
                                       after a successful upstream call
                                       (status < 400). Powers "Used 2h ago".
  - credentials.description    TEXT  — optional free-text notes on what this
                                       credential is for. Surfaced in the
                                       form and (truncated) on the row.
  - credentials.healthy        INT   — tri-state health for manual creds
                                       (NULL / 0 / 1), mirroring the
                                       oauth_broker_accounts.healthy signal
                                       that Pipedream creds already carry.
                                       Written by the broker on 401/403 vs
                                       <400, and by POST /credentials/{id}/test.
                                       Lets a manual credential's StatusDot go
                                       red, not just grey/green.
  - credentials.health_checked_at REAL — unix ts of the last health write
                                       (broker observation or explicit test),
                                       so the UI can say "checked 5m ago".
  - idx_credentials_api_id           — single-column index for the
                                       ?api_id=… filter on GET /credentials
                                       and for the new
                                       /credentials/{id}/bindings join.
  - audit_events table               — backed by the existing
                                       jentic.audit logger callsites; lets
                                       the UI render an actual audit panel
                                       (was log-only before this).

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-28
"""

from alembic import op
from sqlalchemy import text


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    cred_cols = {row[1] for row in bind.execute(text("PRAGMA table_info(credentials)"))}
    if "last_used_at" not in cred_cols:
        op.execute("ALTER TABLE credentials ADD COLUMN last_used_at REAL")
    if "description" not in cred_cols:
        op.execute("ALTER TABLE credentials ADD COLUMN description TEXT")
    if "healthy" not in cred_cols:
        op.execute("ALTER TABLE credentials ADD COLUMN healthy INTEGER")
    if "health_checked_at" not in cred_cols:
        op.execute("ALTER TABLE credentials ADD COLUMN health_checked_at REAL")

    op.execute("CREATE INDEX IF NOT EXISTS idx_credentials_api_id ON credentials(api_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id            TEXT PRIMARY KEY,
            ts            REAL NOT NULL DEFAULT (unixepoch()),
            actor_kind    TEXT NOT NULL,
            actor_id      TEXT,
            ip            TEXT,
            event         TEXT NOT NULL,
            target_kind   TEXT,
            target_id     TEXT,
            payload_json  TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_target "
        "ON audit_events(target_kind, target_id, ts DESC)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(ts DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_events_ts")
    op.execute("DROP INDEX IF EXISTS idx_audit_events_target")
    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP INDEX IF EXISTS idx_credentials_api_id")
    # Drop the new columns. Requires SQLite >= 3.35 (released March 2021),
    # which the project already targets — earlier migrations rely on the
    # same baseline (e.g. 0006 uses ADD COLUMN without a table-rebuild).
    bind = op.get_bind()
    cred_cols = {row[1] for row in bind.execute(text("PRAGMA table_info(credentials)"))}
    if "health_checked_at" in cred_cols:
        op.execute("ALTER TABLE credentials DROP COLUMN health_checked_at")
    if "healthy" in cred_cols:
        op.execute("ALTER TABLE credentials DROP COLUMN healthy")
    if "description" in cred_cols:
        op.execute("ALTER TABLE credentials DROP COLUMN description")
    if "last_used_at" in cred_cols:
        op.execute("ALTER TABLE credentials DROP COLUMN last_used_at")
