"""Import job handler — processes API import sources.

This handler is registered with the WorkerLoop for kind=import jobs.
It fetches/parses OpenAPI/Arazzo sources and creates draft ApiRevisions.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import TypeAdapter
from structlog.contextvars import bind_contextvars, unbind_contextvars

from jentic_one.registry.ingest.exc import DuplicateRevisionError, IngestJobError
from jentic_one.registry.ingest.fetch import IngestSource, load_specification
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.jobs.handlers import JobResultPayload
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

_source_adapter: TypeAdapter[IngestSource] = TypeAdapter(IngestSource)

# The unique constraint that guards one revision per (api_id, spec_digest).
_DIGEST_CONSTRAINT = "uq_api_revisions_api_id_spec_digest"


def _readable_source_error(exc: Exception) -> str:
    """Map a source-level ingest failure to a message safe to show a user.

    Raw ``IntegrityError`` strings leak SQL and get truncated mid-word in the
    job record; translate the duplicate-digest collision to a clear message and
    keep other failures as their (domain) exception text.
    """
    if isinstance(exc, DatabaseIntegrityError) and _DIGEST_CONSTRAINT in exc.detail:
        return DuplicateRevisionError().message
    return str(exc)


class ImportHandler:
    """Handles kind=import jobs by processing API sources."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def execute(
        self,
        job_id: str,
        session: Any,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        """Process import sources and create draft ApiRevisions."""
        bind_contextvars(job_id=job_id)
        if created_by is None:
            raise IngestJobError("import job is missing the triggering actor (created_by)")
        try:
            payload = payload or {}
            sources = payload.get("sources", [])
            resolved_actor_type = ActorType(actor_type) if actor_type else ActorType.USER
            revisions: list[dict[str, Any]] = []
            failures: list[str] = []

            logger.info("import_handler_start", source_count=len(sources), job_id=job_id)

            for idx, src in enumerate(sources):
                try:
                    source = _source_adapter.validate_python(src)
                    spec = await load_specification(source, config=self._ctx.config.ingest)
                    result = await Ingestor(self._ctx).ingest(spec, created_by=created_by)
                    revisions.append(
                        {
                            "api": {
                                "vendor": result.api_vendor,
                                "name": result.api_name,
                                "version": result.api_version,
                            },
                            "revision_id": str(result.revision_id),
                            "state": result.state,
                        }
                    )
                    await record_audit_best_effort(
                        self._ctx,
                        action=AuditAction.CREATE,
                        target_type=AuditTargetType.REVISION,
                        target_id=str(result.revision_id),
                        actor_type=resolved_actor_type,
                        actor_id=created_by,
                        job_id=job_id,
                        after={
                            "vendor": result.api_vendor,
                            "name": result.api_name,
                            "version": result.api_version,
                            "state": result.state,
                        },
                        origin=None,
                    )
                except Exception as exc:
                    logger.exception("import_source_failed", source_index=idx, job_id=job_id)
                    failures.append(f"source[{idx}]: {_readable_source_error(exc)}")

            logger.info(
                "import_handler_complete",
                job_id=job_id,
                total=len(sources),
                succeeded=len(revisions),
                failed=len(failures),
            )

            # If every source failed, surface it as a failed job rather than
            # masking it behind a "completed" status with an empty result.
            # Each source runs in its own transaction (see Ingestor.ingest), so
            # when nothing succeeded there is no committed work to preserve by
            # returning a "completed" result.
            if failures and not revisions:
                raise IngestJobError(
                    f"all {len(sources)} import source(s) failed: " + "; ".join(failures)
                )

            return JobResultPayload(
                body={"revisions": revisions},
                content_type=None,
            )
        finally:
            unbind_contextvars("job_id")
