"""Server extraction stage."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import structlog

from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.repos import OperationRepository, ServerRepository

logger = structlog.get_logger(__name__)


class ExtractServersStage(BasePipelineStage):
    """Extracts servers from the spec and persists them."""

    name: ClassVar[str] = "ExtractServersStage"
    _requires: ClassVar[dict[str, type]] = {"revision_id": uuid.UUID, "operation_ids": set}
    _produces: ClassVar[dict[str, type]] = {"server_ids": set}

    async def pre_run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        await ServerRepository.delete_for_revision(ctx.session, revision_id)

    async def _run(self, ctx: PipelineContext) -> None:
        revision_id = ctx.require("revision_id", uuid.UUID)
        operation_ids: set[str] = ctx.require("operation_ids", set)
        content: dict[str, Any] = ctx.specification.content or {}
        all_server_ids: list[uuid.UUID] = []

        revision_servers: list[dict[str, Any]] = content.get("servers", [])
        if revision_servers:
            ids = await ServerRepository.store_servers(
                ctx.session,
                revision_id=revision_id,
                servers_data=revision_servers,
                operation_id=None,
                created_by=ctx.created_by,
            )
            all_server_ids.extend(ids)

        operations = await OperationRepository.get_by_ids(ctx.session, operation_ids)
        op_by_spec_id: dict[str, str] = {}
        op_by_path_method: dict[tuple[str, str], str] = {}
        for op in operations:
            if op.operation_id:
                op_by_spec_id[op.operation_id] = op.id
            op_by_path_method[(op.path, op.method)] = op.id

        paths: dict[str, Any] = content.get("paths", {})
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue
                op_servers: list[dict[str, Any]] = operation.get("servers", [])
                if not op_servers:
                    continue

                # (path, method) is unique per revision; a spec operationId is
                # not (#1437), so it is only a fallback.
                op_id: str | None = op_by_path_method.get((path, method.upper()))
                spec_op_id = operation.get("operationId")
                if op_id is None and isinstance(spec_op_id, str):
                    op_id = op_by_spec_id.get(spec_op_id)

                if op_id:
                    ids = await ServerRepository.store_servers(
                        ctx.session,
                        revision_id=revision_id,
                        servers_data=op_servers,
                        operation_id=op_id,
                        created_by=ctx.created_by,
                    )
                    all_server_ids.extend(ids)

        ctx.produce("server_ids", set(all_server_ids), set)
