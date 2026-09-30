"""hash existing connect_sessions.poll_token values

Connect-session poll tokens are now stored as their SHA-256 hex digest rather
than in plaintext; verification hashes the presented token and compares it to
the stored digest. This data migration rewrites the values already in the
table in place so in-flight sessions keep working across the upgrade: the
digest is deterministic, so a client still holding its plaintext token
verifies against the rewritten row exactly as before.

No DDL: the column keeps its name (the ORM maps it to the ``poll_token_hash``
attribute). Migrations run while the previous release is still serving
(e.g. the Helm pre-upgrade hook), and a rename would make every
connect-session query on those pods fail until they are replaced. With the
name unchanged the previous release keeps loading rows; for the length of the
rollout it can no longer verify poll tokens of sessions that already hold a
digest, and a session it creates in that window stores a plaintext token the
new release will not accept. Both outcomes are an ordinary poll-token
mismatch on a short-lived session, which the caller recovers from by starting
a new one.

Idempotent: a value that is already a 64-character lowercase hex digest is
left alone. Issued tokens are ``secrets.token_urlsafe(32)`` (43 characters),
so they can never be mistaken for a digest.

Downgrade is a deliberate no-op: a digest cannot be turned back into its
plaintext. After a downgrade, sessions created while this revision was
applied can no longer be polled (the old code compares plaintext) and are
retired by the regular connect-session TTL sweep, which works on ids and
state, not tokens. Sessions are short-lived, so the effect is bounded.

Revision ID: e1a2b3c4d5f6
Revises: z7b8c9d0e1f2
"""

import hashlib
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2b3c4d5f6"  # pragma: allowlist secret
down_revision: str | None = "z7b8c9d0e1f2"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# Frozen copy of the table shape this revision operates on; migrations must
# not import the live ORM model.
_connect_sessions = sa.table(
    "connect_sessions",
    sa.column("id", sa.String(30)),
    sa.column("poll_token", sa.String(64)),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(_connect_sessions.c.id, _connect_sessions.c.poll_token)).all()
    for row_id, token in rows:
        if token is None or _DIGEST_RE.match(token):
            continue
        bind.execute(
            sa.update(_connect_sessions)
            .where(_connect_sessions.c.id == row_id)
            .values(poll_token=hashlib.sha256(token.encode()).hexdigest())
        )


def downgrade() -> None:
    # Irreversible by design — see module docstring.
    pass
