"""Unit tests for ``apply_injection``, the outbound-auth step the sync router
and the async worker share (#1957).

Header creds overlay the caller's headers in any letter case, query creds join
the URL query, and cookie creds are **appended** to the caller's own cookies —
which always reach the upstream, in exactly one ``Cookie`` header.
"""

from __future__ import annotations

from jentic_one.shared.http.injection import apply_injection
from jentic_one.shared.jobs.protocols import InjectedAuth

_URL = "https://api.x.com/v1"


def _auth(
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    server_variables: dict[str, str] | None = None,
) -> InjectedAuth:
    return InjectedAuth(
        headers=headers or {},
        query_params=query or {},
        cookies=cookies or {},
        server_variables=server_variables,
    )


def test_header_creds_overlay_the_caller_headers_in_any_case() -> None:
    url, headers = apply_injection(
        {"accept": "application/json", "x-api-key": "caller"},
        _URL,
        _auth(headers={"X-Api-Key": "k"}),
        inbound_cookie=None,
    )
    assert url == _URL
    assert headers == {"accept": "application/json", "X-Api-Key": "k"}


def test_query_creds_join_the_url_query() -> None:
    url, headers = apply_injection(
        {}, f"{_URL}?page=2", _auth(query={"api_key": "k"}), inbound_cookie=None
    )
    assert url == f"{_URL}?page=2&api_key=k"
    assert headers == {}


def test_query_creds_start_a_query_when_there_is_none() -> None:
    url, _ = apply_injection({}, _URL, _auth(query={"api_key": "k"}), inbound_cookie=None)
    assert url == f"{_URL}?api_key=k"


def test_cookie_creds_append_to_the_inbound_cookie() -> None:
    _, headers = apply_injection(
        {}, _URL, _auth(cookies={"session": "s"}), inbound_cookie="forwarded=1"
    )
    assert headers == {"Cookie": "forwarded=1; session=s"}


def test_cookie_creds_alone_when_there_is_no_inbound_cookie() -> None:
    _, headers = apply_injection({}, _URL, _auth(cookies={"session": "s"}), inbound_cookie=None)
    assert headers == {"Cookie": "session=s"}


def test_inbound_cookie_is_kept_without_cookie_creds() -> None:
    _, headers = apply_injection({}, _URL, _auth(), inbound_cookie="sid=1")
    assert headers == {"Cookie": "sid=1"}


def test_cookie_already_in_headers_is_replaced_never_sent_twice() -> None:
    _, headers = apply_injection(
        {"cookie": "sid=1", "accept": "*/*"},
        _URL,
        _auth(cookies={"session": "s"}),
        inbound_cookie="sid=1",
    )
    assert headers == {"accept": "*/*", "Cookie": "sid=1; session=s"}


def test_no_cookie_at_all_sends_no_cookie_header() -> None:
    _, headers = apply_injection({"accept": "*/*"}, _URL, _auth(), inbound_cookie="  ")
    assert headers == {"accept": "*/*"}


def test_server_variables_then_defaults_fill_the_url_template() -> None:
    url, _ = apply_injection(
        {},
        "https://{region}.api.x.com/{version}",
        _auth(server_variables={"region": "eu"}),
        inbound_cookie=None,
        server_variable_defaults={"region": "us", "version": "v2"},
    )
    assert url == "https://eu.api.x.com/v2"


def test_inputs_are_not_mutated() -> None:
    inbound = {"cookie": "sid=1"}
    auth = _auth(headers={"X-Api-Key": "k"}, cookies={"session": "s"})
    apply_injection(inbound, _URL, auth, inbound_cookie="sid=1")
    assert inbound == {"cookie": "sid=1"}
    assert auth.headers == {"X-Api-Key": "k"}
