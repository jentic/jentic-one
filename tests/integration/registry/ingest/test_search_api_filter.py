"""Integration tests for the search ``apis`` filter identifier format (#1080).

The CLI (and every other surface) references APIs by the canonical
``vendor[/name[/version]]`` slug, but ``_resolve_api_filters`` historically
parsed only a colon-separated form — so every documented ``--api`` invocation
failed with 422 ``Unknown API filter``. These tests ingest two real specs and
assert both separator forms resolve, filters actually restrict results, and an
unknown filter still fails with a format hint.

Runs end-to-end through the real ``Ingestor`` and the real ``SearchService``.
"""

from __future__ import annotations

import pytest

from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.services.errors import InvalidApiFilterError
from jentic_one.registry.services.search_service import SearchService
from jentic_one.shared.config import SearchConfig
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


def _spec(*, vendor: str, name: str, version: str, marker: str) -> IngestSpecification:
    """A one-operation spec whose summary embeds a unique searchable ``marker``."""
    return IngestSpecification(
        api_identifier=ApiIdentifier(
            vendor=vendor,
            name=name,
            version=version,
            filename="spec.yaml",
        ),
        spec_type=SpecType.OPENAPI,
        content={
            "openapi": "3.1.0",
            "info": {"title": "API", "version": version},
            "paths": {
                "/things": {
                    "get": {
                        "operationId": f"list_{marker}",
                        "summary": f"List filterable {marker} things",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
        sha=f"sha-{marker}",
        origin="catalog",
    )


@pytest.fixture()
async def two_ingested_apis(ingest_context: Context, clean_registry: None) -> Context:
    """Two distinct APIs sharing a searchable marker, imported for real."""
    ingest_context.config.search = SearchConfig(enabled=True, search_enabled=True)
    ingestor = Ingestor(ingest_context)
    await ingestor.ingest(
        _spec(vendor="alpha-example-com", name="alpha-api", version="1.2.3", marker="alphamark"),
        created_by="usr_test",
    )
    await ingestor.ingest(
        _spec(vendor="beta-example-com", name="beta-api", version="2.0.0", marker="betamark"),
        created_by="usr_test",
    )
    return ingest_context


@pytest.mark.parametrize(
    "api_filter",
    [
        "alpha-example-com/alpha-api/1.2.3",  # canonical slug, what the CLI sends (#1080)
        "alpha-example-com/alpha-api",
        "alpha-example-com",
        "alpha-example-com:alpha-api:1.2.3",  # legacy colon form stays supported
    ],
)
async def test_search_api_filter_accepts_both_separator_forms(
    two_ingested_apis: Context, api_filter: str
) -> None:
    """Every documented filter shape resolves and restricts results to that API."""
    page = await SearchService(two_ingested_apis).search(
        query="filterable", limit=10, apis=[api_filter]
    )
    assert [hit.api.vendor for hit in page.data] == ["alpha-example-com"]


async def test_search_api_filter_restricts_across_apis(two_ingested_apis: Context) -> None:
    """Without a filter both APIs match; with one, only the filtered API does."""
    unfiltered = await SearchService(two_ingested_apis).search(query="filterable", limit=10)
    assert {hit.api.vendor for hit in unfiltered.data} == {
        "alpha-example-com",
        "beta-example-com",
    }

    filtered = await SearchService(two_ingested_apis).search(
        query="filterable", limit=10, apis=["beta-example-com/beta-api/2.0.0"]
    )
    assert {hit.api.vendor for hit in filtered.data} == {"beta-example-com"}


async def test_search_api_filter_unknown_names_expected_format(
    two_ingested_apis: Context,
) -> None:
    """An unresolvable filter still fails, and the error names the expected format."""
    with pytest.raises(InvalidApiFilterError, match=r"vendor\[/name\[/version\]\]"):
        await SearchService(two_ingested_apis).search(
            query="filterable", limit=10, apis=["nope-example-com/nope-api/9.9.9"]
        )
