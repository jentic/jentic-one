"""Execute/inspect target parsing — the method gate both parsers share.

``parse_method_path`` (broker-relative ``METHOD:/path``) and
``_parse_method_url`` (absolute ``METHOD:https://…``) accept exactly the method
set OpenAPI ingestion does, TRACE included — so execute can refuse a TRACE
target with a coded error instead of misreading it as an opaque operation id.
Anything else is not a METHOD form at all (the caller then treats the target
as an opaque id), mirroring the Go ``parseMethodURL`` / ``ParseMethodPath``.
"""

from __future__ import annotations

import pytest

from jentic_one.mcp.execute import parse_method_path
from jentic_one.mcp.tools import _parse_method_url

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE")


@pytest.mark.parametrize("method", _METHODS)
def test_method_path_accepts_every_ingestible_method(method: str) -> None:
    assert parse_method_path(f"{method.lower()}:/v1/things") == (method, "/v1/things")


@pytest.mark.parametrize("method", _METHODS)
def test_method_url_accepts_every_ingestible_method(method: str) -> None:
    url = "https://api.example.com/v1/things"
    assert _parse_method_url(f"{method}:{url}") == (method, url)
    assert _parse_method_url(f"{method} {url}") == (method, url)


@pytest.mark.parametrize("target", ["FOO:/v1/things", "CONNECT:/v1/things"])
def test_method_path_rejects_unknown_methods(target: str) -> None:
    assert parse_method_path(target) == ("", "")


@pytest.mark.parametrize(
    "target",
    ["FOO:https://api.example.com/x", "CONNECT https://api.example.com/x"],
)
def test_method_url_rejects_unknown_methods(target: str) -> None:
    assert _parse_method_url(target) is None


@pytest.mark.parametrize("target", ["op_abc123", "GET:/relative", "GET:ftp://x"])
def test_method_url_rejects_non_absolute_or_bare_ids(target: str) -> None:
    """A bare registry id or a non-http(s) target is not a METHOD:url pair."""
    assert _parse_method_url(target) is None
