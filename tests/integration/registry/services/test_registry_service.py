"""Integration tests for the registry ``RegistryService`` facade.

Seeds a real Registry DB (api → revision → operation → url-index) and asserts
that ``RegistryService.resolve_operation`` returns the operation id plus the
``APIReference`` identity, returns ``None`` for unknown URLs, and raises
``AmbiguousMatchError`` on equal-specificity matches.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, event, select, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import (
    build_index_entry,
    build_server_index_entries,
    merge_paths,
)
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.registry.services.inspect.registry_service import RegistryService
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.discovery.errors import AmbiguousMatchError
from jentic_one.wiring import InProcessRegistryResolver
from tests.harness.db_round_trips import trace_round_trips

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_url_index(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Truncate the tables this module touches, before and after each test."""

    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(Operation))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_operation(
    registry_db: DatabaseSession,
    *,
    vendor: str,
    name: str,
    version: str,
    host: str,
    path_template: str,
    method: str = "GET",
) -> str:
    """Seed an api → revision → operation → url-index row; return the operation id."""
    api = Api(vendor=vendor, name=name, version=version)
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="published", source_type="url")
        session.add(revision)
        await session.flush()
        # Unpinned resolution only serves the API's live revision.
        api.current_revision_id = revision.id
        rev_id = revision.id
        await session.commit()

    async with registry_db.session() as session:
        op_ids = await OperationRepository.bulk_create(
            session,
            rev_id,
            [OperationInput(path=path_template, method=method)],
            created_by="usr_test",
        )
        await session.commit()

    entry = build_index_entry(host, path_template, "https")
    async with registry_db.session() as session:
        await UrlIndexRepository.upsert_entry(
            session,
            revision_id=rev_id,
            operation_id=op_ids[0],
            method=method,
            entry=entry,
            created_by="usr_test",
        )
        await session.commit()

    return op_ids[0]


async def test_resolve_operation_returns_operation_and_api_context(
    registry_db: DatabaseSession, clean_url_index: None
) -> None:
    """A known URL+method resolves to the operation id and its API identity."""
    op_id = await _seed_operation(
        registry_db,
        vendor="acme.com",
        name="pets-api",
        version="v1",
        host="api.acme.com",
        path_template="/v1/pets/{petId}",
    )

    async with registry_db.session() as session:
        svc = RegistryService(session)
        result = await svc.resolve_operation(method="GET", url="https://api.acme.com/v1/pets/123")

    assert result is not None
    assert result.operation.id == op_id
    assert result.operation.path == "/v1/pets/{petId}"
    assert result.operation.method == "GET"
    assert result.api.vendor == "acme.com"
    assert result.api.name == "pets-api"
    assert result.api.version == "v1"
    assert result.path_params == {"petId": "123"}


@pytest.mark.parametrize("pinned", [False, True], ids=["unpinned", "pinned"])
async def test_resolve_operation_issues_one_registry_statement(
    registry_db: DatabaseSession, clean_url_index: None, pinned: bool
) -> None:
    """Resolving a URL is a single registry round trip (#1699).

    The candidate query carries the API identity and the operation's
    path/method, so no follow-up context query runs. The broker resolves every
    upstream call through here, so a second statement doubles its registry load.
    """
    op_id = await _seed_operation(
        registry_db,
        vendor="acme.com",
        name="pets-api",
        version="v1",
        host="api.acme.com",
        path_template="/v1/pets/{petId}",
    )
    async with registry_db.session() as session:
        rev_id = (
            await session.execute(
                select(OperationURLIndex.revision_id).where(OperationURLIndex.operation_id == op_id)
            )
        ).scalar_one()

    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _params, _context, _executemany) -> None:
        statements.append(statement)

    sync_engine = registry_db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        async with registry_db.session() as session:
            result = await RegistryService(session).resolve_operation(
                method="GET",
                url="https://api.acme.com/v1/pets/123",
                revision_id=rev_id if pinned else None,
            )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert len(statements) == 1, statements
    assert result is not None
    assert result.operation.id == op_id
    assert result.operation.path == "/v1/pets/{petId}"
    assert result.operation.method == "GET"
    assert result.operation.relative_path == "/v1/pets/123"
    assert (result.api.vendor, result.api.name, result.api.version) == (
        "acme.com",
        "pets-api",
        "v1",
    )
    assert result.path_params == {"petId": "123"}


