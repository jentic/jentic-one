"""Unit tests for the theme-8 Phase 1 migration job's pure disposition logic.

T-1: the OQ-1 switch — ``active`` → migrate, ``disabled`` → migrate-disabled,
``pending``/``rejected``/``archived`` → skip-but-stamp; the JSONL labels; and
``had_client_secret`` set iff ``client_secret_hash`` is non-NULL. Pure logic
only — the copy/revoke/stamp transaction is integration-tested against real
databases (no DB mocking).
"""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import MagicMock

import pytest

from jentic_one.control.services.service_account_migration import (
    ServiceAccountMigrationService,
    _PreviewCounts,
)
from jentic_one.shared.models import ActorStatus

_Row = namedtuple(
    "_Row",
    [
        "id",
        "name",
        "description",
        "owner_id",
        "status",
        "migrated_to_actor_id",
        "migrated_at",
        "api_key_hash",
        "client_secret_hash",
    ],
)


def _row(
    status: str = "active",
    *,
    migrated_to_actor_id: str | None = None,
    client_secret_hash: str | None = None,
) -> _Row:
    return _Row(
        id="sva_t1",
        name="t1",
        description=None,
        owner_id="usr_t1",
        status=status,
        migrated_to_actor_id=migrated_to_actor_id,
        migrated_at=None,
        api_key_hash="digest",  # pragma: allowlist secret
        client_secret_hash=client_secret_hash,
    )


@pytest.mark.parametrize(
    ("status", "label", "successor_status"),
    [
        ("active", "migrated", ActorStatus.ACTIVE.value),
        ("disabled", "migrated-disabled", ActorStatus.DISABLED.value),
        ("pending", "skipped-non-active", None),
        ("rejected", "skipped-non-active", None),
        ("archived", "skipped-non-active", None),
    ],
)
def test_disposition_switch(status: str, label: str, successor_status: str | None) -> None:
    """The OQ-1 table, verbatim: only active/disabled get a successor."""
    assert ServiceAccountMigrationService._disposition(status) == (label, successor_status)


def test_preview_labels_and_skip_reason() -> None:
    svc = ServiceAccountMigrationService(MagicMock())

    active = svc._preview(_row("active"), _PreviewCounts(stored_permissions=3))
    assert active.outcome == "migrated"
    assert active.reason is None
    assert active.owner_visibility_note is not None  # OQ-5 report line
    assert active.stored_permission_count == 3  # the computed preview counts are carried

    skipped = svc._preview(_row("pending"), _PreviewCounts(access_tokens=1))
    assert skipped.outcome == "skipped-non-active"
    assert skipped.reason == "status=pending"
    assert skipped.owner_visibility_note is None  # no successor, nothing to see
    assert skipped.access_tokens_revoked == 1


def test_preview_without_counts_reports_not_computed() -> None:
    """No counts → ``None`` (not computed), never a misleading zero."""
    svc = ServiceAccountMigrationService(MagicMock())
    outcome = svc._preview(_row("active"), None)
    assert outcome.stored_permission_count is None
    assert outcome.access_tokens_revoked is None


def test_preview_short_circuits_on_stamp() -> None:
    """A stamped row is done — successor surfaced, skip sentinel maps to None."""
    svc = ServiceAccountMigrationService(MagicMock())

    migrated = svc._preview(_row("active", migrated_to_actor_id="agnt_successor"), None)
    assert migrated.outcome == "already_migrated"
    assert migrated.successor_agent_id == "agnt_successor"
    assert migrated.stored_permission_count is None  # stamped: counts not computed
    assert migrated.permission_rule_count is None

    skipped = svc._preview(_row("pending", migrated_to_actor_id="skipped"), None)
    assert skipped.outcome == "already_migrated"
    assert skipped.successor_agent_id is None


@pytest.mark.parametrize(
    ("client_secret_hash", "expected"),
    [("secret-digest", True), (None, False)],
)
def test_had_client_secret_iff_hash_present(client_secret_hash: str | None, expected: bool) -> None:
    """OQ-1: the report names every client-credentials holder before Phase 2."""
    svc = ServiceAccountMigrationService(MagicMock())
    outcome = svc._preview(_row("active", client_secret_hash=client_secret_hash), None)
    assert outcome.had_client_secret is expected
