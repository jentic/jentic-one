"""Unit tests for import-source error mapping (readable failure messages)."""

from __future__ import annotations

from jentic_one.registry.ingest.exc import DuplicateRevisionError, IngestStageError
from jentic_one.registry.services.import_service import _readable_source_error
from jentic_one.shared.db.errors import DatabaseIntegrityError


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


def test_unrelated_integrity_error_keeps_its_text() -> None:
    """An integrity error on a different constraint is passed through unchanged."""
    exc = DatabaseIntegrityError("violates constraint uq_something_else")

    assert _readable_source_error(exc) == "violates constraint uq_something_else"


def test_other_exception_is_passed_through() -> None:
    """Non-integrity failures keep their own message."""
    exc = IngestStageError("parsing failed at operation X")

    assert _readable_source_error(exc) == "parsing failed at operation X"
