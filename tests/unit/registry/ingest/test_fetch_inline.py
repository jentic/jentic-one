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
# ingest pipeline's later JSON serialization (spec storage, operation
# extraction) rejects.
_SPEC_YAML_BARE_DATES = """\
openapi: "3.1.0"
info:
  title: Dated API
  version: 2022-01-16
  x-vendor: acme
  x-released: 2015-02-22
  x-audited: 2001-12-14t21:59:43.10-05:00
  x-legacy: 2001-12-14 21:59:43.10 -5
paths: {}
"""

# Explicitly tagged !!binary (bytes) and !!set (set) are the other SafeLoader
# outputs that json.dumps rejects — same dead-letter failure mode as #979.
_SPEC_YAML_EXOTIC_TAGS = """\
openapi: "3.1.0"
info:
  title: Exotic API
  version: "1.0.0"
  x-vendor: acme
  x-blob: !!binary aGVsbG8=
  x-flags: !!set
    alpha:
    beta:
paths: {}
"""

# Non-finite float scalars (issue #984): json.dumps emits float('nan')/
# float('inf') as the non-standard NaN/Infinity tokens (it does not raise),
# which JSON parsers and Postgres JSONB reject — dead-lettering the import at
# the JSONB write. Finite floats must keep parsing as numbers.
_SPEC_YAML_NONFINITE_FLOATS = """\
openapi: "3.1.0"
info:
  title: Floaty API
  version: "1.0.0"
  x-vendor: acme
  x-nan: .nan
  x-inf: .inf
  x-neg-inf: -.INF
  x-finite: 1.5
  x-exponent: 2.5e+3
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

    Regression for #979 — see the _SPEC_YAML_BARE_DATES comment for the failure
    mode this pins.
    """
    result = await load_specification(_make_inline(_SPEC_YAML_BARE_DATES, filename="spec.yaml"))

    assert result.content is not None
    info = result.content["info"]
    # Verbatim scalar text — not date/datetime, and not a reformatted ISO string.
    assert info["version"] == "2022-01-16"
    assert info["x-released"] == "2015-02-22"
    assert info["x-audited"] == "2001-12-14t21:59:43.10-05:00"
    # YAML 1.1 canonical space-separated form with a bare-digit offset: the
    # case where a reformat-based fix (isoformat) would diverge from source.
    assert info["x-legacy"] == "2001-12-14 21:59:43.10 -5"
    json.dumps(result.content)  # the property the pipeline actually needs
    # The date-typed version also drives API identity; it must slug cleanly.
    assert result.api_identifier.version == "2022-01-16"


@pytest.mark.asyncio
async def test_yaml_binary_and_set_tags_stay_json_serializable() -> None:
    """Explicit !!binary/!!set tags must not smuggle bytes/set past the parser."""
    result = await load_specification(_make_inline(_SPEC_YAML_EXOTIC_TAGS, filename="spec.yaml"))

    assert result.content is not None
    info = result.content["info"]
    assert info["x-blob"] == "aGVsbG8="  # verbatim base64 text, not bytes
    assert info["x-flags"] == ["alpha", "beta"]  # key list, not a set
    json.dumps(result.content)


@pytest.mark.asyncio
async def test_yaml_nonfinite_floats_stay_json_serializable_strings() -> None:
    """Non-finite float scalars parse as verbatim strings; finite floats as numbers.

    Regression for #984 — see the _SPEC_YAML_NONFINITE_FLOATS comment for the
    failure mode this pins.
    """
    result = await load_specification(
        _make_inline(_SPEC_YAML_NONFINITE_FLOATS, filename="spec.yaml")
    )

    assert result.content is not None
    info = result.content["info"]
    # Verbatim scalar text — case preserved, no normalization to "Infinity".
    assert info["x-nan"] == ".nan"
    assert info["x-inf"] == ".inf"
    assert info["x-neg-inf"] == "-.INF"
    # Finite floats are untouched — still numbers, not strings. (The exponent
    # form needs the sign — PyYAML's YAML 1.1 resolver treats signless "2.5e3"
    # as a plain string, with or without this fix.)
    assert info["x-finite"] == 1.5
    assert info["x-exponent"] == 2500.0
    # Strict-JSON serializable: allow_nan=False raises if a non-finite leaked.
    json.dumps(result.content, allow_nan=False)


@pytest.mark.asyncio
async def test_json_nonfinite_tokens_stay_json_serializable_strings() -> None:
    """The JSON path has the same gap: json.loads accepts NaN/Infinity tokens.

    Python's json.loads is laxer than RFC 8259 and produces non-finite floats
    for them by default — same dead-letter failure as the YAML case (#984).
    """
    spec = (
        '{"openapi":"3.1.0","info":{"title":"Floaty JSON","version":"1.0.0",'
        '"x-vendor":"acme","x-nan":NaN,"x-inf":Infinity,"x-neg-inf":-Infinity,'
        '"x-finite":1.5},"paths":{}}'
    )
    result = await load_specification(_make_inline(spec, filename="spec.json"))

    assert result.content is not None
    info = result.content["info"]
    assert info["x-nan"] == "NaN"
    assert info["x-inf"] == "Infinity"
    assert info["x-neg-inf"] == "-Infinity"
    assert info["x-finite"] == 1.5
    json.dumps(result.content, allow_nan=False)


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


@pytest.mark.parametrize(
    "scalar",
    [
        "!!float abc",  # ValueError from float()
        "!!float ''",  # IndexError from value[0]
        "!!int abc",  # ValueError from int()
        "!!bool abc",  # KeyError from the bool lookup table
    ],
)
@pytest.mark.asyncio
async def test_malformed_tagged_scalars_raise_ingest_stage_error(scalar: str) -> None:
    """Malformed explicitly-tagged scalars must fail as a clean parse error.

    Regression for #988: PyYAML's stock constructors leak raw builtin
    exceptions (ValueError/IndexError/KeyError) for these instead of a
    YAMLError, which used to dead-letter the import with an internal
    traceback rather than an operator-facing message.
    """
    spec = f'openapi: "3.1.0"\ninfo:\n  title: Bad API\n  version: "1.0.0"\n  x-bad: {scalar}\n'
    with pytest.raises(IngestStageError, match="failed to parse"):
        await load_specification(_make_inline(spec, filename="spec.yaml"))


@pytest.mark.parametrize(
    # Deep nesting exhausts the recursion limit inside both parsers —
    # RecursionError is neither a YAMLError nor a ValueError, so without the
    # _load_yaml/_load_json normalization it leaked raw through both parse
    # orders (found by the #989 review).
    "content, filename",
    [
        ('{"a":' * 10_000 + "1" + "}" * 10_000, "spec.json"),  # JSON-first order
        ("[" * 10_000, "spec.yaml"),  # YAML-first order
    ],
)
@pytest.mark.asyncio
async def test_deeply_nested_content_raises_ingest_stage_error(content: str, filename: str) -> None:
    with pytest.raises(IngestStageError, match="failed to parse"):
        await load_specification(_make_inline(content, filename=filename))


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
