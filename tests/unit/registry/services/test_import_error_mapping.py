"""Unit tests for import-source error mapping (readable failure messages)."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from jentic_one.registry.ingest.exc import DuplicateRevisionError, IngestStageError
from jentic_one.registry.ingest.fetch import IngestSource
from jentic_one.registry.services.errors import HostOwnedByOtherVendorError
from jentic_one.registry.services.import_service import _readable_source_error
from jentic_one.shared.db.errors import (
    DatabaseConsistencyError,
    DatabaseDataError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)


def test_duplicate_digest_integrity_error_maps_to_readable_message() -> None:
    """A duplicate-digest IntegrityError surfaces the readable message, not raw SQL."""
    raw = (
        "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) duplicate key value "
        'violates unique constraint "uq_api_revisions_api_id_spec_digest"'
    )
    exc = DatabaseIntegrityError(raw)

    message = _readable_source_error(exc)

    assert message == DuplicateRevisionError().message
    assert "identical content already exists" in message
    assert "uq_api_revisions" not in message
    assert "sqlalchemy" not in message


def test_one_active_index_integrity_error_maps_to_readable_message() -> None:
    """A lost race for the single-active slot surfaces the readable duplicate message.

    Two concurrent imports can both pass the (digest-keyed) duplicate guard yet
    race to become the API's one active revision; the loser trips
    ``ix_api_revisions_one_active`` (keyed on api_id, independent of spec_digest).
    That must read as a duplicate, not leak a raw SQL string. See PR #1045 (F1).
    """
    raw = (
        "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) duplicate key value "
        'violates unique constraint "ix_api_revisions_one_active"'
    )
    exc = DatabaseIntegrityError(raw)

    message = _readable_source_error(exc)

    assert message == DuplicateRevisionError().message
    assert "ix_api_revisions_one_active" not in message
    assert "sqlalchemy" not in message


def test_sqlite_duplicate_revision_integrity_error_maps_to_readable_message() -> None:
    """SQLite names the violated columns, not the constraint; both duplicate-revision
    constraints on ``api_revisions`` still map to the readable message."""
    for raw in (
        "(sqlite3.IntegrityError) UNIQUE constraint failed: api_revisions.api_id",
        "(sqlite3.IntegrityError) UNIQUE constraint failed: api_revisions.api_id, "
        "api_revisions.spec_digest",
    ):
        assert (
            _readable_source_error(DatabaseIntegrityError(raw)) == DuplicateRevisionError().message
        )


@pytest.mark.parametrize(
    "exc",
    [
        DatabaseIntegrityError('duplicate key value violates unique constraint "uq_other"'),
        DatabaseUnavailableError(
            "(asyncpg.exceptions.ConnectionDoesNotExistError) connection lost"
        ),
        DatabaseDataError("(asyncpg.exceptions.StringDataRightTruncationError) value too long"),
        DatabaseConsistencyError("(sqlalchemy.exc.MissingGreenlet) greenlet_spawn"),
        RuntimeError("internal detail: SELECT * FROM api_revisions"),
    ],
    ids=["other-integrity", "unavailable", "data", "consistency", "internal"],
)
def test_unexpected_errors_map_to_generic_message(exc: Exception) -> None:
    """Pins #1436: database/driver and internal errors never echo their raw text
    into the job record; the detail stays in the server-side log."""
    message = _readable_source_error(exc)

    assert message == "import failed unexpectedly"
    assert str(exc) not in message


def test_domain_errors_are_passed_through() -> None:
    """Deliberate ingest and registry domain errors keep their own message."""
    assert (
        _readable_source_error(IngestStageError("parsing failed at operation X"))
        == "parsing failed at operation X"
    )
    host_error = HostOwnedByOtherVendorError("rev_1", "api.example.com is served by vendor 'acme'")
    assert _readable_source_error(host_error) == str(host_error)


def test_source_validation_error_is_passed_through() -> None:
    """A malformed source payload is a caller-fixable input error; its message stays."""
    with pytest.raises(ValidationError) as exc_info:
        TypeAdapter(IngestSource).validate_python({"type": "url"})

    message = _readable_source_error(exc_info.value)

    assert message == str(exc_info.value)
    assert "url" in message
