"""access-request governance collapse (theme 5, phase 3)

Two coupled changes to ``access_request_items``:

1. **``rule_set_id`` column.** ``credential:bind`` now requires a policy —
   inline ``rules`` or a pointer at a shared ``permission_rule_sets`` row
   (hard problem 6: a rules-less agent↔credential bind is a live default-deny
   the operator believes granted). The pointer is FK-less by convention with
   the admin binding's own ``rule_set_id`` (the set is validated at decide
   time).

2. **Auto-withdraw pending toolkit-addressed items.** The toolkit vocabulary
   (``toolkit:create`` / ``toolkit:bind``) is retired and the web schema
   rejects it with a hard 422; ``credential:bind`` re-means agent↔credential,
   so a pending pre-Phase-3 ``credential:bind`` (which targeted a toolkit via
   ``to_id``) is equally unfulfillable as filed. Approving any of these on a
   Phase-3 server would either hard-fail or grant something the filer never
   asked for. They are withdrawn here with a re-file directive as the
   ``decision_reason`` so the agent's ``--wait``/status polls close with a
   legible outcome instead of stranding forever-pending rows. Envelope
   statuses are recomputed: all-items-withdrawn requests become ``withdrawn``;
   partially decided ones settle to the aggregate their remaining items imply.
   Historical (already decided) rows are untouched — they render read-only.

The withdrawal is intentionally irreversible: ``downgrade()`` keeps the rows
withdrawn (re-pending them under a downgraded server would resurrect items the
Phase-3 schema can't decide) and only drops the column.

Revision ID: s0a1b2c3d4e5
Revises: r9f0a1b2c3d4
Create Date: 2026-09-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s0a1b2c3d4e5"
down_revision: str | None = "r9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Named actor for rows this migration decides — greppable in audit trails.
_MIGRATION_ACTOR = "system:theme5-phase3"

_REFILE_DIRECTIVE = (
    "Withdrawn by the toolkit-retirement migration (theme 5 phase 3): "
    "toolkit-addressed access requests can no longer be decided. Re-file the "
    "request as resource_type='credential', action='bind' naming the API by "
    "resource_reference ({vendor, name[, version]}) or a credential id in "
    "resource_id, with 'rules' or 'rule_set_id' carrying the proposed policy."
)


def upgrade() -> None:
    op.add_column(
        "access_request_items",
        sa.Column("rule_set_id", sa.String(30), nullable=True),
    )

    bind = op.get_bind()

    # Withdraw every still-pending toolkit-addressed item: any toolkit-verb
    # item, plus toolkit-era credential:bind items that carried a to_id/to_type
    # bind target (the new credential:bind has no assignment target — the
    # agent axis is the item's own actor_id).
    bind.execute(
        sa.text(
            "UPDATE access_request_items "
            "SET status = 'withdrawn', "
            "    decided_by = :actor, "
            "    decided_at = CURRENT_TIMESTAMP, "
            "    decision_reason = :reason "
            "WHERE status = 'pending' "
            "  AND (resource_type = 'toolkit' "
            "       OR to_type IS NOT NULL "
            "       OR to_id IS NOT NULL)"
        ),
        {"actor": _MIGRATION_ACTOR, "reason": _REFILE_DIRECTIVE},
    )

    # Recompute envelope statuses for pending requests whose items no longer
    # contain a pending row. Mirrors
    # ``access_request_repo.compute_aggregate_status`` with one extra terminal:
    # all-withdrawn -> withdrawn (that function never needs it at decide time,
    # but a fully-withdrawn envelope must not read as 'denied').
    bind.execute(
        sa.text(
            "UPDATE access_requests SET status = ("
            "  SELECT CASE "
            "    WHEN COUNT(*) FILTER (WHERE i.status = 'pending') > 0 THEN 'pending' "
            "    WHEN COUNT(*) FILTER (WHERE i.status != 'withdrawn') = 0 THEN 'withdrawn' "
            "    WHEN COUNT(*) FILTER (WHERE i.status = 'approved') = COUNT(*) THEN 'approved' "
            "    WHEN COUNT(*) FILTER (WHERE i.status = 'approved') = 0 THEN 'denied' "
            "    ELSE 'partially_approved' "
            "  END "
            "  FROM access_request_items i "
            "  WHERE i.access_request_id = access_requests.id"
            ") "
            "WHERE status = 'pending' "
            "  AND id IN ("
            "    SELECT DISTINCT access_request_id FROM access_request_items "
            "    WHERE decided_by = :actor"
            "  )"
        ),
        {"actor": _MIGRATION_ACTOR},
    )


def downgrade() -> None:
    # The withdrawal is not reversed (see module docstring); only the schema
    # change rolls back.
    op.drop_column("access_request_items", "rule_set_id")
