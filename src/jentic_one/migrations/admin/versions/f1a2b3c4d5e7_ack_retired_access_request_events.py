"""acknowledge actionable access-request events (theme 7)

Theme 7 retires the access-request workflow (the control-DB tables drop in
y6a7b8c9d0e1), but ``access_request.filed`` events were emitted with
``requires_action=True``. Left unacknowledged they would sit in the console's
action inbox forever with nothing left to act on — the rail that decided them
is gone. Mark them acknowledged (with a note naming this retirement, and no
``acknowledged_by`` — no human closed them) so the inbox drains;
the rows themselves stay readable in Monitor → Events.

Data-only and idempotent. ``downgrade()`` is a no-op: which rows were
acknowledged by this migration is recoverable from ``acknowledgement_note``,
but re-opening them would resurrect the phantom alerts.

Revision ID: f1a2b3c4d5e7
Revises: b9d0e1f2a3b4
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e7"  # pragma: allowlist secret
down_revision: str | None = "b9d0e1f2a3b4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``acknowledgement_note`` marking rows this migration closed.
ACKNOWLEDGEMENT_NOTE = "access requests were retired (theme 7)"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE events SET acknowledged = :ack, acknowledged_at = CURRENT_TIMESTAMP, "
            "acknowledgement_note = :note "
            "WHERE type LIKE 'access_request.%' AND requires_action = :ack "
            "AND acknowledged = :unack"
        ),
        {
            "ack": True,
            "unack": False,
            "note": ACKNOWLEDGEMENT_NOTE,
        },
    )


def downgrade() -> None:
    """No-op: re-opening the rows would resurrect alerts nothing can act on."""
