"""The async enqueue payload — the producer leg of the operation dual-write.

The consumer legs (handler forwarding, executor rebuild) have their own pins;
this one holds the broker's ``_async_job_payload`` to the payload contract both
worker generations read during a rolling deploy.
"""

from typing import Any

from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import _async_job_payload
from jentic_one.shared.schemas import OperationInfo


def _ctx(**overrides: Any) -> ExecuteRequestContext:
    defaults: dict[str, Any] = {
        "upstream_url": "https://api.example.com/v1/things",
        "method": "GET",
        "trace_id": "a" * 32,
        "toolkit_id": "tk_test000000000000000000",
        "operation": OperationInfo(id="op_x", path="/v1/things/{id}", method="GET"),
        "api_vendor": "example",
        "api_name": "api",
        "api_version": "1.0.0",
    }
    defaults.update(overrides)
    return ExecuteRequestContext(**defaults)


def test_payload_dual_writes_operation_dict_and_flat_id() -> None:
    """The producer writes the ``operation`` dict AND the flat ``operation_id``
    (the #1382 rolling-deploy shim) so a pre-dict worker draining the job still
    persists the id and keeps repeated-failure detection keyed."""
    payload = _async_job_payload(_ctx(), execution_id="exec_1", origin="api")

    assert payload["operation"] == {"id": "op_x", "path": "/v1/things/{id}", "method": "GET"}
    assert payload["operation_id"] == "op_x"


def test_payload_operation_keys_are_none_when_discovery_resolved_nothing() -> None:
    """No resolved operation → both keys ride as None (the record persists the
    NULL trio; nothing downstream invents an identity)."""
    payload = _async_job_payload(_ctx(operation=None), execution_id="exec_1", origin="api")

    assert payload["operation"] is None
    assert payload["operation_id"] is None
