"""Ingestor service — owns the transaction boundary for API spec ingestion."""

from __future__ import annotations

import time
import uuid

import structlog
from opentelemetry.trace.status import Status, StatusCode

from jentic_one.registry.ingest.host_change_guard import api_has_bound_credentials
from jentic_one.registry.ingest.models import IngestSpecification
from jentic_one.registry.ingest.observability import (
    ingest_duration,
    ingest_operations,
    ingests_total,
    tracer,
)
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.pipeline.pipeline import PipelineFactory
from jentic_one.registry.ingest.schemas import HeldHostChange, IngestResult
from jentic_one.shared import Context
from jentic_one.shared.models import ORIGIN_OVERLAY, ApiRevisionState

logger = structlog.get_logger(__name__)


class Ingestor:
    """Style A service: ingests an API specification into a draft revision."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def ingest(self, spec: IngestSpecification, *, created_by: str) -> IngestResult:
        """Run the full ingest pipeline within a single transaction."""
        with tracer.start_as_current_span("ingest.ingest") as span:
            span.set_attribute("ingest.vendor", spec.api_identifier.vendor)
            span.set_attribute("ingest.name", spec.api_identifier.name)
            span.set_attribute("ingest.version", spec.api_identifier.version)
            span.set_attribute("ingest.spec_type", str(spec.spec_type))
            start = time.perf_counter()
            status = "ok"
            try:
                logger.info(
                    "ingest_start",
                    vendor=spec.api_identifier.vendor,
                    name=spec.api_identifier.name,
                    version=spec.api_identifier.version,
                )

                # Lexical search text is built for every backend; only the
                # feature flag gates it.
                include_search_text = self._ctx.config.search.enabled
                spec = await self._with_host_change_guard(spec)

                async with self._ctx.registry_db.transaction() as session:
                    pipeline_ctx = PipelineContext(
                        session=session,
                        specification=spec,
                        created_by=created_by,
                        config=self._ctx.config,
                    )
                    pipeline = PipelineFactory.from_specification(
                        spec, include_search_text=include_search_text
                    )
                    await pipeline.run(pipeline_ctx)

                    operation_ids: set[str] = pipeline_ctx.require("operation_ids", set)
                    revision_id: uuid.UUID = pipeline_ctx.require("revision_id", uuid.UUID)
                    # Optional: only produced by CreateRevisionStage for an overlay
                    # materialization that superseded an existing current revision.
                    superseded_revision_id: uuid.UUID | None = pipeline_ctx.get(
                        "superseded_revision_id"
                    )
                    held: dict[str, list[str]] | None = pipeline_ctx.get("held_host_change")

                held_host_change = HeldHostChange(**held) if held is not None else None

                result_state = (
                    ApiRevisionState.IMPORTED
                    if spec.origin is not None and held_host_change is None
                    else ApiRevisionState.DRAFT
                )
                result = IngestResult(
                    api_vendor=spec.api_identifier.vendor,
                    api_name=spec.api_identifier.name,
                    api_version=spec.api_identifier.version,
                    revision_id=revision_id,
                    superseded_revision_id=superseded_revision_id,
                    state=result_state,
                    operation_count=len(operation_ids),
                    held_host_change=held_host_change,
                )
                if held_host_change is not None:
                    logger.info(
                        "ingest_server_host_change_held",
                        revision_id=str(revision_id),
                        current_hosts=held_host_change.current_hosts,
                        new_hosts=held_host_change.new_hosts,
                    )
                ingest_operations.record(len(operation_ids))
                logger.info("ingest_complete", revision_id=str(revision_id))
                return result
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                ingests_total.add(1, {"status": status})
                ingest_duration.record(elapsed_ms, {"status": status})

    async def _with_host_change_guard(self, spec: IngestSpecification) -> IngestSpecification:
        """Arm the server-host change guard for a non-operator catalog ingest.

        Applies to origin-tracked, non-overlay ingests (catalog re-imports) that
        were not operator-approved, and only when the API has credentials bound
        to a consumer. The binding lookup crosses into the control/admin
        databases, so it runs here, before the registry transaction opens.
        Overlay materialization is already operator-gated (``overlays:confirm``).
        """
        if spec.origin is None or spec.origin == ORIGIN_OVERLAY or spec.host_change_approved:
            return spec
        identifier = spec.api_identifier
        if not await api_has_bound_credentials(
            self._ctx,
            vendor=identifier.vendor,
            name=identifier.name,
            version=identifier.version,
        ):
            return spec
        return spec.model_copy(update={"guard_host_change": True})