@pytest.mark.parametrize(
    "url",
    ["https://api.acme.com/v1/pets/123", "https://api.unknown.example/v1/pets/123"],
    ids=["hit", "miss"],
)
async def test_broker_resolve_operation_reads_without_begin_or_rollback(
    registry_db: DatabaseSession, clean_url_index: None, url: str
) -> None:
    """The broker's URL lookup runs in autocommit: no BEGIN/ROLLBACK round trips (#1823).

    The miss falls through to the host-regex query, so it pins the
    multi-statement read path as well as the single-statement hit.
    """
    await _seed_operation(
        registry_db,
        vendor="acme.com",
        name="pets-api",
        version="v1",
        host="api.acme.com",
        path_template="/v1/pets/{petId}",
    )
    resolver = InProcessRegistryResolver(registry_db)

    with trace_round_trips(registry_db) as trace:
        result = await resolver.resolve_operation(method="GET", url=url)

    assert (result is not None) == url.startswith("https://api.acme.com")
    if url.startswith("https://api.acme.com"):
        assert len(trace.statements) == 1, trace.statements
    else:
        assert len(trace.statements) >= 2, trace.statements
    trace.assert_no_begin_or_rollback()


async def test_resolve_operation_ignores_display_name(
    registry_db: DatabaseSession, clean_url_index: None
) -> None:
    """``APIReference.name`` is the canonical name even when a display_name is set.

    The broker matches credentials on this identity, so an editable display
    label (including a case-only variant of the name) must not change it.
    """
    api = Api(vendor="acme.com", name="pets-api", version="v1", display_name="Pets-API")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="published", source_type="url")
        session.add(revision)
        await session.flush()
        # Unpinned resolution only serves the API's live revision.
        api.current_revision_id = revision.id
        rev_id = revision.id
        await session.commit()

    async with registry_db.session() as session:
        op_ids = await OperationRepository.bulk_create(
            session, rev_id, [OperationInput(path="/v1/pets", method="GET")], created_by="usr_test"
        )
        await session.commit()

    entry = build_index_entry("api.acme.com", "/v1/pets", "https")
    async with registry_db.session() as session:
        await UrlIndexRepository.upsert_entry(
            session,
            revision_id=rev_id,
            operation_id=op_ids[0],
            method="GET",
            entry=entry,
            created_by="usr_test",
        )
        await session.commit()

    async with registry_db.session() as session:
        svc = RegistryService(session)
        result = await svc.resolve_operation(method="GET", url="https://api.acme.com/v1/pets")

    assert result is not None
    assert result.api.vendor == "acme.com"
    assert result.api.name == "pets-api"
    assert result.api.version == "v1"


async def test_resolve_operation_unknown_url_returns_none(
    registry_db: DatabaseSession, clean_url_index: None
) -> None:
    """An unknown URL resolves to None (→ 404 at the caller)."""
    await _seed_operation(
        registry_db,
        vendor="acme.com",
        name="pets-api",
        version="v1",
        host="api.acme.com",
        path_template="/v1/pets",
    )

    async with registry_db.session() as session:
        svc = RegistryService(session)
        result = await svc.resolve_operation(method="GET", url="https://api.acme.com/v1/unknown")

    assert result is None


async def test_resolve_operation_ambiguous_match_raises(
    registry_db: DatabaseSession, clean_url_index: None
) -> None:
    """Two equal-specificity parameterized matches raise AmbiguousMatchError."""
    api = Api(vendor="acme.com", name="pets-api", version="v1")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="published", source_type="url")
        session.add(revision)
        await session.flush()
        # Unpinned resolution only serves the API's live revision.
        api.current_revision_id = revision.id
        rev_id = revision.id
        await session.commit()

    async with registry_db.session() as session:
        op_ids = await OperationRepository.bulk_create(
            session,
            rev_id,
            [
                OperationInput(path="/v1/{a}", method="GET"),
                OperationInput(path="/v1/{b}", method="GET"),
            ],
            created_by="usr_test",
        )
        await session.commit()

    async with registry_db.session() as session:
        for op_id, template in zip(op_ids, ("/v1/{a}", "/v1/{b}"), strict=False):
            entry = build_index_entry("api.acme.com", template, "https")
            await UrlIndexRepository.upsert_entry(
                session,
                revision_id=rev_id,
                operation_id=op_id,
                method="GET",
                entry=entry,
                created_by="usr_test",
            )
        await session.commit()

    async with registry_db.session() as session:
        svc = RegistryService(session)
        with pytest.raises(AmbiguousMatchError):
            await svc.resolve_operation(method="GET", url="https://api.acme.com/v1/thing")


@pytest.mark.parametrize("path_template", ["/api/bootstrap-static", "/api/bootstrap-static/"])
@pytest.mark.parametrize("request_path", ["/api/bootstrap-static", "/api/bootstrap-static/"])
async def test_resolve_operation_trailing_slash_combinations(
    registry_db: DatabaseSession,
    clean_url_index: None,
    path_template: str,
    request_path: str,
) -> None:
    """All four spec/request trailing-slash combinations resolve (#1085).

    Before the fix, a template ending in ``/`` baked the slash into the stored
    regex while the lookup normalized it off the request path, so operations
    of trailing-slash APIs (Django/DRF style, e.g. the Fantasy Premier League
    API) always failed discovery with ``operation_not_found``.
    """
    op_id = await _seed_operation(
        registry_db,
        vendor="premierleague.com",
        name="fpl",
        version="v1",
        host="fantasy.premierleague.com",
        path_template=path_template,
    )

    async with registry_db.session() as session:
        svc = RegistryService(session)
        result = await svc.resolve_operation(
            method="GET", url=f"https://fantasy.premierleague.com{request_path}"
        )

    assert result is not None
    assert result.operation.id == op_id


