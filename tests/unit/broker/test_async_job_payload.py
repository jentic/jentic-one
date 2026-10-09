"""The async enqueue payload — the producer leg of the operation dual-write.

The consumer legs (handler forwarding, executor rebuild) have their own pins;
this one holds the broker's ``_async_job_payload`` to the payload contract both
worker generations read during a rolling deploy.
"""

from typing import Any

import pytest

from jentic_one.broker.core.proxy_headers import is_replay_header, replay_headers
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import _async_job_payload
from jentic_one.shared.jobs.operation_payload import operation_from_job_payload
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


def test_payload_round_trips_through_the_worker_fold() -> None:
    """Producer and consumer agree: what the broker enqueues, the worker folds
    back into the identical ``OperationInfo`` (no field lost in transit)."""
    op = OperationInfo(id="op_x", path="/v1/things/{id}", method="GET")
    payload = _async_job_payload(_ctx(operation=op), execution_id="exec_1", origin="api")

    assert operation_from_job_payload(payload) == op


def test_payload_carries_no_toolkit_and_keeps_server_variables() -> None:
    """The merged-in server-variable keys ride alongside the operation, and the
    retired toolkit attribution is no longer written."""
    payload = _async_job_payload(
        _ctx(
            server_variables={"region": "eu"},
            server_variable_defaults={"tld": "com"},
            server_variables_unresolved=True,
        ),
        execution_id="exec_1",
        origin="api",
    )

    assert "toolkit_id" not in payload
    assert payload["server_variables"] == {"region": "eu"}
    assert payload["server_variable_defaults"] == {"tld": "com"}
    assert payload["server_variables_unresolved"] is True


def test_payload_keeps_only_the_replay_headers() -> None:
    """Content-Type and friends ride along for the run; the caller's credentials,
    broker steering headers and arbitrary headers never reach the stored payload."""
    inbound = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "If-Match": '"v1"',
        "Authorization": "Bearer agent-token",
        "Prefer": "respond-async",
        "Cookie": "sid=1",
        "X-Custom": "1",
        "Content-Length": "9",
    }
    payload = _async_job_payload(
        _ctx(method="POST"),
        execution_id="exec_1",
        origin="api",
        body=b'{"a": 1}',
        headers=replay_headers(inbound),
    )

    assert payload["headers"] == {
        "content-type": "application/json",
        "accept": "application/json",
        "if-match": '"v1"',
    }


def test_payload_omits_headers_when_none_are_kept() -> None:
    payload = _async_job_payload(_ctx(), execution_id="exec_1", origin="api", headers={})

    assert "headers" not in payload


def test_replay_keeps_api_version_headers_vendors_require() -> None:
    """A queued or held run sends the API version the caller chose: vendor
    version headers and the generic ``*-version`` forms replay."""
    inbound = {
        "Notion-Version": "2022-06-28",
        "Stripe-Version": "2024-06-20",
        "X-GitHub-Api-Version": "2022-11-28",
        "Anthropic-Version": "2023-06-01",
        "Api-Version": "7.1",
        "X-Api-Version": "2",
    }

    assert replay_headers(inbound) == {key.lower(): value for key, value in inbound.items()}


@pytest.mark.parametrize(
    "name",
    [
        "Version",
        "Jentic-Version",
        "X-Jentic-Version",
        "Jentic-Revision",
        "Authorization",
        "Proxy-Authorization",
        "Cookie",
        "X-Api-Key",
        "Connection",
        "Upgrade",
        "Host",
        "X-Forwarded-For",
        "Versioning",
        "X-Version-Token",
        "-version",
        "x--version",
    ],
)
def test_replay_never_keeps_credentials_hop_by_hop_or_jentic_headers(name: str) -> None:
    assert is_replay_header(name) is False
    assert replay_headers({name: "1"}) == {}
