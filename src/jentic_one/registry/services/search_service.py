"""Search service — query-time lexical (full-text / BM25) search over operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import expand_server_variables, merge_paths
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.operation_repo import OperationRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.repos.search.errors import SearchUnsupportedError
from jentic_one.registry.repos.search.protocol import SearchCursor
from jentic_one.registry.repos.search.registry import resolve_strategy
from jentic_one.registry.services.errors import (
    ArchivedRevisionPinError,
    InvalidApiFilterError,
    SearchUnavailableError,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionState
from jentic_one.shared.pagination import (
    Page,
    decode_search_cursor,
    encode_search_cursor,
)


@dataclass(frozen=True, slots=True)
class ApiRef:
    """Resolved API identity for a search result."""

    vendor: str
    name: str
    version: str
    host: str | None


@dataclass(frozen=True, slots=True)
class OperationResult:
    """A single search result with metadata matching the spec shape."""

    type: str
    operation_id: str
    method: str
    url: str
    name: str | None
    description: str | None
    relevance_score: float
    api: ApiRef
    inspect_link: str


def compute_relevance_score(distance: float) -> float:
    """Convert a distance metric to a 0-1 relevance score."""
    return max(0.0, 1.0 - distance)


def _resolve_operation_url(operation: Operation) -> str:
    """Build fully-qualified URL for an operation from its server + path.

    The server URL may end in ``/`` and the operation path may start with ``/``
    (both valid OpenAPI). A naive ``base + path`` then produces ``host//path``,
    which never matches the broker's URL index — that index is built with
    :func:`merge_paths`, so the operation can't be resolved by ``operation_id``
    or by the URL we surface in ``search``/``inspect``. Join through the same
    helper so the URL we hand out agrees with what was registered.
    """
    servers = operation.servers or operation.version_servers
    if not servers:
        return operation.path
    server = servers[0]
    base = expand_server_variables(server.url, server.variables)
    return merge_paths(base, operation.path)


def _build_inspect_link(method: str, url: str) -> str:
    """Build the canonical /inspect link in ?id=METHOD%20URL form."""
    encoded_id = quote(f"{method.upper()} {url}", safe="")
    return f"/inspect?id={encoded_id}"


def _parse_api_identifier(entry: str) -> tuple[str, str | None, str | None]:
    """Parse an API filter entry into a ``(vendor, name, version)`` tuple.

    Accepts the canonical ``vendor[/name[/version]]`` slug used everywhere else
    (CLI ``--api`` flags, catalog references, access requests) as well as the
    colon-separated form this endpoint historically required (#1080). Colon
    takes precedence so any existing colon-encoded caller keeps working; a
    slash-form version keeps embedded slashes intact via ``maxsplit``.
    """
    sep = ":" if ":" in entry else "/"
    parts = entry.split(sep, 2)
    vendor = parts[0]
    name = parts[1] if len(parts) > 1 else None
    version = parts[2] if len(parts) > 2 else None
    return vendor, name, version


async def _resolve_api_filters(session: Any, apis: list[str]) -> list[uuid.UUID]:
    """Resolve ``vendor[/name[/version]]`` api identifiers to api_ids."""
    all_ids: list[uuid.UUID] = []
    for entry in apis:
        vendor, name, version = _parse_api_identifier(entry)
        resolved = await ApiRepository.resolve_ids(
            session, vendor=vendor, name=name, version=version
        )
        if not resolved:
            raise InvalidApiFilterError(entry)
        all_ids.extend(resolved)
    return all_ids


async def _resolve_revision_pins(
    session: Any, revision_pins: dict[str, str]
) -> dict[uuid.UUID, uuid.UUID]:
    """Resolve revision_pins from api identifier -> revision_id strings to uuid mapping."""
    resolved: dict[uuid.UUID, uuid.UUID] = {}
    for api_key, rev_id_str in revision_pins.items():
        vendor, name, version = _parse_api_identifier(api_key)
        if name is None or version is None:
            raise InvalidApiFilterError(api_key)
        api = await ApiRepository.get_by_identifier(session, vendor, name, version)
        if api is None:
            raise InvalidApiFilterError(api_key)
        try:
            revision_id = uuid.UUID(rev_id_str)
        except ValueError as exc:
            raise InvalidApiFilterError(f"{api_key} -> {rev_id_str}") from exc
        revision = await ApiRevisionRepository.get_for_api(session, api.id, revision_id)
        if revision is None:
            raise InvalidApiFilterError(f"{api_key} -> {rev_id_str}")
        if revision.state == ApiRevisionState.ARCHIVED:
            raise ArchivedRevisionPinError(api_key, rev_id_str)
        resolved[api.id] = revision_id
    return resolved


class SearchService:
    """Orchestrates search: resolve strategy, run the lexical query, hydrate results."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def search(
        self,
        *,
        query: str,
        apis: list[str] | None = None,
        revision_pins: dict[str, str] | None = None,
        limit: int = 10,
        cursor: str | None = None,
    ) -> Page[OperationResult]:
        config = self._ctx.config.search

        if not config.search_enabled:
            raise SearchUnavailableError(
                "Search is disabled via configuration (search_enabled=false)"
            )

        backend = self._ctx.registry_db.backend
        try:
            strategy = resolve_strategy(backend, config)
        except SearchUnsupportedError as exc:
            raise SearchUnavailableError(str(exc)) from exc

        search_cursor: SearchCursor | None = None
        if cursor is not None:
            distance, op_id = decode_search_cursor(cursor)
            search_cursor = SearchCursor(distance=distance, operation_id=op_id)

        async with self._ctx.registry_db.session() as session:
            api_filters: list[uuid.UUID] | None = None
            if apis:
                api_filters = await _resolve_api_filters(session, apis)

            resolved_pins: dict[uuid.UUID, uuid.UUID] | None = None
            if revision_pins:
                resolved_pins = await _resolve_revision_pins(session, revision_pins)

            hits = await strategy.search_operations(
                session,
                query=query,
                api_filters=api_filters,
                revision_pins=resolved_pins,
                limit=limit + 1,
                cursor=search_cursor,
            )

            if not hits:
                return Page(data=[], has_more=False, next_cursor=None)

            has_more = len(hits) > limit
            hits = hits[:limit]

            op_ids = {h.operation_id for h in hits}
            operations = await OperationRepository.get_by_ids(session, op_ids)
            op_map = {op.id: op for op in operations}

            unique_api_ids = {h.api_id for h in hits}
            api_map: dict[uuid.UUID, ApiRef] = {}
            for api_id in unique_api_ids:
                api_obj = await ApiRepository.get_by_id(session, api_id)
                if api_obj is not None:
                    host: str | None = None
                    op_for_api = next(
                        (
                            op_map[h.operation_id]
                            for h in hits
                            if h.api_id == api_id and h.operation_id in op_map
                        ),
                        None,
                    )
                    if op_for_api is not None:
                        url = _resolve_operation_url(op_for_api)
                        parsed = urlparse(url)
                        host = parsed.hostname
                    api_map[api_id] = ApiRef(
                        vendor=api_obj.vendor,
                        name=api_obj.name,
                        version=api_obj.version,
                        host=host,
                    )

        results: list[OperationResult] = []
        for hit in hits:
            op = op_map.get(hit.operation_id)
            if op is None:
                continue
            api_ref = api_map.get(hit.api_id)
            if api_ref is None:
                continue
            url = _resolve_operation_url(op)
            method = op.method.upper()
            results.append(
                OperationResult(
                    type="operation",
                    operation_id=hit.operation_id,
                    method=method,
                    url=url,
                    name=op.summary,
                    description=op.description,
                    relevance_score=compute_relevance_score(hit.distance),
                    api=api_ref,
                    inspect_link=_build_inspect_link(method, url),
                )
            )

        next_cursor: str | None = None
        if has_more and hits:
            next_cursor = encode_search_cursor(hits[-1].distance, hits[-1].operation_id)

        return Page(data=results, has_more=has_more, next_cursor=next_cursor)
