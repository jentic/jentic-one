"""Unit tests for server host-set extraction (server-host change guard)."""

from __future__ import annotations

from jentic_one.registry.core.server_hosts import hosts_from_servers, hosts_from_spec
from jentic_one.registry.ingest.fetch import InlineSource, UrlSource
from jentic_one.registry.web.schemas.apis import ApiImportRequest


def test_api_and_operation_level_servers_are_collected() -> None:
    spec = {
        "servers": [{"url": "https://API.example.com/v1"}],
        "paths": {
            "/a": {
                "summary": "not an operation",
                "get": {"servers": [{"url": "https://ops.example.com:8443/x"}]},
            }
        },
    }
    assert hosts_from_spec(spec) == {"api.example.com", "ops.example.com:8443"}


def test_relative_servers_and_missing_servers_are_hostless() -> None:
    assert hosts_from_spec({"servers": [{"url": "/v1"}]}) == frozenset()
    assert hosts_from_spec({"paths": {}}) == frozenset()
    assert hosts_from_spec(None) == frozenset()


def test_scheme_and_path_changes_are_not_host_changes() -> None:
    old = hosts_from_spec({"servers": [{"url": "http://api.example.com/v1"}]})
    new = hosts_from_spec({"servers": [{"url": "https://api.example.com/v2"}]})
    assert old == new


def test_variables_expand_default_and_enum() -> None:
    spec = {
        "servers": [
            {
                "url": "https://{region}.example.com",
                "variables": {"region": {"default": "eu", "enum": ["eu", "us"]}},
            }
        ]
    }
    assert hosts_from_spec(spec) == {"eu.example.com", "us.example.com"}


def test_stored_and_spec_forms_agree() -> None:
    stored = hosts_from_servers([("https://{region}.example.com", {"region": ["eu", "us"]})])
    spec = hosts_from_spec(
        {
            "servers": [
                {
                    "url": "https://{region}.example.com",
                    "variables": {"region": {"default": "eu", "enum": ["us"]}},
                }
            ]
        }
    )
    assert stored == spec


def test_expansion_is_capped() -> None:
    values = [str(i) for i in range(10)]
    hosts = hosts_from_servers([("https://{a}.{b}.example.com", {"a": values, "b": values})])
    assert hosts == {"{a}.{b}.example.com"}


def test_approval_flag_is_not_client_settable() -> None:
    """Client import schemas drop ``host_change_approved``; only the server sets it."""
    body = ApiImportRequest.model_validate(
        {
            "sources": [
                {
                    "type": "url",
                    "url": "https://x.example.com/o.json",
                    "host_change_approved": True,
                },
                {
                    "type": "inline",
                    "content": "{}",
                    "filename": "o.json",
                    "host_change_approved": True,
                },
            ]
        }
    )
    for source in body.sources:
        assert "host_change_approved" not in source.model_dump()
    # The worker-side models default it to False when absent.
    assert UrlSource(type="url", url="https://x").host_change_approved is False
    assert InlineSource(type="inline", content="{}", filename="o").host_change_approved is False
