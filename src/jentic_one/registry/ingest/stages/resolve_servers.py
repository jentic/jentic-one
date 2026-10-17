"""Server URL resolution stage — makes every declared server URL absolute."""

from __future__ import annotations

from typing import ClassVar

import structlog

from jentic_one.registry.ingest.exc import ServerUrlProblem
from jentic_one.registry.ingest.models import IngestSpecification
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.server_urls import (
    SAMPLE_SIZE,
    DocumentBase,
    ServerResolution,
    document_base,
    resolve_relative_servers,
    resolved_revision_digest,
    url_host,
)
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.shared.config import AppConfig, CatalogConfig
from jentic_one.shared.models import ORIGIN_CATALOG, ORIGIN_OVERLAY

logger = structlog.get_logger(__name__)


def _catalog_mirror_hosts(config: AppConfig | None) -> list[str]:
    """The host the catalog manifest (and so every catalog spec) is served from."""
    manifest_url = (config.catalog if config is not None else CatalogConfig()).manifest_url
    host = url_host(manifest_url)
    return [host] if host else []


def _document_base(spec: IngestSpecification, config: AppConfig | None) -> DocumentBase:
    """Where the spec's document lives, for resolving relative server URLs.

    A catalog spec is fetched from a mirror of the vendor's document, so its
    URL is not where the API is served: resolving against it would route the
    API (and the credentials bound to it) to the mirror's host. Catalog
    re-ingests (overlay materialization) carry ``catalog_api_id`` forward, so
    that marks them too. Every other source goes through the same content-host
    check, the catalog mirror included, whichever path imported it.
    """
    if spec.origin == ORIGIN_CATALOG or spec.catalog_api_id is not None:
        return DocumentBase(None, ServerUrlProblem.CONTENT_HOST)
    return document_base(
        spec.source_url,
        retrieval_url=spec.retrieval_url,
        content_hosts=_catalog_mirror_hosts(config),
    )


def _is_user_import(spec: IngestSpecification) -> bool:
    """A manual URL import, paste, or upload, as opposed to a catalog/overlay re-ingest.

    The user can fix the server URL (or import from the right URL), so an
    unresolvable server refuses the import. Catalog imports, update re-imports,
    and overlay materializations keep such servers as declared (inspect-only).
    """
    return spec.origin not in (ORIGIN_CATALOG, ORIGIN_OVERLAY) and spec.catalog_api_id is None


class ResolveServerUrlsStage(BasePipelineStage):
    """Resolves relative server URLs against the document URL, or refuses the spec.

    Runs before anything reads ``servers`` (host-change guard, server rows, URL
    index, stored spec file) so every consumer sees the same absolute URLs.
    Produces ``server_resolution`` (optional, not declared in ``_produces``)
    when it changed or kept back a server, for the import job result.
    """

    name: ClassVar[str] = "ResolveServerUrlsStage"
    _requires: ClassVar[dict[str, type]] = {}
    _produces: ClassVar[dict[str, type]] = {}

    async def _run(self, ctx: PipelineContext) -> None:
        spec = ctx.specification
        content = spec.content
        if not content:
            return
        resolution = resolve_relative_servers(
            content, _document_base(spec, ctx.config), strict=_is_user_import(spec)
        )
        if not resolution.changed:
            return
        if resolution.resolved and spec.sha:
            spec.sha = resolved_revision_digest(spec.sha, resolution.resolved)
        ctx.produce("server_resolution", resolution, ServerResolution)
        log = logger.bind(
            vendor=spec.api_identifier.vendor,
            name=spec.api_identifier.name,
            version=spec.api_identifier.version,
            source_type=spec.source_type,
        )
        if resolution.resolved:
            log.info(
                "relative_servers_resolved",
                count=len(resolution.resolved),
                sample=[absolute for _, absolute in resolution.resolved[:SAMPLE_SIZE]],
            )
        if resolution.dropped:
            log.warning(
                "relative_servers_dropped",
                count=len(resolution.dropped),
                sample=resolution.dropped[:SAMPLE_SIZE],
            )
        if resolution.unresolved:
            log.warning(
                "relative_servers_unresolved",
                count=len(resolution.unresolved),
                sample=resolution.unresolved[:SAMPLE_SIZE],
            )
