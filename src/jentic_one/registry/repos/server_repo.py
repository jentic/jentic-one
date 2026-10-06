"""Repository for Server entities."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jentic_one.registry.core.schema.servers import Server, ServerVariable


class ServerRepository:
    """Data access layer for Server entities — flush-only, never commits."""

    @staticmethod
    async def delete_for_revision(session: AsyncSession, revision_id: uuid.UUID) -> None:
        await session.execute(delete(Server).where(Server.revision_id == revision_id))
        await session.flush()

    @staticmethod
    async def store_servers(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        servers_data: list[dict[str, Any]],
        operation_id: str | None = None,
        created_by: str,
    ) -> list[uuid.UUID]:
        servers: list[Server] = []
        for server_dict in servers_data:
            server = Server(
                revision_id=revision_id,
                operation_id=operation_id,
                url=server_dict["url"],
                description=server_dict.get("description"),
                created_by=created_by,
            )
            variables: dict[str, Any] = server_dict.get("variables", {})
            for var_name, var_data in variables.items():
                variable = ServerVariable(
                    name=var_name,
                    default_value=var_data.get("default"),
                    description=var_data.get("description"),
                    enum=var_data.get("enum"),
                    extensions=var_data.get("x-extensions"),
                    created_by=created_by,
                )
                server.variables.append(variable)
            servers.append(server)

        if not servers:
            return []

        session.add_all(servers)
        await session.flush()
        return [server.id for server in servers]

    @staticmethod
    async def list_url_specs(
        session: AsyncSession, revision_id: uuid.UUID
    ) -> list[tuple[str, dict[str, list[str]]]]:
        """Every server of a revision as ``(url, {variable: [default, *enum]})``.

        API- and operation-level servers alike; the shape ``hosts_from_servers``
        consumes, so a stored revision's host set compares against a spec's.
        """
        result = await session.execute(
            select(Server)
            .where(Server.revision_id == revision_id)
            .options(selectinload(Server.variables))
        )
        specs: list[tuple[str, dict[str, list[str]]]] = []
        for server in result.scalars().all():
            variables: dict[str, list[str]] = {}
            for variable in server.variables:
                values: list[str] = []
                if variable.default_value is not None:
                    values.append(variable.default_value)
                if isinstance(variable.enum, list):
                    values.extend(
                        str(v) for v in variable.enum if v is not None and str(v) not in values
                    )
                variables[variable.name] = values
            specs.append((server.url, variables))
        return specs
