"""Base pipeline stage — abstract class for all ingest pipeline stages."""

from __future__ import annotations

import abc
import time
from typing import Any, ClassVar

import structlog
from opentelemetry.trace.status import Status, StatusCode

from jentic_one.registry.ingest.errors import IngestStageError
from jentic_one.registry.ingest.observability import stage_duration, stages_total, tracer
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext

logger = structlog.get_logger(__name__)


class BasePipelineStage(abc.ABC):
    """Abstract base for all pipeline stages."""

    name: ClassVar[str]
    _requires: ClassVar[dict[str, type]]
    _produces: ClassVar[dict[str, type]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "name", ""):
            cls.name = cls.__name__
        if not hasattr(cls, "_requires"):
            cls._requires = {}
        if not hasattr(cls, "_produces"):
            cls._produces = {}

    async def run(self, ctx: PipelineContext) -> None:
        """Orchestrate stage execution with pre/post hooks and contract validation."""
        log = logger.bind(stage=self.name)
        log.info("stage_start")
        with tracer.start_as_current_span(f"ingest.stage {self.name}") as span:
            span.set_attribute("ingest.stage", self.name)
            start = time.perf_counter()
            status = "ok"
            try:
                await self.pre_run(ctx)
                ctx.ensure_requires(self._requires, self)
                await self._run(ctx)
                ctx.ensure_produces(self._produces, self)
                await self.post_run(ctx)
            except IngestStageError as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                log.error("stage_failed", stage=self.name)
                raise
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                log.error(
                    "stage_unexpected_error",
                    stage=self.name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
                # The raised message lands in operator-visible job error records, so
                # it never echoes the raw exception text (DB/driver internals); the
                # detail stays in the server-side log and span above.
                raise IngestStageError(f"Stage '{self.name}' failed unexpectedly") from exc
            else:
                log.info("stage_complete")
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                attrs = {"stage": self.name, "status": status}
                stages_total.add(1, attrs)
                stage_duration.record(elapsed_ms, attrs)

    async def pre_run(self, ctx: PipelineContext) -> None:  # noqa: B027
        """Hook called before _run. Override for setup logic."""

    async def post_run(self, ctx: PipelineContext) -> None:  # noqa: B027
        """Hook called after _run. Override for cleanup logic."""

    @abc.abstractmethod
    async def _run(self, ctx: PipelineContext) -> None:
        """Implement the stage's core logic."""
