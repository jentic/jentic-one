"""Unit tests for import-source error mapping (readable failure messages)."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from jentic_one.registry.ingest.errors import (
    CatalogIdentityConflictError,
    DuplicateRevisionError,
    HostOwnedByOtherVendorIngestError,
    IngestPipelineError,
    IngestStageError,
    RelativeServerUrlError,
    ServerUrlProblem,
)
from jentic_one.registry.ingest.fetch import IngestSource
from jentic_one.registry.services.errors import HostOwnedByOtherVendorError
from jentic_one.registry.services.import_service import (
    _is_deterministic_source_failure,
    _readable_source_error,
    _SourcePhase,
)
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


def _chained[E: BaseException](outer: E, cause: BaseException) -> E:
    """``outer`` raised ``from cause``, as a stage or the pipeline raises it."""
    outer.__cause__ = cause
    return outer


def _through_pipeline(stage_error: IngestStageError) -> IngestPipelineError:
    """The ``IngestPipelineError`` the pipeline raises over a failed stage."""
    return _chained(IngestPipelineError(stage_error.message), stage_error)


def _source_validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        TypeAdapter(IngestSource).validate_python({"type": "url"})
    return exc_info.value


_DUPLICATE_INTEGRITY = DatabaseIntegrityError(
    'duplicate key value violates unique constraint "uq_api_revisions_api_id_spec_digest"'
)
_IDENTITY_CONFLICT = CatalogIdentityConflictError(
    vendor="v", name="n", version="1", stored_id="a", incoming_id="b"
)


@pytest.mark.parametrize(
    ("exc", "phase"),
    [
        pytest.param(_source_validation_error(), _SourcePhase.VALIDATE, id="source-payload"),
        pytest.param(
            IngestStageError("cannot resolve api_identifier: missing vendor"),
            _SourcePhase.LOAD_INLINE,
            id="inline-identity",
        ),
        pytest.param(
            _chained(IngestStageError("failed to parse spec content"), ValueError("bad")),
            _SourcePhase.LOAD_INLINE,
            id="inline-parse",
        ),
        pytest.param(
            _through_pipeline(IngestStageError("Missing 'openapi' key in specification content")),
            _SourcePhase.INGEST,
            id="document-validation",
        ),
        pytest.param(
            _through_pipeline(DuplicateRevisionError()), _SourcePhase.INGEST, id="duplicate"
        ),
        pytest.param(
            _through_pipeline(_chained(DuplicateRevisionError(), _DUPLICATE_INTEGRITY)),
            _SourcePhase.INGEST,
            id="duplicate-race-in-stage",
        ),
        pytest.param(_DUPLICATE_INTEGRITY, _SourcePhase.INGEST, id="duplicate-race-at-commit"),
        pytest.param(
            _through_pipeline(_chained(_IDENTITY_CONFLICT, RuntimeError("stored id"))),
            _SourcePhase.INGEST,
            id="catalog-identity-conflict",
        ),
        pytest.param(
            _through_pipeline(HostOwnedByOtherVendorIngestError("api.example.com")),
            _SourcePhase.INGEST,
            id="host-owned-by-other-vendor",
        ),
        pytest.param(
            _through_pipeline(RelativeServerUrlError(["/v1"], ServerUrlProblem.NO_BASE)),
            _SourcePhase.INGEST,
            id="relative-server-url",
        ),
    ],
)
def test_deterministic_source_failures(exc: Exception, phase: _SourcePhase) -> None:
    """Pins #1980: failures a retry cannot change fail the import job at once."""
    assert _is_deterministic_source_failure(exc, phase=phase) is True


@pytest.mark.parametrize(
    ("exc", "phase"),
    [
        pytest.param(
            _chained(IngestStageError("failed to fetch URL: refused"), OSError("refused")),
            _SourcePhase.LOAD_URL,
            id="url-network",
        ),
        pytest.param(
            IngestStageError("non-success status 503 fetching https://x"),
            _SourcePhase.LOAD_URL,
            id="url-5xx",
        ),
        pytest.param(RuntimeError("bug"), _SourcePhase.LOAD_INLINE, id="inline-unexpected"),
        pytest.param(
            _through_pipeline(
                _chained(
                    IngestStageError("Stage 'ResolveApiStage' failed unexpectedly"),
                    DatabaseIntegrityError('violates unique constraint "uq_apis_vendor_name"'),
                )
            ),
            _SourcePhase.INGEST,
            id="stage-unexpected-db-error",
        ),
        pytest.param(
            _through_pipeline(
                _chained(
                    IngestStageError("Stage 'BuildURLIndexStage' failed unexpectedly"),
                    RuntimeError("regex"),
                )
            ),
            _SourcePhase.INGEST,
            id="stage-unexpected-bug",
        ),
        pytest.param(
            DatabaseIntegrityError('violates unique constraint "uq_other"'),
            _SourcePhase.INGEST,
            id="other-integrity-at-commit",
        ),
        pytest.param(
            DatabaseUnavailableError("connection lost"), _SourcePhase.INGEST, id="db-unavailable"
        ),
    ],
)
def test_retryable_source_failures(exc: Exception, phase: _SourcePhase) -> None:
    """Pins #1980: transient and unexpected failures keep the retry budget."""
    assert _is_deterministic_source_failure(exc, phase=phase) is False
