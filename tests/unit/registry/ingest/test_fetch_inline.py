"""Tests for inline spec loading via load_specification."""

import hashlib
import json
from typing import Any

import pytest
import yaml

from jentic_one.registry.ingest.exc import IngestStageError
from jentic_one.registry.ingest.fetch import InlineSource, load_specification
from jentic_one.shared.config import IngestConfig


def _make_inline(content: str, filename: str = "openapi.yaml", **kwargs: Any) -> InlineSource:
    return InlineSource(type="inline", content=content, filename=filename, **kwargs)


_SPEC_JSON = '{"openapi":"3.1.0","info":{"title":"Pet Store","version":"1.0.0","x-vendor":"acme"}}'

_SPEC_YAML = """\
openapi: "3.1.0"
info:
  title: Pet Store
  version: "1.0.0"
  x-vendor: acme
"""

# Real-world specs are full of unquoted ISO date scalars (issue #979): stock
# yaml.safe_load turns these into datetime.date/datetime.datetime, which the
# ingest pipeline's JSON serialization then rejects.
_SPEC_YAML_BARE_DATES = """\
openapi: "3.1.0"
info:
  title: Dated API
  version: 2022-01-16
  x-vendor: acme
  x-released: 2015-02-22
  x-audited: 2001-12-14t21:59:43.10-05:00
paths: {}
"""


@pytest.mark.asyncio
async def test_inline_json_produces_valid_specification() -> None:
    result = await load_specification(_make_inline(_SPEC_JSON, filename="spec.json"))

    assert result.spec_type == "openapi"
    assert result.api_identifier.vendor == "acme"
    assert result.api_identifier.name == "pet-store"
    assert result.api_identifier.version == "1.0.0"
    assert result.sha == hashlib.sha256(_SPEC_JSON.encode()).hexdigest()
    assert result.source_type == "inline"
    assert result.source_filename == "spec.json"
    assert result.content is not None
    assert result.content["openapi"] == "3.1.0"


@pytest.mark.asyncio
async def test_inline_yaml_produces_valid_specification() -> None:
    result = await load_specification(_make_inline(_SPEC_YAML, filename="spec.yaml"))

    assert result.spec_type == "openapi"
    assert result.api_identifier.vendor == "acme"
    assert result.api_identifier.name == "pet-store"
    assert result.api_identifier.version == "1.0.0"
    assert result.sha == hashlib.sha256(_SPEC_YAML.encode()).hexdigest()
    assert result.source_type == "inline"


@pytest.mark.asyncio
async def test_yaml_bare_date_scalars_stay_json_serializable_strings() -> None:
    """Bare YAML dates must parse as their literal strings, not datetime objects.

    Regression for #979: yaml.safe_load resolves unquoted ISO dates to
    datetime.date/datetime, which every downstream json.dumps / JSONB write
    then rejects — dead-lettering the whole import.
    """
    result = await load_specification(_make_inline(_SPEC_YAML_BARE_DATES, filename="spec.yaml"))

    assert result.content is not None
    info = result.content["info"]
    # Verbatim scalar text — not date/datetime, and not a reformatted ISO string.
    assert info["version"] == "2022-01-16"
    assert info["x-released"] == "2015-02-22"
    assert info["x-audited"] == "2001-12-14t21:59:43.10-05:00"
    json.dumps(result.content)  # the property the pipeline actually needs
    # The date-typed version also drives API identity; it must slug cleanly.
    assert result.api_identifier.version == "2022-01-16"


@pytest.mark.asyncio
async def test_catalog_api_id_carried_verbatim() -> None:
    """The catalog slug survives loading untouched (#910) — unlike the same
    value passed as api_name, which identity resolution slugifies. The
    separable `domain/sub-api` shape is what display surfaces need."""
    result = await load_specification(
        _make_inline(_SPEC_JSON, filename="spec.json", catalog_api_id="acme.com/pet_store")
    )
    assert result.catalog_api_id == "acme.com/pet_store"


@pytest.mark.asyncio
async def test_catalog_api_id_defaults_to_none() -> None:
    """A genuine paste has no catalog identity."""
    result = await load_specification(_make_inline(_SPEC_JSON, filename="spec.json"))
    assert result.catalog_api_id is None


@pytest.mark.asyncio
async def test_invalid_content_raises_ingest_stage_error() -> None:
    with pytest.raises(IngestStageError, match="must be a mapping"):
        await load_specification(_make_inline("<<<not valid>>>"))


@pytest.mark.asyncio
async def test_unparseable_content_raises_ingest_stage_error() -> None:
    with pytest.raises(IngestStageError, match="failed to parse"):
        await load_specification(_make_inline("{invalid json: [}", filename="spec.json"))


@pytest.mark.asyncio
async def test_non_dict_content_raises_ingest_stage_error() -> None:
    yaml_list = yaml.dump(["item1", "item2"])
    with pytest.raises(IngestStageError, match="must be a mapping"):
        await load_specification(_make_inline(yaml_list, filename="list.yaml"))


@pytest.mark.asyncio
async def test_arazzo_content_raises_ingest_stage_error() -> None:
    arazzo_spec = '{"arazzo":"1.0.0","info":{"title":"Test","version":"1.0"}}'
    with pytest.raises(IngestStageError, match="arazzo specifications are not supported"):
        await load_specification(_make_inline(arazzo_spec, filename="spec.json"))


@pytest.mark.asyncio
async def test_empty_content_raises_ingest_stage_error() -> None:
    with pytest.raises(IngestStageError, match="empty"):
        await load_specification(_make_inline(""))


@pytest.mark.asyncio
async def test_whitespace_only_content_raises_ingest_stage_error() -> None:
    with pytest.raises(IngestStageError, match="empty"):
        await load_specification(_make_inline("   \n  "))


@pytest.mark.asyncio
async def test_inline_content_over_size_limit_is_rejected() -> None:
    """Inline content bypasses the URL-fetch size checks, so it enforces its own cap.

    Regression for a DoS gap where a huge inline import (e.g. a materialized overlay)
    was ingested without any size bound.
    """
    cfg = IngestConfig(max_spec_bytes=100)
    oversized = _SPEC_JSON + " " * 200  # valid JSON, > 100 bytes
    with pytest.raises(IngestStageError, match="size limit"):
        await load_specification(_make_inline(oversized, filename="spec.json"), config=cfg)
