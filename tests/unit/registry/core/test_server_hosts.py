"""Unit tests for server host-set extraction (server-host change guard)."""

from __future__ import annotations

from jentic_one.registry.core.server_hosts import hosts_from_servers, hosts_from_spec, needs_review
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
    assert hosts_from_spec(spec) == {"https://api.example.com", "https://ops.example.com:8443"}


def test_relative_servers_and_missing_servers_are_hostless() -> None:
    assert hosts_from_spec({"servers": [{"url": "/v1"}]}) == frozenset()
    assert hosts_from_spec({"paths": {}}) == frozenset()
    assert hosts_from_spec(None) == frozenset()


def _servers(*urls: str) -> frozenset[str]:
    return hosts_from_spec({"servers": [{"url": u} for u in urls]})


def test_path_change_needs_no_review() -> None:
    old = _servers("https://api.example.com/v1")
    new = _servers("https://api.example.com/v2")
    assert old == new
    assert not needs_review(old, new)


def test_https_to_http_downgrade_needs_review() -> None:
    assert needs_review(_servers("https://api.example.com"), _servers("http://api.example.com"))
    # Adding a plaintext twin of an existing https host is a downgrade too.
    assert needs_review(
        _servers("https://api.example.com"),
        _servers("https://api.example.com", "http://api.example.com"),
    )
    # Protocol-relative servers can be reached over http.
    assert needs_review(_servers("https://api.example.com"), _servers("//api.example.com"))


def test_http_to_https_upgrade_needs_no_review() -> None:
    assert not needs_review(_servers("http://api.example.com"), _servers("https://api.example.com"))
    assert not needs_review(
        _servers("http://api.example.com", "https://api.example.com"),
        _servers("https://api.example.com"),
    )


def test_host_change_needs_review() -> None:
    assert needs_review(_servers("https://api.example.com"), _servers("https://evil.example.net"))
    assert needs_review(
        _servers("https://api.example.com"), _servers("https://api.example.com:8443")
    )
    # Going hostless still changes the set.
    assert needs_review(_servers("https://api.example.com"), _servers("/v1"))


def test_host_normalisation() -> None:
    canonical = _servers("https://api.example.com")
    for variant in (
        "HTTPS://API.Example.COM/v1",
        "https://api.example.com:443",
        "https://api.example.com./x",
        "https://user:pw@api.example.com",
        "https://api.example.com?q=1",
        "https://api.example.com#frag",
    ):
        assert _servers(variant) == canonical, variant
    assert _servers("http://api.example.com:80") == _servers("http://api.example.com")
    # A non-default port is significant, and :80 is not the https default.
    assert _servers("https://api.example.com:80") == {"https://api.example.com:80"}
    assert _servers("https://[::1]:443/x") == {"https://[::1]"}
    # Userinfo cannot smuggle a different host past the comparison.
    assert _servers("https://api.example.com@evil.example.net") == {"https://evil.example.net"}
    assert _servers("https://api.example.com\\@evil.example.net") == canonical


def test_relative_templates_are_not_a_bypass() -> None:
    # A template that could still supply a scheme and host is kept, opaquely.
    assert _servers("{base}/v1")
    assert needs_review(_servers("{base}/v1"), _servers("{other}/v1"))
    spec = {"servers": [{"url": "{base}/v1", "variables": {"base": {"default": "https://a.io"}}}]}
    assert hosts_from_spec(spec) == {"https://a.io"}


def test_variables_expand_default_and_enum() -> None:
    spec = {
        "servers": [
            {
                "url": "https://{region}.example.com",
                "variables": {"region": {"default": "eu", "enum": ["eu", "us"]}},
            }
        ]
    }
    assert hosts_from_spec(spec) == {"https://eu.example.com", "https://us.example.com"}


def test_path_only_variables_are_not_expanded() -> None:
    spec = {
        "servers": [
            {
                "url": "https://api.example.com/{v}",
                "variables": {"v": {"default": "v1", "enum": [str(i) for i in range(100)]}},
            }
        ]
    }
    assert hosts_from_spec(spec) == {"https://api.example.com"}


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


def test_expansion_is_capped_and_value_sensitive() -> None:
    values = [str(i) for i in range(10)]
    capped = hosts_from_servers([("https://{a}.{b}.example.com", {"a": values, "b": values})])
    assert len(capped) == 1
    # Past the cap, adding a value still changes the set: it can never hide a change.
    more = hosts_from_servers(
        [("https://{a}.{b}.example.com", {"a": [*values, "evil"], "b": values})]
    )
    assert needs_review(capped, more)
    # The same template and values compare equal (no spurious hold on a no-op).
    again = hosts_from_servers(
        [("https://{a}.{b}.example.com", {"a": list(reversed(values)), "b": values})]
    )
    assert not needs_review(capped, again)
    # A capped template without a pinned https scheme counts as plaintext-reachable.
    open_scheme = hosts_from_servers(
        [("{s}://{a}.{b}.example.com", {"a": values, "b": values, "s": ["https"]})]
    )
    assert any(o.startswith("http://") for o in open_scheme)


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
