"""Spike (not for merge): can the registry route the calls a broker-side CLI makes?

Ingests minimal specs through the real ``Ingestor`` and resolves CLI-shaped
requests through the broker's real resolver: GitHub GraphQL next to GitHub
REST on the same host, and an S3 bucket-style host template.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from jentic_one.broker.services.discovery import discover
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionSourceType
from jentic_one.wiring import InProcessRegistryResolver

pytestmark = pytest.mark.integration


def _spec(
    vendor: str, name: str, servers: list[dict[str, Any]], paths: dict[str, Any]
) -> IngestSpecification:
    return IngestSpecification(
        api_identifier=ApiIdentifier(vendor=vendor, name=name, version="1"),
        spec_type=SpecType.OPENAPI,
        content={
            "openapi": "3.0.3",
            "info": {"title": f"{vendor} {name}", "version": "1"},
            "servers": servers,
            "paths": paths,
        },
        sha=f"sha-{vendor}-{name}",
        source_type=ApiRevisionSourceType.INLINE,
        source_filename=f"{name}.json",
    )


_OK = {"responses": {"200": {"description": "OK"}}}
_IDENTITY = Identity(sub="usr_spike", email="spike@example.com")


async def _ingest_live(ctx: Context, spec: IngestSpecification) -> None:
    result = await Ingestor(ctx).ingest(spec, created_by="usr_spike")
    ident = spec.api_identifier
    await RevisionService(ctx).promote(
        ident.vendor, ident.name, ident.version, str(result.revision_id), identity=_IDENTITY
    )


async def test_github_graphql_and_rest_coexist_on_one_host(
    ingest_context: Context, clean_registry: None
) -> None:
    await _ingest_live(
        ingest_context,
        _spec(
            "github.com",
            "rest",
            [{"url": "https://api.github.com"}],
            {"/repos/{owner}/{repo}/pulls": {"get": {"operationId": "pulls/list", **_OK}}},
        ),
    )
    await _ingest_live(
        ingest_context,
        _spec(
            "github.com",
            "graphql",
            [{"url": "https://api.github.com"}],
            {"/graphql": {"post": {"operationId": "graphql", **_OK}}},
        ),
    )
    resolver = InProcessRegistryResolver(ingest_context.registry_db)

    gql = await discover(resolver, method="POST", url="https://api.github.com/graphql")
    rest = await discover(
        resolver, method="GET", url="https://api.github.com/repos/cli/cli/pulls?per_page=10"
    )
    miss = await discover(resolver, method="GET", url="https://api.github.com/repos/cli/cli/readme")

    print("\nSPIKE graphql ->", gql and (gql.api.name, gql.operation.path, gql.operation.method))
    print("SPIKE rest    ->", rest and (rest.api.name, rest.operation.path, rest.path_params))
    print("SPIKE readme  ->", miss)
    assert gql is not None and gql.api.name == "graphql"
    assert rest is not None and rest.api.name == "rest"
    assert miss is None


async def test_s3_bucket_host_template_resolves(
    ingest_context: Context, clean_registry: None
) -> None:
    await _ingest_live(
        ingest_context,
        _spec(
            "amazonaws.com",
            "s3",
            [
                {
                    "url": "https://{bucket}.s3.{region}.amazonaws.com",
                    "variables": {
                        "bucket": {"default": "example"},
                        "region": {"default": "eu-west-1"},
                    },
                },
                {
                    "url": "https://s3.{region}.amazonaws.com",
                    "variables": {"region": {"default": "eu-west-1"}},
                },
            ],
            {
                "/": {"get": {"operationId": "ListObjectsV2", **_OK}},
                "/{Bucket}": {"get": {"operationId": "ListObjectsV2PathStyle", **_OK}},
            },
        ),
    )
    resolver = InProcessRegistryResolver(ingest_context.registry_db)

    vhost = await discover(
        resolver, method="GET", url="https://my-bucket.s3.us-east-1.amazonaws.com/?list-type=2"
    )
    path = await discover(
        resolver, method="GET", url="https://s3.us-east-1.amazonaws.com/my-bucket?list-type=2"
    )

    print(
        "\nSPIKE s3 vhost ->",
        vhost and (vhost.operation.path, vhost.server_variables, vhost.server_variables_unresolved),
    )
    print(
        "SPIKE s3 path  ->", path and (path.operation.path, path.path_params, path.server_variables)
    )


async def test_s3_index_rows(ingest_context: Context, clean_registry: None) -> None:
    await _ingest_live(
        ingest_context,
        _spec(
            "amazonaws.com",
            "s3",
            [
                {
                    "url": "https://{bucket}.s3.{region}.amazonaws.com",
                    "variables": {
                        "bucket": {"default": "example"},
                        "region": {"default": "eu-west-1"},
                    },
                },
                {
                    "url": "https://s3.{region}.amazonaws.com",
                    "variables": {"region": {"default": "eu-west-1"}},
                },
                {"url": "https://s3.amazonaws.com"},
            ],
            {"/{Bucket}": {"get": {"operationId": "ListObjectsV2PathStyle", **_OK}}},
        ),
    )
    async with ingest_context.registry_db.session() as session:
        rows = (await session.execute(select(OperationURLIndex))).scalars().all()
        for r in rows:
            print(
                "\nSPIKE index:",
                {c: getattr(r, c, None) for c in ("method", "host", "host_regex", "path_regex")},
            )
    resolver = InProcessRegistryResolver(ingest_context.registry_db)
    for url in (
        "https://s3.amazonaws.com/my-bucket",
        "https://s3.eu-west-1.amazonaws.com/my-bucket",
        "https://s3.us-east-1.amazonaws.com/my-bucket",
        "https://example.s3.eu-west-1.amazonaws.com/x",
    ):
        r = await discover(resolver, method="GET", url=url)
        print(
            "SPIKE",
            url,
            "->",
            r
            and (
                r.operation.path,
                r.path_params,
                r.server_variables,
                r.server_variables_unresolved,
            ),
        )


async def test_s3_region_enum(ingest_context: Context, clean_registry: None) -> None:
    regions = ["eu-west-1", "us-east-1", "ap-southeast-2"]
    await _ingest_live(
        ingest_context,
        _spec(
            "amazonaws.com",
            "s3",
            [
                {
                    "url": "https://s3.{region}.amazonaws.com",
                    "variables": {"region": {"default": "eu-west-1", "enum": regions}},
                }
            ],
            {"/{Bucket}": {"get": {"operationId": "ListObjectsV2PathStyle", **_OK}}},
        ),
    )
    resolver = InProcessRegistryResolver(ingest_context.registry_db)
    for region in [*regions, "sa-east-1"]:
        r = await discover(
            resolver, method="GET", url=f"https://s3.{region}.amazonaws.com/my-bucket"
        )
        print("\nSPIKE enum", region, "->", r and r.server_variables)
