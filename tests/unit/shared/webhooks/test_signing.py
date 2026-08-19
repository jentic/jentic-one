"""Unit tests for webhook signing.

These cover the security-critical core of the outbound webhook feature. The
emphasis is on the properties that matter: the byte-exactness of the signed
content, determinism, and that the produced signature changes when the id,
timestamp, secret or body change.
"""

from __future__ import annotations

import pytest

from jentic_one.shared.webhooks.signing import (
    HEADER_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    SCHEME,
    compute_signature,
    hash_secret,
    sign_payload,
)

SECRET = "whsec_test_secret_value"  # pragma: allowlist secret
MESSAGE_ID = "whev_6a7d99aa957c0420bd874d56"
BODY = b'{"type":"charge.dispute.created","amount":420000}'
NOW = 1_760_000_000


def _sign(secret: str = SECRET, *, body: bytes = BODY, timestamp: int = NOW) -> dict[str, str]:
    return sign_payload(secret, MESSAGE_ID, body, timestamp=timestamp).as_dict()


def _expected(headers: dict[str, str], *, secret: str = SECRET, body: bytes = BODY) -> str:
    return f"{SCHEME}," + compute_signature(
        secret, headers[HEADER_ID], headers[HEADER_TIMESTAMP], body
    )


# --- shape --------------------------------------------------------------------


def test_header_names_are_standard_webhooks() -> None:
    headers = _sign()
    assert set(headers) == {HEADER_ID, HEADER_TIMESTAMP, HEADER_SIGNATURE}
    assert headers[HEADER_SIGNATURE].startswith("v1,")


def test_signed_payload_matches_recomputed_signature() -> None:
    headers = _sign()
    assert headers[HEADER_SIGNATURE] == _expected(headers)
    assert headers[HEADER_ID] == MESSAGE_ID
    assert headers[HEADER_TIMESTAMP] == str(NOW)


def test_signature_is_deterministic() -> None:
    first = compute_signature(SECRET, MESSAGE_ID, str(NOW), BODY)
    second = compute_signature(SECRET, MESSAGE_ID, str(NOW), BODY)
    assert first == second


# --- the signed content covers every field -----------------------------------


def test_modified_body_changes_the_signature() -> None:
    headers = _sign()
    tampered = b'{"type":"charge.dispute.created","amount":1}'
    assert headers[HEADER_SIGNATURE] != _expected(headers, body=tampered)


def test_wrong_secret_changes_the_signature() -> None:
    headers = _sign()
    assert headers[HEADER_SIGNATURE] != _expected(headers, secret="whsec_a_different_secret")


def test_message_id_is_inside_the_signed_content() -> None:
    a = compute_signature(SECRET, "whev_aaaa", str(NOW), BODY)
    b = compute_signature(SECRET, "whev_bbbb", str(NOW), BODY)
    assert a != b


def test_timestamp_is_inside_the_signed_content() -> None:
    a = compute_signature(SECRET, MESSAGE_ID, str(NOW), BODY)
    b = compute_signature(SECRET, MESSAGE_ID, str(NOW + 1), BODY)
    assert a != b


def test_whitespace_difference_in_body_changes_the_signature() -> None:
    """Guards the "never re-serialise" rule: re-encoded JSON differs."""
    compact = compute_signature(SECRET, MESSAGE_ID, str(NOW), b'{"a":1}')
    spaced = compute_signature(SECRET, MESSAGE_ID, str(NOW), b'{"a": 1}')
    assert compact != spaced


# --- body edge cases ----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\x00\x01\x02\xff",
        "unicode: \u00e9\u4e2d\u6587\U0001f600".encode(),
        b'{"nested":{"deep":[1,2,{"x":null}]}}',
        b"x" * 100_000,
    ],
)
def test_arbitrary_bytes_sign_consistently(body: bytes) -> None:
    headers = sign_payload(SECRET, MESSAGE_ID, body, timestamp=NOW).as_dict()
    assert headers[HEADER_SIGNATURE] == _expected(headers, body=body)


def test_empty_body_differs_from_whitespace_body() -> None:
    empty = compute_signature(SECRET, MESSAGE_ID, str(NOW), b"")
    space = compute_signature(SECRET, MESSAGE_ID, str(NOW), b" ")
    assert empty != space


# --- secret hashing -----------------------------------------------------------


def test_hash_is_stable_and_not_the_plaintext() -> None:
    hashed = hash_secret(SECRET)
    assert hashed == hash_secret(SECRET)
    assert SECRET not in hashed
    assert len(hashed) == 64


def test_different_secrets_hash_differently() -> None:
    assert hash_secret("a") != hash_secret("b")