@pytest.mark.parametrize("path_template", ["/v1/pets/{petId}", "/v1/pets/{petId}/"])
@pytest.mark.parametrize("request_path", ["/v1/pets/123", "/v1/pets/123/"])
async def test_resolve_operation_parameterized_trailing_slash_combinations(
    registry_db: DatabaseSession,
    clean_url_index: None,
    path_template: str,
    request_path: str,
) -> None:
    """Parameterized templates resolve across all trailing-slash combinations,
    with path params extracted intact."""
    op_id = await _seed_operation(
        registry_db,
        vendor="acme.com",
        name="pets-api",
        version="v1",
        host="api.acme.com",
        path_template=path_template,
    )

    async with registry_db.session() as session:
        svc = RegistryService(session)
        result = await svc.resolve_operation(
            method="GET", url=f"https://api.acme.com{request_path}"
        )

    assert result is not None
    assert result.operation.id == op_id
    assert result.path_params == {"petId": "123"}


async def test_advertised_url_round_trip_for_trailing_slash_spec(
    registry_db: DatabaseSession, clean_url_index: None
) -> None:
    """The URL that search/inspect advertise for a trailing-slash spec resolves.

    ``search``/``inspect`` build the advertised URL from the *raw* spec path
    via ``merge_paths`` (trailing slash preserved, so upstreams that 301 the
    slash-less form keep working), while the URL index matches on the
    canonical form. This pins the advertise → execute round trip both halves
    rely on.
    """
    template = "/api/bootstrap-static/"
    op_id = await _seed_operation(
        registry_db,
        vendor="premierleague.com",
        name="fpl",
        version="v1",
        host="fantasy.premierleague.com",
        path_template=template,
    )

    advertised = merge_paths("https://fantasy.premierleague.com", template)
    assert advertised == "https://fantasy.premierleague.com/api/bootstrap-static/"

    async with registry_db.session() as session:
        svc = RegistryService(session)
        result = await svc.resolve_operation(method="GET", url=advertised)

    assert result is not None
    assert result.operation.id == op_id


async def _seed_server_operation(
    registry_db: DatabaseSession,
    *,
    server_url: str,
    variables: dict[str, dict[str, object]] | None,
    path_template: str,
    method: str = "GET",
) -> str:
    """Seed an operation indexed exactly as ingestion indexes a server-scoped spec.

    The operation row holds the spec-relative template; the URL-index rows
    carry the server base (static or templated) via ``build_server_index_entries``.
    """
    api = Api(vendor="widgets-example-com", name="widgets", version="1.0.0")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="published", source_type="url")
        session.add(revision)
        await session.flush()
        api.current_revision_id = revision.id
        rev_id = revision.id
        await session.commit()

    async with registry_db.session() as session:
        op_ids = await OperationRepository.bulk_create(
            session,
            rev_id,
            [OperationInput(path=path_template, method=method)],
            created_by="usr_test",
        )
        expansion = build_server_index_entries(server_url, variables, path_template)
        for entry in expansion.entries:
            await UrlIndexRepository.upsert_entry(
                session,
                revision_id=rev_id,
                operation_id=op_ids[0],
                method=method,
                entry=entry,
                created_by="usr_test",
            )
        await session.commit()
    return op_ids[0]


@pytest.mark.parametrize(
    ("server_url", "variables", "url", "expected"),
    [
        # Petstore-shaped static base path.
        (
            "https://petstore.example.com/api/v3",
            None,
            "https://petstore.example.com/api/v3/widgets/9",
            "/widgets/9",
        ),
        # The #1424 repro: a templated path server variable.
        (
            "http://{host}:18765/{region}",
            {"host": {"default": "localhost"}, "region": {"enum": ["eu", "us"], "default": "eu"}},
            "http://localhost:18765/eu/widgets/9",
            "/widgets/9",
        ),
        # No base path: the relative path is the request path.
        ("https://api.example.com", None, "https://api.example.com/widgets/9/", "/widgets/9"),
    ],
)
async def test_resolve_operation_relative_path_excludes_server_base(
    registry_db: DatabaseSession,
    clean_url_index: None,
    server_url: str,
    variables: dict[str, dict[str, object]] | None,
    url: str,
    expected: str,
) -> None:
    """Pins #1424: discovery hands the broker the server-relative concrete path
    (the basis binding rules are authored on), never the full upstream path."""
    await _seed_server_operation(
        registry_db, server_url=server_url, variables=variables, path_template="/widgets/{id}"
    )

    async with registry_db.session() as session:
        result = await RegistryService(session).resolve_operation(method="GET", url=url)

    assert result is not None
    assert result.operation.path == "/widgets/{id}"
    assert result.operation.relative_path == expected
