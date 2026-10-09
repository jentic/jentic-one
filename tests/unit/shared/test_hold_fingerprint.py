"""Unit tests for the held-execution fingerprint and terminal problem bodies."""

from __future__ import annotations

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


def _fp(body: bytes | None, **overrides: str) -> str:
    args = {"agent_id": "agnt_1", "credential_id": "cred_1", "method": "POST", "path": "/v1/x"}
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


def test_non_json_body_is_fingerprinted_verbatim() -> None:
    assert canonical_body(b"not json") == b"not json"
    assert canonical_body(None) == b""


def test_denied_and_expired_problems_are_permission_denied() -> None:
    denied = approval_denied_problem("exap_1", "too risky")
    assert denied["status"] == 403
    assert denied["type"] == APPROVAL_DENIED_TYPE
    assert denied["approval"] == {"id": "exap_1", "state": "denied"}
    assert "too risky" in denied["detail"]
    expired = approval_expired_problem("exap_1")
    assert expired["status"] == 403
    assert expired["type"] == APPROVAL_EXPIRED_TYPE
    assert approval_resume_failed_problem("exap_1")["approval"]["state"] == "approved"


def test_query_string_is_part_of_the_fingerprint() -> None:
    assert _fp(None, query="limit=10") != _fp(None, query="limit=20")
    assert _fp(None, query="limit=10") != _fp(None)
    assert _fp(None, query="a=1&b=") != _fp(None, query="a=1")


def test_query_parameter_order_does_not_split_identical_requests() -> None:
    assert _fp(None, query="a=1&b=2") == _fp(None, query="b=2&a=1")
    assert canonical_query("b=2&a=1&a=0") == "a=0&a=1&b=2"
