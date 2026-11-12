"""Unit tests for the idempotency request fingerprint (§07 §1).

The fingerprint is the stable identity of a request under a given
``Idempotency-Key``: the same logical request must hash identically (so a retry
*replays*), while any change to method, URL, toolkit, or body must change the
hash (so a key reuse for a *different* request is a 409 conflict). Volatile
headers are deliberately excluded.
"""

from __future__ import annotations

from jentic_one.broker.core.idempotency import fingerprint


def test_identical_requests_match() -> None:
    a = fingerprint("POST", "https://api.example.com/v1/charge", "stripe", b'{"amt":100}')
    b = fingerprint("POST", "https://api.example.com/v1/charge", "stripe", b'{"amt":100}')
    assert a == b


def test_method_is_case_insensitive() -> None:
    lower = fingerprint("post", "https://api.example.com/x", "tk", b"")
    upper = fingerprint("POST", "https://api.example.com/x", "tk", b"")
    assert lower == upper


def test_different_method_differs() -> None:
    get = fingerprint("GET", "https://api.example.com/x", "tk", b"")
    put = fingerprint("PUT", "https://api.example.com/x", "tk", b"")
    assert get != put


def test_different_url_differs() -> None:
    a = fingerprint("POST", "https://api.example.com/a", "tk", b"")
    b = fingerprint("POST", "https://api.example.com/b", "tk", b"")
    assert a != b


def test_different_toolkit_differs() -> None:
    a = fingerprint("POST", "https://api.example.com/x", "stripe", b"")
    b = fingerprint("POST", "https://api.example.com/x", "adyen", b"")
    assert a != b


def test_different_body_differs() -> None:
    a = fingerprint("POST", "https://api.example.com/x", "tk", b'{"amt":100}')
    b = fingerprint("POST", "https://api.example.com/x", "tk", b'{"amt":200}')
    assert a != b


def test_none_body_equals_empty_body() -> None:
    none = fingerprint("POST", "https://api.example.com/x", "tk", None)
    empty = fingerprint("POST", "https://api.example.com/x", "tk", b"")
    assert none == empty


def test_field_boundaries_are_unambiguous() -> None:
    # A NUL separator means concatenation can't collide: "a"+"b" must not equal
    # "ab"+"" across adjacent fields.
    a = fingerprint("GET", "https://x/ab", "", b"")
    b = fingerprint("GET", "https://x/a", "b", b"")
    assert a != b


def test_different_cookies_and_semantic_headers_differ() -> None:
    base = fingerprint(
        "POST",
        "https://api.example.com/v1/items",
        "cred_1",
        b'{"a":1}',
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        cookie="sid=one; theme=dark",
    )
    same_norm = fingerprint(
        "POST",
        "https://api.example.com/v1/items",
        "cred_1",
        b'{"a":1}',
        headers={
            "content-type": 'Application/JSON; CHARSET="UTF-8"',
            "accept": "application/json",
            "Authorization": "Bearer different-caller-token",
            "X-Request-Id": "req-999",
            "Traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        },
        cookie=" sid=one ;  theme=dark ",
    )
    assert base == same_norm

    diff_cookie = fingerprint(
        "POST",
        "https://api.example.com/v1/items",
        "cred_1",
        b'{"a":1}',
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        cookie="sid=two; theme=dark",
    )
    assert base != diff_cookie

    diff_if_match = fingerprint(
        "POST",
        "https://api.example.com/v1/items",
        "cred_1",
        b'{"a":1}',
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "If-Match": '"etag-2"',
        },
        cookie="sid=one; theme=dark",
    )
    assert base != diff_if_match

    diff_server_vars = fingerprint(
        "POST",
        "https://api.example.com/v1/items",
        "cred_1",
        b'{"a":1}',
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        cookie="sid=one; theme=dark",
        server_variables={"region": "eu"},
    )
    assert base != diff_server_vars
