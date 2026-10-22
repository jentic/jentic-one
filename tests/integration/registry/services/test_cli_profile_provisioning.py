"""CLI profile provisioning end to end, on the selected backend (Postgres or SQLite).

Provisioning the GitHub profile leaves its three APIs available: the REST API
imported from the public catalog (an enqueued import job, run here with the
spec fetch stubbed), the GraphQL API imported from the spec shipped in the
package and live at once, and the built-in git API with nothing to import. A
second run changes nothing, and two concurrent runs import the shipped spec
once. The broker resolves a GraphQL call to the shipped API.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, func, select, update

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.repos.catalog_repo import CatalogRepository
from jentic_one.registry.services.cli_profile_service import CliProfileService
from jentic_one.registry.services.errors import CliProfileNotFoundError
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import CliFacadesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, RevisionOrigin
from jentic_one.shared.models.jobs import JobKind
from jentic_one.wiring import InProcessRegistryResolver

pytestmark = pytest.mark.integration

_USER_ID = "usr_cliprof_operator"
_IDENTITY = Identity(sub=_USER_ID, actor_type=ActorType.USER, permissions=["org:admin"])
_REST_SPEC_URL = "https://catalog.example.com/github/openapi.json"

_REST_SPEC = json.dumps(
    {
        "openapi": "3.0.3",
        "info": {"title": "GitHub v3 REST API", "version": "1.1.4"},
        "servers": [{"url": "https://api.github.com"}],
        "components": {"securitySchemes": {"token": {"type": "http", "scheme": "bearer"}}},
        "security": [{"token": []}],
        "paths": {
            "/user": {"get": {"operationId": "usersGetAuthenticated", "responses": {"200": {}}}}
        },
    }
)


def _spec_client() -> AsyncMock:
    """An httpx.AsyncClient double serving the REST spec for any GET."""
    body = _REST_SPEC.encode()
    response = AsyncMock()
    response.status_code = 200
    response.text = _REST_SPEC
    response.content = body
    response.headers = {"content-length": str(len(body))}
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


async def _truncate(ctx: Context) -> None:
    async with ctx.registry_db.session() as session:
        for table in (
            OperationURLIndex,
            SecuritySchemeFlow,
            SecurityScheme,
            ServerVariable,
            Server,
            Operation,
            SpecFile,
        ):
            await session.execute(delete(table))
        await session.execute(update(Api).values(current_revision_id=None))
        await session.execute(delete(ApiRevision))
        await session.execute(delete(Api))
        await session.execute(delete(CatalogSnapshot))
        await session.commit()
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(Job))


@pytest.fixture()
def github_enabled(integration_context: Context) -> Iterator[Context]:
    config = integration_context.config
    prior = config.cli_facades
    config.cli_facades = CliFacadesConfig.model_validate(
        {"enabled": True, "vendors": {"github": {"enabled": True, "host": "github.jentic.test"}}}
    )
    yield integration_context
    config.cli_facades = prior


@pytest.fixture()
async def clean(github_enabled: Context) -> AsyncIterator[Context]:
    ctx = github_enabled
    await _truncate(ctx)
    async with ctx.registry_db.transaction() as session:
        await CatalogRepository.replace(
            session,
            source_url=ctx.config.catalog.manifest_url,
            entries=[
                {
                    "api_id": "github.com/api.github.com",
                    "path": "apis/openapi/github.com/api.github.com",
                    "spec_url": _REST_SPEC_URL,
                }
            ],
        )
    yield ctx
    await _truncate(ctx)


async def _import_jobs(ctx: Context) -> list[Job]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(select(Job).where(Job.kind == JobKind.IMPORT.value))
        return list(rows.scalars().all())


async def _run_import_job(ctx: Context, job: Job) -> None:
    payload: Any = job.payload
    if isinstance(payload, str):
        payload = json.loads(payload)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", return_value=_spec_client()):
        await ImportHandler(ctx).execute(
            job_id=job.id, db=None, payload=payload, created_by=_USER_ID
        )


def _statuses(result: Any) -> dict[str, str]:
    return {a.role: a.status for a in result.apis}


async def test_github_profile_provisions_rest_graphql_and_git(clean: Context) -> None:
    ctx = clean
    svc = CliProfileService(ctx)

    first = await svc.provision("github", _IDENTITY)
    assert first.vendor == "github" and first.vendor_enabled
    assert _statuses(first) == {"rest": "import_queued", "graphql": "imported", "git": "builtin"}
    [job] = await _import_jobs(ctx)
    rest = next(a for a in first.apis if a.role == "rest")
    assert rest.job_id == job.id

    await _run_import_job(ctx, job)

    second = await svc.provision("github", _IDENTITY)
    assert _statuses(second) == {"rest": "live", "graphql": "live", "git": "builtin"}
    assert len(await _import_jobs(ctx)) == 1, "a second run must enqueue nothing"
    by_role = {a.role: a for a in second.apis}
    assert (by_role["rest"].vendor, by_role["rest"].name, by_role["rest"].version) == (
        "github-com",
        "api-github-com",
        "1.1.4",
    )
    assert (by_role["graphql"].vendor, by_role["graphql"].name, by_role["graphql"].version) == (
        "github-com",
        "github-graphql",
        "1",
    )

    availability = await svc.availability(svc.get_profile("github"))
    assert all(a.live for a in availability)

    async with ctx.registry_db.session() as session:
        [graphql_api] = (
            (await session.execute(select(Api).where(Api.name == "github-graphql"))).scalars().all()
        )
        assert graphql_api.catalog_api_id == "github.com/github-graphql"
        revision = await session.get(ApiRevision, graphql_api.current_revision_id)
        assert revision is not None and revision.origin == RevisionOrigin.CLI_PROFILE

    resolved = await InProcessRegistryResolver(ctx.registry_db, ctx=ctx).resolve_operation(
        method="POST", url="https://api.github.com/graphql"
    )
    assert resolved is not None
    assert (resolved.api.vendor, resolved.api.name) == ("github-com", "github-graphql")


async def test_concurrent_provisioning_imports_the_shipped_spec_once(clean: Context) -> None:
    ctx = clean
    results = await asyncio.gather(
        CliProfileService(ctx).provision("github", _IDENTITY),
        CliProfileService(ctx).provision("github", _IDENTITY),
    )
    graphql = {next(a.status for a in r.apis if a.role == "graphql") for r in results}
    assert graphql <= {"imported", "live"}
    assert all(s != "failed" for r in results for s in _statuses(r).values())
    async with ctx.registry_db.session() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ApiRevision)
            .join(Api, ApiRevision.api_id == Api.id)
            .where(Api.name == "github-graphql")
        )
    assert count == 1


async def test_on_demand_provisioning_covers_a_profile_scoped_profile_whole(
    clean: Context,
) -> None:
    ctx = clean
    svc = CliProfileService(ctx)
    ctx.config.cli_facades = CliFacadesConfig()
    # Facade off: GitHub is profile_scoped, so one connect covers (and
    # provisions) every API of the profile all the same.
    result = await svc.ensure_for_catalog_api_id("github.com/github-graphql", _IDENTITY)
    assert result is not None and not result.vendor_enabled
    assert _statuses(result) == {"rest": "import_queued", "graphql": "imported", "git": "builtin"}

    ctx.config.cli_facades = CliFacadesConfig.model_validate(
        {"enabled": True, "vendors": {"github": {"enabled": True, "host": "github.jentic.test"}}}
    )
    # Facade on: the whole profile.
    result = await svc.ensure_for_catalog_api_id("github.com/github-git", _IDENTITY)
    assert result is not None
    assert _statuses(result) == {"rest": "import_queued", "graphql": "live", "git": "builtin"}
    assert await svc.ensure_for_catalog_api_id("stripe.com", _IDENTITY) is None


async def test_unknown_profile_is_refused(clean: Context) -> None:
    with pytest.raises(CliProfileNotFoundError, match="shipped profiles: cloudflare, github"):
        await CliProfileService(clean).provision("gitlab", _IDENTITY)


async def test_a_spec_missing_from_the_catalog_is_reported(clean: Context) -> None:
    ctx = clean
    async with ctx.registry_db.transaction() as session:
        await CatalogRepository.replace(
            session, source_url=ctx.config.catalog.manifest_url, entries=[]
        )
    result = await CliProfileService(ctx).provision("github", _IDENTITY)
    rest = next(a for a in result.apis if a.role == "rest")
    assert rest.status == "not_in_catalog"
    assert rest.detail is not None and "github.com/api.github.com" in rest.detail
