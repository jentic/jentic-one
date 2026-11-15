"""Integration tests: a spec whose path parameter starts with a digit ingests cleanly."""

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

_PATH = "/items/{1id}"


def _spec() -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(
            vendor="digit-param.example.com", name="items", version="1.0.0"
        ),
        sha="sha-digit-leading-param",
        content={
            "openapi": "3.0.3",
            "info": {"title": "Digit-leading param", "version": "1.0.0"},
            "servers": [{"url": "https://api.digit-param.example.com/v1"}],
            "paths": {
                _PATH: {
                    "get": {
                        "operationId": "getItemByDigitParam",
                        "parameters": [
                            {
                                "name": "1id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )


async def test_spec_with_digit_leading_path_param_ingests(
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
    match = re.fullmatch(rows[0].path_regex, "/v1/items/abc")
    assert match is not None
    assert match.groupdict() == {"p_1id": "abc"}
