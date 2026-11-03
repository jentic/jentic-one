"""Tests for API identifier resolution logic."""

from typing import Any

import pytest

from jentic_one.registry.ingest.api_identifier import resolve_api_identifier
from jentic_one.registry.ingest.errors import IngestStageError


def _info_block(
    title: str = "My API",
    version: str = "2.0.0",
    x_vendor: str | None = "acme-corp",
    contact_name: str | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {"title": title, "version": version}
    if x_vendor is not None:
        info["x-vendor"] = x_vendor
    if contact_name is not None:
        info["contact"] = {"name": contact_name}
    return {"info": info}


def test_explicit_overrides_win() -> None:
    content = _info_block(title="FromSpec", version="1.0", x_vendor="spec-vendor")
    result = resolve_api_identifier(
        content, vendor="override-vendor", name="override-name", version="9.9"
    )

    assert result.vendor == "override-vendor"
    assert result.name == "override-name"
    assert result.version == "9.9"


def test_x_vendor_preferred_over_contact_name() -> None:
    content = _info_block(x_vendor="x-vendor-value", contact_name="contact-name-value")
    result = resolve_api_identifier(content)

    assert result.vendor == "x-vendor-value"


def test_fallback_to_contact_name() -> None:
    content = _info_block(x_vendor=None, contact_name="Fallback Org")
    result = resolve_api_identifier(content)

    assert result.vendor == "fallback-org"


def test_title_maps_to_name() -> None:
    content = _info_block(title="My Cool API")
    result = resolve_api_identifier(content)

    assert result.name == "my-cool-api"


def test_version_from_info() -> None:
    content = _info_block(version="3.2.1")
    result = resolve_api_identifier(content)

    assert result.version == "3.2.1"


def test_missing_vendor_raises() -> None:
    content: dict[str, Any] = {"info": {"title": "Test", "version": "1.0"}}
    with pytest.raises(IngestStageError, match="vendor"):
        resolve_api_identifier(content)


def test_missing_name_raises() -> None:
    content: dict[str, Any] = {"info": {"x-vendor": "v", "version": "1.0"}}
    with pytest.raises(IngestStageError, match="name"):
        resolve_api_identifier(content)


def test_missing_version_raises() -> None:
    content: dict[str, Any] = {"info": {"title": "T", "x-vendor": "v"}}
    with pytest.raises(IngestStageError, match="version"):
        resolve_api_identifier(content)


def test_missing_multiple_fields_lists_all() -> None:
    content: dict[str, Any] = {"info": {}}
    with pytest.raises(IngestStageError, match=r"vendor.*name.*version"):
        resolve_api_identifier(content)


def test_empty_info_block_raises() -> None:
    content: dict[str, Any] = {}
    with pytest.raises(IngestStageError):
        resolve_api_identifier(content)


def test_spaces_replaced_with_hyphens() -> None:
    content = _info_block(title="My Cool API", x_vendor="Big Corp")
    result = resolve_api_identifier(content)

    assert result.vendor == "big-corp"
    assert result.name == "my-cool-api"


def test_special_chars_replaced() -> None:
    content = _info_block(title="API@v2!beta", x_vendor="org/dept")
    result = resolve_api_identifier(content)

    assert result.vendor == "org-dept"
    assert result.name == "api-v2-beta"


def test_length_capped_at_100() -> None:
    long_title = "a" * 200
    content = _info_block(title=long_title)
    result = resolve_api_identifier(content)

    assert len(result.name) == 100
    assert len(result.vendor) <= 100
