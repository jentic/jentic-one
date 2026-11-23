"""Integration tests: a spec whose path repeats a parameter name ingests and resolves.

Pins #1980: a path such as Cloudflare's
``/accounts/{account_id}/addressing/address_maps/{address_map_id}/accounts/{account_id}``
must not fail ``BuildURLIndexStage``; the indexed row matches a request whose
repeated segments agree and reports the parameter once.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_PATH = "/accounts/{account_id}/addressing/address_maps/{address_map_id}/accounts/{account_id}"


def _spec() -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="cloudflare.example.com", name="main", version="4.0.0"),
        sha="sha-repeated-param",
        content={
            "openapi": "3.0.3",
            "info": {"title": "Repeated param", "version": "4.0.0"},
            "servers": [{"url": "https://api.cloudflare.example.com/client/v4"}],
            "paths": {
                _PATH: {
                    "put": {
                        "operationId": "addAccountMembership",
                        "parameters": [
                            {
                                "name": "account_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "address_map_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )


async def test_spec_with_repeated_path_param_ingests(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    result = await Ingestor(ingest_context).ingest(_spec(), created_by="usr")
    assert result.operation_count == 1

    async with registry_db.session() as session:
        rows = list(
            (
                await session.execute(
                    select(OperationURLIndex).where(
                        OperationURLIndex.revision_id == result.revision_id
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1
    match = re.fullmatch(
        rows[0].path_regex,
        "/client/v4/accounts/acc1/addressing/address_maps/map1/accounts/acc1",
    )
    assert match is not None
    assert match.groupdict() == {"account_id": "acc1", "address_map_id": "map1"}
    assert (
        re.fullmatch(
            rows[0].path_regex,
            "/client/v4/accounts/acc1/addressing/address_maps/map1/accounts/acc2",
        )
        is None
    )
