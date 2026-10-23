"""Server URL resolution stage — makes every declared server URL absolute."""

from __future__ import annotations

from typing import Any, ClassVar

import structlog

from jentic_one.registry.ingest.errors import ServerUrlProblem
from jentic_one.registry.ingest.models import IngestSpecification
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.server_urls import (
    SAMPLE_SIZE,
    DocumentBase,
    ServerResolution,
    document_base,
    is_static_content_host,
    resolve_relative_servers,
    resolved_revision_digest,
    url_host,
)
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos.catalog_repo import CatalogRepository
from jentic_one.shared.config import AppConfig, CatalogConfig
from jentic_one.shared.models import ORIGIN_CATALOG, ORIGIN_OVERLAY

logger = structlog.get_logger(__name__)


def _manifest_host(config: AppConfig | None) -> list[str]:
    """The host the configured catalog manifest is served from."""
    manifest_url = (config.catalog if config is not None else CatalogConfig()).manifest_url
    host = url_host(manifest_url)
    return [host] if host else []


async def _catalog_spec_hosts(session: Any) -> set[str]:
    """Hosts the cached catalog manifest serves its spec files from.

    A manifest may list spec URLs on a host other than its own, so the mirror is
    every one of those hosts, not only the manifest's. Empty when no manifest is
    cached, or when the pipeline runs without a registry session (stage unit
    tests), where there is no snapshot to read.
    """
    if session is None:
        return set()
    urls = await CatalogRepository.manifest_spec_urls(session)
    return {host for host in map(url_host, urls) if host}


async def _document_base(
    spec: IngestSpecification, config: AppConfig | None, session: Any
) -> DocumentBase:
    """Where the spec's document lives, for resolving relative server URLs.

    A catalog spec is fetched from a mirror of the vendor's document, so its
    URL is not where the API is served: resolving against it would route the
    API (and the credentials bound to it) to the mirror's host. Catalog
    re-ingests (overlay materialization) carry ``catalog_api_id`` forward, so
    that marks them too.

    An overlay re-ingest never resolves (``NO_BASE``). It carries the base's
    ``source_url`` but not where that fetch's redirects ended, and the person
    confirming the overlay never sees a host it didn't write: resolving there
    would give a host-less API new hosts without the ``credentials:write``
    review a promote needs. Such an API is fixed by re-importing it from its
    URL and promoting the draft, or by an overlay that sets absolute servers.

    Every other source goes through the same content-host check: the built-in
    list, the configured manifest's host, and every host the cached manifest
    serves spec files from, whichever path imported it.
    """
    if spec.origin == ORIGIN_CATALOG or spec.catalog_api_id is not None:
        return DocumentBase(None, ServerUrlProblem.CONTENT_HOST)
    if spec.origin == ORIGIN_OVERLAY:
        return DocumentBase(None, ServerUrlProblem.NO_BASE)
    base = document_base(
        spec.source_url,
        retrieval_url=spec.retrieval_url,
        content_hosts=_manifest_host(config),
    )
    if base.url is None:
        return base
    mirror = await _catalog_spec_hosts(session)
    if any(is_static_content_host(url_host(u), mirror) for u in (spec.source_url, base.url)):
        return DocumentBase(None, ServerUrlProblem.CONTENT_HOST)
    return base


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
            content,
            await _document_base(spec, ctx.config, ctx.session),
            strict=_is_user_import(spec),
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
