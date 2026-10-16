"""Server URL resolution stage — makes every declared server URL absolute."""

from __future__ import annotations

from typing import ClassVar

import structlog

from jentic_one.registry.ingest.models import IngestSpecification
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.server_urls import resolve_relative_servers, server_base_url
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.shared.models import ORIGIN_CATALOG

logger = structlog.get_logger(__name__)


def _document_base_url(spec: IngestSpecification) -> str | None:
    """Where the spec's document lives, for resolving relative server URLs.

    A catalog spec is fetched from a mirror of the vendor's document, so its
    URL is not where the API is served: resolving against it would route the
    API (and the credentials bound to it) to the mirror's host. Catalog
    re-ingests (overlay materialization) carry ``catalog_api_id`` forward, so
    that marks them too.
    """
    if spec.origin == ORIGIN_CATALOG or spec.catalog_api_id is not None:
        return None
    return server_base_url(spec.source_url)


class ResolveServerUrlsStage(BasePipelineStage):
    """Resolves relative server URLs against the source URL, or refuses the spec.

    Runs before anything reads ``servers`` (host-change guard, server rows, URL
    index, stored spec file) so every consumer sees the same absolute URLs.
    """

    name: ClassVar[str] = "ResolveServerUrlsStage"
    _requires: ClassVar[dict[str, type]] = {}
    _produces: ClassVar[dict[str, type]] = {}

    async def _run(self, ctx: PipelineContext) -> None:
        content = ctx.specification.content
        if not content:
            return
        resolution = resolve_relative_servers(content, _document_base_url(ctx.specification))
        if resolution.resolved:
            logger.info(
                "relative_servers_resolved",
                count=len(resolution.resolved),
                servers=[absolute for _, absolute in resolution.resolved],
            )
        if resolution.dropped:
            logger.warning("relative_servers_dropped", servers=resolution.dropped)
