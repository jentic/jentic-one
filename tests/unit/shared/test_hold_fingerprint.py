"""Unit tests for the held-execution fingerprint and terminal problem bodies."""

from __future__ import annotations

from typing import Any

from jentic_one.shared.jobs.hold import (
    APPROVAL_DENIED_TYPE,
    APPROVAL_EXPIRED_TYPE,
    approval_denied_problem,
    approval_expired_problem,
    approval_resume_failed_problem,
    canonical_body,
    canonical_query,
    compute_execution_fingerprint,
)


def _fp(body: bytes | None, **overrides: Any) -> str:
    args: dict[str, Any] = {
        "agent_id": "agnt_1",
        "credential_id": "cred_1",
        "method": "POST",
        "path": "/v1/x",
    }
    args.update(overrides)
    return compute_execution_fingerprint(body=body, **args)


def test_json_key_order_and_whitespace_do_not_split_identical_requests() -> None:
    assert _fp(b'{"a": 1, "b": [1, 2]}') == _fp(b'{"b":[1,2],"a":1}')


def test_body_is_part_of_the_fingerprint() -> None:
    assert _fp(b'{"a": 1}') != _fp(b'{"a": 2}')
    assert _fp(None) != _fp(b'{"a": 1}')


def test_identity_fields_are_part_of_the_fingerprint() -> None:
    base = _fp(None)
    assert base != _fp(None, agent_id="agnt_2")
    assert base != _fp(None, credential_id="cred_2")
    assert base != _fp(None, path="/v1/y")
    assert base == _fp(None, method="post")
    assert base != _fp(None, upstream_url="https://eu.api.example.com/v1/x")


def test_non_json_body_is_fingerprinted_verbatim() -> None:
    assert canonical_body(b"not json") == b"not json"
    assert canonical_body(None) == b""


def test_denied_and_expired_problems_are_permission_denied() -> None:
    denied = approval_denied_problem("held_1", "too risky")
    assert denied["status"] == 403
    assert denied["type"] == APPROVAL_DENIED_TYPE
    assert denied["held_execution"] == {"id": "held_1", "state": "denied"}
    assert "too risky" in denied["detail"]
    expired = approval_expired_problem("held_1")
    assert expired["status"] == 403
    assert expired["type"] == APPROVAL_EXPIRED_TYPE
    assert approval_resume_failed_problem("held_1")["held_execution"]["state"] == "approved"


def test_query_string_is_part_of_the_fingerprint() -> None:
    assert _fp(None, query="limit=10") != _fp(None, query="limit=20")
    assert _fp(None, query="limit=10") != _fp(None)
    assert _fp(None, query="a=1&b=") != _fp(None, query="a=1")


def test_query_parameter_order_does_not_split_identical_requests() -> None:
    assert _fp(None, query="a=1&b=2") == _fp(None, query="b=2&a=1")
    assert canonical_query("b=2&a=1&a=0") == "a=0&a=1&b=2"


def test_cookie_and_semantic_headers_are_part_of_the_hold_fingerprint() -> None:
    base = compute_execution_fingerprint(
        agent_id="agnt_1",
        credential_id="cred_1",
        method="POST",
        path="/v1/x",
        body=b'{"a":1}',
        headers={"Content-Type": "application/json; charset=utf-8", "Notion-Version": "2022-06-28"},
        cookie="sid=alpha; mode=strict",
    )
    same_with_volatile = compute_execution_fingerprint(
        agent_id="agnt_1",
        credential_id="cred_1",
        method="POST",
        path="/v1/x",
        body=b'{"a": 1}',
        headers={
            "content-type": 'Application/JSON; CHARSET="UTF-8"',
            "notion-version": "2022-06-28",
            "Authorization": "Bearer other-token",
            "X-Request-Id": "req-2",
            "Traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        },
        cookie=" sid=alpha ; mode=strict ",
    )
    assert base == same_with_volatile

    diff_cookie = compute_execution_fingerprint(
        agent_id="agnt_1",
        credential_id="cred_1",
        method="POST",
        path="/v1/x",
        body=b'{"a":1}',
        headers={"Content-Type": "application/json; charset=utf-8", "Notion-Version": "2022-06-28"},
        cookie="sid=beta; mode=strict",
    )
    assert base != diff_cookie

    diff_header = compute_execution_fingerprint(
        agent_id="agnt_1",
        credential_id="cred_1",
        method="POST",
        path="/v1/x",
        body=b'{"a":1}',
        headers={"Content-Type": "application/json; charset=utf-8", "Notion-Version": "2025-01-01"},
        cookie="sid=alpha; mode=strict",
    )
    assert base != diff_header


def test_upstream_url_query_parameter_order_does_not_split_identical_requests() -> None:
    first = _fp(None, upstream_url="https://api.example.com/v1/x?a=1&b=2")
    reordered = _fp(None, upstream_url="https://api.example.com/v1/x?b=2&a=1")
    different = _fp(None, upstream_url="https://api.example.com/v1/x?a=1&b=3")

    assert first == reordered
    assert first != different


def test_response_representation_negotiation_is_part_of_the_hold_fingerprint() -> None:
    implicit_identity = _fp(None, headers={})
    identity = _fp(None, headers={"Accept-Encoding": "identity"})
    uppercase_identity = _fp(None, headers={"Accept-Encoding": "IDENTITY"})
    gzip = _fp(None, headers={"Accept-Encoding": "gzip"})

    assert identity == implicit_identity
    assert uppercase_identity == implicit_identity
    assert gzip != implicit_identity
