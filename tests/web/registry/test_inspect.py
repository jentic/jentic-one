"""Integration tests for the /inspect endpoint."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.repos.operation_repo import _generate_operation_id
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _cleanup(web_context: Context) -> AsyncGenerator[None]:
    yield
    async with web_context.registry_db.session() as session:
        await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
        await session.execute(text("DELETE FROM registry.operation_url_indexes"))
        await session.execute(text("DELETE FROM registry.operations"))
        await session.execute(text("DELETE FROM registry.servers"))
        await session.execute(text("DELETE FROM registry.security_schemes"))
        await session.execute(text("DELETE FROM registry.api_revisions"))
        await session.execute(text("DELETE FROM registry.apis"))
        await session.commit()


async def _seed_operation(
    ctx: Context,
    *,
    vendor: str = "acme",
    name: str = "pets",
    version: str = "v1",
    path: str = "/v1/pets",
    method: str = "GET",
    server_url: str = "https://api.example.com",
    index_host: str = "api.example.com",
    spec_operation_id: str | None = None,
    raw_operation: Mapping[str, object] | None = None,
) -> tuple[Api, ApiRevision, str]:
    """Seed an API, revision, operation, server, and URL index entry.

    Returns (api, revision, operation_id).
    """
    async with ctx.registry_db.session() as session:
        api = Api(
            vendor=vendor,
            name=name,
            version=version,
            display_name="Acme Pets",
            revision_count=1,
            operation_count=1,
        )
        session.add(api)
        await session.flush()

        revision = ApiRevision(
            api_id=api.id,
            state="published",
            spec_digest="sha256:test",
            source_type="url",
            source_url="https://example.com/spec.yaml",
            submitted_by="test",
            operation_count=1,
        )
        session.add(revision)
        await session.flush()

        api.current_revision_id = revision.id
        await session.flush()

        server = Server(revision_id=revision.id, url=server_url)
        session.add(server)
        await session.flush()

        op_id = _generate_operation_id(revision.id, path, method)
        operation = Operation(
            id=op_id,
            revision_id=revision.id,
            path=path,
            method=method,
            operation_id=spec_operation_id,
            summary="List pets",
            description="Returns all pets",
            tags=["pets"],
            raw_operation=raw_operation,
        )
        session.add(operation)
        await session.flush()

        url_index = OperationURLIndex(
            operation_id=op_id,
            revision_id=revision.id,
            method=method,
            host=index_host,
            host_regex=None,
            path_template=path,
            path_regex=f"^{path}$",
            param_names=[],
            segment_count=len(path.strip("/").split("/")),
        )
        session.add(url_index)
        await session.flush()

        await session.commit()
        return api, revision, op_id


async def test_inspect_by_method_url_summary_returns_200(
    authed_client: TestClient, web_context: Context
) -> None:
    _, revision, _ = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={
            "id": "GET https://api.example.com/v1/pets",
            "revision_id": str(revision.id),
            "detail": "summary",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "operation"
    assert data["method"] == "GET"
    assert data["api"]["vendor"] == "acme"
    assert "links" in data


async def test_inspect_by_operation_id_summary_returns_200(
    authed_client: TestClient, web_context: Context
) -> None:
    _, _, op_id = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={"operation_id": op_id, "detail": "summary"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "operation"
    assert data["operation_id"] == op_id


async def test_inspect_by_spec_operation_id_falls_back(
    authed_client: TestClient, web_context: Context
) -> None:
    """A spec operationId (from `catalog show`) resolves via the fallback (#670)."""
    _, _, op_id = await _seed_operation(
        web_context, spec_operation_id="sheets.spreadsheets.values.get"
    )
    resp = authed_client.get(
        "/inspect",
        params={"operation_id": "sheets.spreadsheets.values.get", "detail": "summary"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # The response always carries the registry PK, regardless of the input form.
    assert data["operation_id"] == op_id


async def test_inspect_unknown_spec_operation_id_returns_404(
    authed_client: TestClient, web_context: Context
) -> None:
    await _seed_operation(web_context, spec_operation_id="real.op.id")
    resp = authed_client.get(
        "/inspect",
        params={"operation_id": "does.not.exist", "detail": "summary"},
    )
    assert resp.status_code == 404


async def test_inspect_detail_full_returns_501(
    authed_client: TestClient, web_context: Context
) -> None:
    _, _, op_id = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={"operation_id": op_id, "detail": "full"},
    )
    assert resp.status_code == 501


async def test_inspect_no_identifier_returns_422(authed_client: TestClient) -> None:
    resp = authed_client.get("/inspect", params={"detail": "summary"})
    assert resp.status_code == 400


async def test_inspect_both_identifiers_returns_422(
    authed_client: TestClient, web_context: Context
) -> None:
    _, _revision, op_id = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={
            "id": "GET https://api.example.com/v1/pets",
            "operation_id": op_id,
            "detail": "summary",
        },
    )
    assert resp.status_code == 400


async def test_inspect_not_found_returns_404(
    authed_client: TestClient, web_context: Context
) -> None:
    _, revision, _ = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={
            "id": "GET https://unknown.example.com/nope",
            "revision_id": str(revision.id),
            "detail": "summary",
        },
    )
    assert resp.status_code == 404


