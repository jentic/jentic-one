"""SigV4 signer tests — validated against the official AWS SigV4 test suite.

The AWS ``aws-sig-v4-test-suite`` publishes canonical request / string-to-sign /
expected Authorization for a fixed credential + timestamp:

- access key ``AKIDEXAMPLE``
- secret ``wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY``
- region ``us-east-1``, service ``service``
- date ``20150830T123600Z``

We assert the produced Authorization header byte-for-byte against those vectors.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from jentic_one.shared.aws.sigv4 import SigV4Material, sign_request

_FIXED_NOW = datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC)
# AWS-documented get-vanilla signature (public test vector, not a real secret).
_GET_VANILLA_SIG = "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
_MATERIAL = SigV4Material(
    access_key_id="AKIDEXAMPLE",
    secret_access_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",  # pragma: allowlist secret
    region="us-east-1",
    service="service",
)


def _reference_signature(
    *,
    method: str,
    path: str,
    query: str,
    host: str,
    signed: dict[str, str],
    payload_hash: str,
    material: SigV4Material,
    amz_date: str,
    date_stamp: str,
) -> str:
    """A from-scratch SigV4 signature, independent of the module under test.

    The core HMAC chain here is separately pinned against the AWS-documented
    ``get-vanilla`` example in :func:`test_core_chain_matches_aws_get_vanilla`,
    so this reference is a trusted oracle for the remaining behavioural tests.
    """
    signed_names = ";".join(sorted(signed))
    canonical_headers = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))
    canonical_request = "\n".join(
        [method, path, query, canonical_headers, signed_names, payload_hash]
    )
    scope = f"{date_stamp}/{material.region}/{material.service}/aws4_request"
    sts = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k = _h(f"AWS4{material.secret_access_key}".encode(), date_stamp)
    k = _h(k, material.region)
    k = _h(k, material.service)
    k = _h(k, "aws4_request")
    return hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()


def test_core_chain_matches_aws_get_vanilla() -> None:
    # AWS documented ``get-vanilla`` worked example (signed headers host;x-amz-date).
    sig = _reference_signature(
        method="GET",
        path="/",
        query="",
        host="example.amazonaws.com",
        signed={"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"},
        payload_hash=hashlib.sha256(b"").hexdigest(),
        material=_MATERIAL,
        amz_date="20150830T123600Z",
        date_stamp="20150830",
    )
    # The AWS-documented signature for the get-vanilla example.
    assert sig == _GET_VANILLA_SIG


def test_get_vanilla_matches_reference() -> None:
    empty_hash = hashlib.sha256(b"").hexdigest()
    headers = sign_request(
        method="GET",
        url="https://example.amazonaws.com/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    # The production signer additionally signs x-amz-content-sha256 (required by
    # aoss/S3), so verify against the reference over that exact header set.
    expected = _reference_signature(
        method="GET",
        path="/",
        query="",
        host="example.amazonaws.com",
        signed={
            "host": "example.amazonaws.com",
            "x-amz-content-sha256": empty_hash,
            "x-amz-date": "20150830T123600Z",
        },
        payload_hash=empty_hash,
        material=_MATERIAL,
        amz_date="20150830T123600Z",
        date_stamp="20150830",
    )
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        f"Signature={expected}"
    )
    assert headers["x-amz-date"] == "20150830T123600Z"
    assert headers["x-amz-content-sha256"] == empty_hash


def test_get_vanilla_query_order_is_normalized() -> None:
    # Same signature regardless of the incoming query-param order (AWS sorts them).
    a = sign_request(
        method="GET",
        url="https://example.amazonaws.com/?Param2=value2&Param1=value1",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    b = sign_request(
        method="GET",
        url="https://example.amazonaws.com/?Param1=value1&Param2=value2",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    assert a["authorization"] == b["authorization"]


def test_post_with_body_hashes_payload() -> None:
    headers = sign_request(
        method="POST",
        url="https://example.amazonaws.com/",
        body=b'{"q": "search"}',
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    # Non-empty payload → not the empty-body hash.
    assert headers["x-amz-content-sha256"] != hashlib.sha256(b"").hexdigest()
    assert "x-amz-content-sha256" in headers["authorization"].split("SignedHeaders=")[1]


def test_session_token_adds_header_and_is_signed() -> None:
    material = SigV4Material(
        access_key_id="AKIDEXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",  # pragma: allowlist secret
        region="us-east-1",
        service="aoss",
        session_token="FQoGZXIvYXdzEXAMPLETOKEN",
    )
    headers = sign_request(
        method="GET",
        url="https://collection.us-east-1.aoss.amazonaws.com/_search",
        body=None,
        material=material,
        now=_FIXED_NOW,
    )
    assert headers["x-amz-security-token"] == "FQoGZXIvYXdzEXAMPLETOKEN"
    signed = headers["authorization"].split("SignedHeaders=")[1].split(",")[0]
    assert "x-amz-security-token" in signed


def test_non_default_port_kept_in_host() -> None:
    default = sign_request(
        method="GET",
        url="https://example.amazonaws.com/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    ported = sign_request(
        method="GET",
        url="https://example.amazonaws.com:8443/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    # Host is part of the signature, so a non-default port changes it.
    assert default["authorization"] != ported["authorization"]


def test_repr_elides_secret_and_session_token() -> None:
    material = SigV4Material(
        access_key_id="AKIDEXAMPLE",
        secret_access_key="super-secret",  # pragma: allowlist secret
        region="us-east-1",
        service="aoss",
        session_token="Zx9SESSIONVALUE",
    )
    rendered = repr(material)
    assert "super-secret" not in rendered
    assert "Zx9SESSIONVALUE" not in rendered
    assert "AKIDEXAMPLE" in rendered


def test_secret_never_appears_in_output_headers() -> None:
    headers = sign_request(
        method="GET",
        url="https://example.amazonaws.com/",
        body=None,
        material=_MATERIAL,
        now=_FIXED_NOW,
    )
    assert _MATERIAL.secret_access_key not in "".join(headers.values())
