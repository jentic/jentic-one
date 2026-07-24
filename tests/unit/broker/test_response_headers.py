"""Unit tests for the broker response header assembly (§04 tracestate echo)."""

from __future__ import annotations

from jentic_one.broker.core.headers import TRACESTATE_HEADER, JenticHeader
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import _metadata_headers
from jentic_one.broker.web.streaming import _metadata_headers as _stream_metadata_headers


def _ctx(**overrides: object) -> ExecuteRequestContext:
    base: dict[str, object] = {
        "upstream_url": "https://api.stripe.com/v1/charges",
        "method": "POST",
        "trace_id": "trace-1",
        "toolkit_id": "tk_abc123",
        "operation_id": "op_1",
        "api_vendor": "stripe",
        "api_name": "payments",
        "api_version": "2023-10-16",
    }
    base.update(overrides)
    return ExecuteRequestContext(**base)  # type: ignore[arg-type]


def test_metadata_headers_echo_jentic_tracestate():
    """The response carries the packed jentic= tracestate member."""
    headers = _metadata_headers(_ctx(), "exec_xyz789")
    assert headers[TRACESTATE_HEADER] == "jentic=exec_xyz789:tk_abc123:stripe:payments:2023-10-16"


def test_metadata_headers_tracestate_uses_placeholders_for_missing_fields():
    """Missing toolkit/api segments still produce a fixed five-field member."""
    headers = _metadata_headers(
        _ctx(toolkit_id=None, api_vendor=None, api_name=None, api_version=None),
        "exec_1",
    )
    assert headers[TRACESTATE_HEADER] == "jentic=exec_1:_:_:_:_"


def test_metadata_headers_stamp_credential_attribution_when_present():
    """A resolved credential is echoed as ``Jentic-Credential-Id``/``-Name`` (#740)."""
    headers = _metadata_headers(
        _ctx(credential_id="cred_abc", credential_name="stripe-live"),
        "exec_1",
    )
    assert headers[JenticHeader.CREDENTIAL_ID.value] == "cred_abc"
    assert headers[JenticHeader.CREDENTIAL_NAME.value] == "stripe-live"


def test_metadata_headers_omit_credential_attribution_when_absent():
    """No credential → no attribution header (unambiguous ``no credential used``)."""
    headers = _metadata_headers(_ctx(credential_id=None, credential_name=None), "exec_1")
    assert JenticHeader.CREDENTIAL_ID.value not in headers
    assert JenticHeader.CREDENTIAL_NAME.value not in headers


def test_stream_metadata_headers_stamp_credential_attribution():
    """Streaming path stays symmetric with the sync router (#740)."""
    headers = _stream_metadata_headers(
        _ctx(credential_id="cred_abc", credential_name="stripe-live"),
        "exec_1",
        200,
    )
    assert headers[JenticHeader.CREDENTIAL_ID.value] == "cred_abc"
    assert headers[JenticHeader.CREDENTIAL_NAME.value] == "stripe-live"


def test_stream_metadata_headers_omit_credential_attribution_when_absent():
    headers = _stream_metadata_headers(
        _ctx(credential_id=None, credential_name=None), "exec_1", 200
    )
    assert JenticHeader.CREDENTIAL_ID.value not in headers
    assert JenticHeader.CREDENTIAL_NAME.value not in headers
