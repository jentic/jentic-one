"""URL index building stage."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import structlog

from jentic_one.registry.core.url_index import (
    build_index_entry,
    expand_server_variables,
    merge_paths,
    parse_server_url,
    structural_regex,
)
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import OperationRepository, UrlIndexRepository

logger = structlog.get_logger(__name__)


class BuildURLIndexStage(BasePipelineStage):
    """Builds URL index entries for all operations."""

    name: ClassVar[str] = "BuildURLIndexStage"
    _requires: ClassVar[dict[str, type]] = {
        "revision_id": uuid.UUID,
        "operation_ids": set,
        "server_ids": set,
    }
    _produces: ClassVar[dict[str, type]] = {}

    async def pre_run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        await UrlIndexRepository.delete_for_revision(ctx.session, revision_id)

    async def _run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        operation_ids: set[str] = ctx.require("operation_ids", set)
        content: dict[str, Any] = ctx.specification.content or {}

        operations = await OperationRepository.get_by_ids(ctx.session, operation_ids)
        revision_servers = content.get("servers", [])

        seen: set[tuple[str, str, str]] = set()

        for op in operations:
            op_servers = self._get_effective_servers(op, content, revision_servers)
            if not op_servers:
                logger.warning("operation_no_servers", operation_id=op.id, path=op.path)
                continue

            for server_data in op_servers:
                variables: list[Any] = server_data.get("variables", [])
                if isinstance(variables, dict):
                    variables = [
                        type("Var", (), {"name": k, "default_value": v.get("default")})()
                        for k, v in variables.items()
                    ]

                expanded_url = expand_server_variables(server_data["url"], variables)
                parsed = parse_server_url(expanded_url)
                full_path = merge_paths(parsed.path, op.path)
                entry = build_index_entry(parsed.host, full_path, parsed.scheme)

                # Dedup on the entry's canonical template, not the raw merged
                # path, so `/a/` and `/a` collapse to one structural key —
                # matching the canonical form the entry itself was built from.
                struct_form = structural_regex(entry.path_pattern)
                dedup_key = (op.method.upper(), entry.host_pattern, struct_form)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                await UrlIndexRepository.upsert_entry(
                    ctx.session,
                    revision_id=revision_id,
                    operation_id=op.id,
                    method=op.method.upper(),
                    entry=entry,
                    created_by=ctx.created_by,
                )

    def _get_effective_servers(
        self, op: Any, content: dict[str, Any], revision_servers: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Determine effective servers for an operation (operation > path > revision level)."""
        paths: dict[str, Any] = content.get("paths", {})
        path_item: dict[str, Any] = paths.get(op.path, {})
        operation_data: dict[str, Any] = path_item.get(op.method.lower(), {})

        op_level_servers: list[dict[str, Any]] = operation_data.get("servers", [])
        if op_level_servers:
            return op_level_servers

        path_level_servers: list[dict[str, Any]] = path_item.get("servers", [])
        if path_level_servers:
            return path_level_servers

        return revision_servers
