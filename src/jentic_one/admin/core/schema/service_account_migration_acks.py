"""ServiceAccountMigrationAck ORM model — the theme-8 Phase-1 acknowledgement sentinel.

Theme-8 Phase 4's drop migrations destroy the service-account tables
(``service_accounts``, ``service_account_credentials``) and the resolver's SA
fallback arm. That is only safe once an operator has run the Phase-1
service-account → agent migration against the *production* data, its
verification queries passed (zero unstamped rows, grant-twin parity, zero
unrevoked live SA tokens, digest parity, no post-stamp mutation), and the
operator explicitly acknowledged the result. This table records exactly that:
one row per acknowledged verification run, written **only** by ``jentic_one
migrate-service-accounts --verify --acknowledge`` and only when the
verification passed in that same invocation (the ``toolkit_flattening_acks``
precedent — ``control/core/schema/toolkit_flattening_acks.py``).

Phase 4's drop migrations must check this table directly and **raise** — not
skip — when it is empty (guard-and-raise). It lives in the **admin** DB (plan
M-B) so the drop migration reads it in the same database it drops from — no
theme-5-style cross-DB proxy. The sentinel is necessary but not sufficient:
Phase 4 re-runs the verification at drop time (F5 x M-C), so the row carries
every count the re-verify needs to compare against.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class ServiceAccountMigrationAck(AuditableMixin, AdminBase):
    """One acknowledged Phase-1 verification run (see module docstring)."""

    __tablename__ = "service_account_migration_acks"

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("smak"),
        server_default=func.generate_ksuid("smak"),
    )
    #: When the operator acknowledged (the --acknowledge invocation's clock).
    acknowledged_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    #: count(*) of service_accounts with migrated_to_actor_id IS NULL — must be 0.
    unstamped_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: (sva_, 'service_account') grant rows (non-retired scopes) missing their
    #: (agnt_, 'agent') twin on the stamped successor — must be 0.
    grant_twin_missing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Unexpired SA access/refresh token rows with revoked_at IS NULL — must be 0.
    unrevoked_token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Fully-migrated SAs whose successor's agent_credentials.api_key_hash no
    #: longer equals the (still-live, pre-sweep) SA-side digest — must be 0.
    digest_mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: SA-keyed grant rows created — or credential rows rotated — after the
    #: row's migrated_at stamp (NF-3 scope) — must be 0.
    post_stamp_mutation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Number of report findings the verification emitted (informational).
    report_finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: jentic-one package version that ran the verification.
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
