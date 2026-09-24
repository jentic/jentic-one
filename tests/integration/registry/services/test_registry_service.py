"""Integration tests for the registry ``RegistryService`` facade.

Seeds a real Registry DB (api → revision → operation → url-index) and asserts
that ``RegistryService.resolve_operation`` returns the operation id plus the
``APIReference`` identity, returns ``None`` for unknown URLs, and raises
``AmbiguousMatchError`` on equal-specificity matches.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import build_index_entry, merge_paths
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.registry.services.errors import AmbiguousMatchError
from jentic_one.registry.services.inspect.registry_service import RegistryService
from jentic_one.shared.db.session import DatabaseSession

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
    assert result.operation_id == op_id
    assert result.api.vendor == "acme.com"
    assert result.api.name == "pets-api"
    assert result.api.version == "v1"
    assert result.path_params == {"petId": "123"}


async def test_resolve_operation_uses_display_name_when_set(
    registry_db: DatabaseSession, clean_url_index: None
) -> None:
    """``APIReference.name`` falls back to the API's display_name when present."""
    api = Api(vendor="acme.com", name="pets-api", version="v1", display_name="Acme Pets")
    async with registry_db.session() as session:
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="published", source_type="url")
        session.add(revision)
        await session.flush()
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
    assert result.api.name == "Acme Pets"


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
    assert result.operation_id == op_id


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
    assert result.operation_id == op_id
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
    assert result.operation_id == op_id