async def test_inspect_method_not_allowed_returns_405(
    authed_client: TestClient, web_context: Context
) -> None:
    _, revision, _ = await _seed_operation(web_context, method="POST")
    resp = authed_client.get(
        "/inspect",
        params={
            "id": "GET https://api.example.com/v1/pets",
            "revision_id": str(revision.id),
            "detail": "summary",
        },
    )
    assert resp.status_code == 405
    assert "POST" in resp.headers.get("Allow", "")


async def test_inspect_accept_markdown_returns_text(
    authed_client: TestClient, web_context: Context
) -> None:
    _, _, op_id = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={"operation_id": op_id, "detail": "summary"},
        headers={"Accept": "text/markdown"},
    )
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers.get("content-type", "")
    assert "# " in resp.text


async def test_inspect_accept_openapi_yaml(authed_client: TestClient, web_context: Context) -> None:
    _, _, op_id = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={"operation_id": op_id, "detail": "summary"},
        headers={"Accept": "application/openapi+yaml"},
    )
    assert resp.status_code == 200
    assert "openapi+yaml" in resp.headers.get("content-type", "")
    assert "openapi:" in resp.text


async def test_inspect_accept_unsupported_returns_406(
    authed_client: TestClient, web_context: Context
) -> None:
    _, _, op_id = await _seed_operation(web_context)
    resp = authed_client.get(
        "/inspect",
        params={"operation_id": op_id, "detail": "summary"},
        headers={"Accept": "application/xml"},
    )
    assert resp.status_code == 406


async def test_inspect_without_auth_returns_401(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/inspect", params={"operation_id": "op_abc", "detail": "summary"})
    assert resp.status_code == 401


async def test_inspect_surfaces_header_query_path_and_body_inputs(
    authed_client: TestClient, web_context: Context
) -> None:
    """Regression for #768: imported operations must expose their header,
    query, path, and body inputs — not only path params — so a client can
    build a complete, callable request."""
    raw_operation = {
        "operation_id": "createPage",
        "path": "/v1/pages/{page_id}",
        "method": "POST",
        "parameters": [
            {"name": "page_id", "in": "path", "required": True},
            {"name": "page_size", "in": "query", "schema": {"type": "integer"}},
            {"name": "Notion-Version", "in": "header", "required": True},
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"parent": {"type": "object"}}}
                }
            },
        },
    }
    _, _, op_id = await _seed_operation(
        web_context,
        path="/v1/pages/{page_id}",
        method="POST",
        raw_operation=raw_operation,
    )
    resp = authed_client.get("/inspect", params={"operation_id": op_id, "detail": "summary"})
    assert resp.status_code == 200
    inputs = resp.json()["inputs"]

    assert [p["name"] for p in inputs["path"]] == ["page_id"]
    assert [p["name"] for p in inputs["query"]] == ["page_size"]
    assert [p["name"] for p in inputs["header"]] == ["Notion-Version"]
    assert inputs["body"]["required"] is True
    assert inputs["body"]["content_type"] == "application/json"
    assert inputs["body"]["schema"]["type"] == "object"


async def test_inspect_no_raw_operation_yields_empty_inputs(
    authed_client: TestClient, web_context: Context
) -> None:
    """An operation stored without a raw_operation blob still inspects cleanly
    with empty input groups (backwards compatibility)."""
    _, _, op_id = await _seed_operation(web_context, raw_operation=None)
    resp = authed_client.get("/inspect", params={"operation_id": op_id, "detail": "summary"})
    assert resp.status_code == 200
    inputs = resp.json()["inputs"]
    assert inputs["path"] == []
    assert inputs["query"] == []
    assert inputs["header"] == []
    assert inputs["body"] is None
