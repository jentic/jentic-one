"""Unit tests for InspectService helper methods."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from jentic_one.registry.services.inspect.models import InspectLoadOptions, TagDescription
from jentic_one.registry.services.inspect.service import (
    InspectService,
    _build_links,
    _extract_tag_descriptions,
)


def _make_operation(
    *,
    op_id: str = "op_abc",
    method: str = "GET",
    path: str = "/v1/pets",
    summary: str | None = "List pets",
    description: str | None = "Get all pets",
    tags: list[str] | None = None,
    servers: list[object] | None = None,
    version_servers: list[object] | None = None,
    api_vendor: str = "acme",
    api_name: str = "pets",
    api_display_name: str | None = None,
    api_version: str = "v1",
    api_description: str | None = "The pets API",
) -> MagicMock:
    """Create a mock Operation with its relationships."""
    op = MagicMock()
    op.id = op_id
    op.method = method
    op.path = path
    op.summary = summary
    op.description = description
    op.tags = tags
    op.servers = servers or []
    op.version_servers = version_servers or []
    op.revision_id = "rev-123"

    api = MagicMock()
    api.vendor = api_vendor
    api.name = api_name
    api.display_name = api_display_name
    api.version = api_version
    api.description = api_description

    revision = MagicMock()
    revision.api = api
    op.revision = revision

    return op


def _make_server(
    url: str = "https://api.example.com", variables: list[object] | None = None
) -> MagicMock:
    server = MagicMock()
    server.url = url
    server.variables = variables or []
    return server


def test_resolve_server_picks_operation_level_servers() -> None:
    op_server = _make_server("https://op-level.example.com")
    version_server = _make_server("https://version-level.example.com")
    operation = _make_operation(servers=[op_server], version_servers=[version_server])

    result = InspectService._resolve_server(operation)

    assert result == "https://op-level.example.com"


def test_resolve_server_falls_back_to_version_servers() -> None:
    version_server = _make_server("https://version-level.example.com")
    operation = _make_operation(servers=[], version_servers=[version_server])

    result = InspectService._resolve_server(operation)

    assert result == "https://version-level.example.com"


def test_resolve_server_returns_none_when_no_servers() -> None:
    operation = _make_operation(servers=[], version_servers=[])

    result = InspectService._resolve_server(operation)

    assert result is None


def test_resolve_server_expands_server_variables() -> None:
    var = MagicMock()
    var.name = "version"
    var.default_value = "v2"
    server = _make_server("https://api.example.com/{version}", variables=[var])
    operation = _make_operation(servers=[server])

    result = InspectService._resolve_server(operation)

    assert result == "https://api.example.com/v2"


def test_build_api_context_returns_canonical_name_and_separate_display_name() -> None:
    # api.name is the canonical identity the broker matches on (#1468); the
    # label rides separately in display_name.
    operation = _make_operation(api_display_name="Acme Pets API")

    context = InspectService._build_api_context(operation)

    assert context.name == "pets"
    assert context.display_name == "Acme Pets API"


def test_build_api_context_display_name_absent_when_unset() -> None:
    operation = _make_operation(api_display_name=None)

    context = InspectService._build_api_context(operation)

    assert context.name == "pets"
    assert context.display_name is None


def test_build_api_context_includes_vendor_and_version() -> None:
    operation = _make_operation()

    context = InspectService._build_api_context(operation)

    assert context.vendor == "acme"
    assert context.version == "v1"


def test_build_api_context_includes_description() -> None:
    operation = _make_operation(api_description="The best API")

    context = InspectService._build_api_context(operation)

    assert context.description == "The best API"


def test_extract_tag_descriptions_returns_empty_for_no_tags() -> None:
    operation = _make_operation(tags=None)
    assert _extract_tag_descriptions(operation) == []


def test_extract_tag_descriptions_returns_tag_descriptions() -> None:
    operation = _make_operation(tags=["pets", "admin"])
    result = _extract_tag_descriptions(operation)
    assert len(result) == 2
    assert result[0] == TagDescription(tag="pets", description="")
    assert result[1] == TagDescription(tag="admin", description="")


def test_build_links_encodes_method_url() -> None:
    links = _build_links("GET", "https://api.example.com/v1/pets")
    assert "/inspect?id=" in links.self_link
    assert "GET" in links.self_link


# --- inspect_by_id: reconstructed URL must match the broker URL index ---

_SERVER_ONLY = InspectLoadOptions(load_spec=False, load_auth=False, load_server=True)


async def test_inspect_by_id_collapses_double_slash() -> None:
    """Regression: reconstructing the URL from a server ending in "/" + a path
    starting with "/" must not yield "host//path". The broker indexes the
    normalized (single-slash) URL, so the double slash made `execute <op_id>`
    fail with `operation_not_found` even though the operation was imported."""
    server = _make_server("https://sheets.googleapis.com/")
    op = _make_operation(servers=[server], path="/v4/spreadsheets/{id}")
    service = InspectService(MagicMock(), base_url="http://127.0.0.1:8000")

    with patch(
        "jentic_one.registry.services.inspect.service.OperationRepository.get_by_id_for_inspect",
        new=AsyncMock(return_value=op),
    ):
        result = await service.inspect_by_id(operation_id="op_abc", load_options=_SERVER_ONLY)

    assert result.url == "https://sheets.googleapis.com/v4/spreadsheets/{id}"
    assert "//v4" not in result.url


async def test_inspect_by_id_without_server_uses_bare_path() -> None:
    op = _make_operation(servers=[], version_servers=[], path="/v4/x")
    service = InspectService(MagicMock(), base_url="http://127.0.0.1:8000")

    with patch(
        "jentic_one.registry.services.inspect.service.OperationRepository.get_by_id_for_inspect",
        new=AsyncMock(return_value=op),
    ):
        result = await service.inspect_by_id(operation_id="op_abc", load_options=_SERVER_ONLY)

    assert result.url == "/v4/x"
