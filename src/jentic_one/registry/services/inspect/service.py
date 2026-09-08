"""Inspect service — loads and assembles operation structural detail."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import expand_server_variables, merge_paths
from jentic_one.registry.repos.operation_repo import OperationRepository
from jentic_one.registry.repos.security_repo import SecurityRepository
from jentic_one.registry.services.errors import OperationNotFoundError
from jentic_one.registry.services.inspect.auth import translate_security_schemes
from jentic_one.registry.services.inspect.inputs import build_operation_inputs
from jentic_one.registry.services.inspect.models import (
    ApiContext,
    AuthInstruction,
    InspectLinks,
    InspectLoadOptions,
    OperationInspectResult,
    TagDescription,
)


class InspectService:
    """Assembles a full structural view of an operation."""

    def __init__(self, session: Any, *, base_url: str = "") -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    async def inspect(
        self,
        *,
        operation_id: str,
        method: str,
        url: str,
        load_options: InspectLoadOptions,
    ) -> OperationInspectResult:
        """Inspect an operation given its ID plus the resolved method/url."""
        operation = await self._load_operation(operation_id)
        return await self._build_result(
            operation, method=method, url=url, load_options=load_options
        )

    async def inspect_by_id(
        self,
        *,
        operation_id: str,
        load_options: InspectLoadOptions,
    ) -> OperationInspectResult:
        """Inspect an operation using only its ID (reconstruct method/url)."""
        operation = await self._load_operation(operation_id)
        method = operation.method.upper()
        server = self._resolve_server(operation)
        # Join through merge_paths so a server ending in "/" and a path starting
        # with "/" don't yield "host//path" — that double slash never matches the
        # broker's URL index (built via merge_paths) and the operation can't be
        # executed by operation_id. With no server, the bare path is the best ref.
        url = merge_paths(server, operation.path) if server else operation.path
        return await self._build_result(
            operation, method=method, url=url, load_options=load_options
        )

    async def _build_result(
        self,
        operation: Operation,
        *,
        method: str,
        url: str,
        load_options: InspectLoadOptions,
    ) -> OperationInspectResult:
        """Assemble the inspect result from an already-loaded operation."""
        server: str | None = None
        if load_options.load_server:
            server = self._resolve_server(operation)

        auth: list[AuthInstruction] | None = None
        if load_options.load_auth:
            auth = await self._load_security_schemes(operation.revision_id)

        raw_spec: dict[str, object] | None = None
        if load_options.load_spec:
            raise NotImplementedError("detail=full is not yet supported")

        api_context = self._build_api_context(operation)
        links = _build_links(method, url, self._base_url)
        inputs = build_operation_inputs(operation.raw_operation)

        return OperationInspectResult(
            operation_id=operation.id,
            method=method.upper(),
            url=url,
            name=operation.summary,
            description=operation.description,
            api=api_context,
            inputs=inputs,
            response_schema=None,
            auth=auth,
            server=server,
            raw_spec=raw_spec,
            links=links,
        )

    async def _load_operation(self, operation_id: str) -> Operation:
        operation = await OperationRepository.get_by_id_for_inspect(self._session, operation_id)
        if operation is None:
            raise OperationNotFoundError(operation_id)
        return operation

    async def _load_security_schemes(self, revision_id: object) -> list[AuthInstruction]:
        schemes = await SecurityRepository.get_by_revision(self._session, revision_id)  # type: ignore[arg-type]
        return translate_security_schemes(schemes)

    @staticmethod
    def _resolve_server(operation: Operation) -> str | None:
        servers = operation.servers or operation.version_servers
        if not servers:
            return None
        server = servers[0]
        return expand_server_variables(server.url, server.variables)

    @staticmethod
    def _build_api_context(operation: Operation) -> ApiContext:
        api = operation.revision.api
        return ApiContext(
            vendor=api.vendor,
            name=api.display_name or api.name,
            version=api.version,
            description=api.description,
            tag_descriptions=_extract_tag_descriptions(operation),
        )


def _extract_tag_descriptions(operation: Operation) -> list[TagDescription]:
    if not operation.tags:
        return []
    return [TagDescription(tag=t, description="") for t in operation.tags]


def _build_links(method: str, url: str, base_url: str = "") -> InspectLinks:
    encoded_id = quote(f"{method.upper()} {url}", safe="")
    return InspectLinks(self_link=f"{base_url}/inspect?id={encoded_id}")
