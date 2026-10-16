"""The async enqueue payload — the producer leg of the operation contract.

The consumer legs (handler forwarding, executor rebuild) have their own pins;
this one holds the broker's ``_async_job_payload`` to the payload contract the
worker reads.
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
        "operation": OperationInfo(
            id="op_x", path="/v1/things/{id}", method="GET", relative_path="/v1/things/7"
        ),
        "api_vendor": "example",
        "api_name": "api",
        "api_version": "1.0.0",
    }
    defaults.update(overrides)
    return ExecuteRequestContext(**defaults)


def test_payload_carries_the_operation_dict_only() -> None:
    """The producer writes the resolved operation as one ``operation`` dict and
    no flat ``operation_id`` key (#1382). The dict carries the server-relative
    path so the run-time rule re-check judges the same path the sync edge would
    (#1424)."""
    payload = _async_job_payload(_ctx(), execution_id="exec_1", origin="api")

    assert payload["operation"] == {
        "id": "op_x",
        "path": "/v1/things/{id}",
        "method": "GET",
        "relative_path": "/v1/things/7",
    }
    assert "operation_id" not in payload


def test_payload_operation_is_none_when_discovery_resolved_nothing() -> None:
    """No resolved operation → the dict rides as None (the record persists the
    NULL trio; nothing downstream invents an identity)."""
    payload = _async_job_payload(_ctx(operation=None), execution_id="exec_1", origin="api")

    assert payload["operation"] is None
    assert "operation_id" not in payload


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
    """Content-Type, version headers and friends ride along for the run; the
    caller's credentials, broker steering headers and arbitrary headers never
    reach the stored payload."""
    inbound = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "If-Match": '"v1"',
        "Notion-Version": "2022-06-28",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer agent-token",
        "Proxy-Authorization": "Basic abc",
        "Prefer": "respond-async",
        "Cookie": "sid=1",
        "Connection": "keep-alive",
        "Jentic-Credential-Id": "cred_1",
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
        "notion-version": "2022-06-28",
        "x-github-api-version": "2022-11-28",
    }


def test_payload_omits_headers_when_none_are_kept() -> None:
    payload = _async_job_payload(_ctx(), execution_id="exec_1", origin="api", headers={})

    assert "headers" not in payload


@pytest.mark.parametrize(
    "name",
    ["Notion-Version", "Stripe-Version", "X-GitHub-Api-Version", "Box-Version", "x-acme-version"],
)
def test_any_vendor_version_header_replays(name: str) -> None:
    assert is_replay_header(name)


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "Cookie",
        "Proxy-Authorization",
        "Connection",
        "Version",
        "Jentic-Api-Version",
        "X-Jentic-Version",
        "X-Api-Key",
    ],
)
def test_credentials_hop_by_hop_and_jentic_headers_never_replay(name: str) -> None:
    assert not is_replay_header(name)
