"""The async job payload fold: the ``operation`` dict into one ``OperationInfo``."""

from typing import Any

import pytest

from jentic_one.shared.jobs.operation_payload import operation_from_job_payload
from jentic_one.shared.schemas import OperationInfo


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"operation": {"id": "op_x", "path": "/v1/things", "method": "GET"}},
            OperationInfo(id="op_x", path="/v1/things", method="GET"),
        ),
        # A flat ``operation_id`` key carries no identity on its own.
        ({"operation_id": "op_x"}, None),
        # An empty dict carries no identity.
        ({"operation": {}}, None),
        ({}, None),
    ],
)
def test_fold(payload: dict[str, Any], expected: OperationInfo | None) -> None:
    assert operation_from_job_payload(payload) == expected


def test_malformed_operation_dict_is_operation_less() -> None:
    """A dict missing its ``id`` must not fail the job — attribution metadata
    degrades to no operation rather than dropping the execution."""
    payload = {"operation": {"path": "/v1/things", "method": "GET"}}
    assert operation_from_job_payload(payload) is None


@pytest.mark.parametrize(
    ("info", "label"),
    [
        (OperationInfo(id="op_x", path="/v1/things/{id}", method="GET"), "GET /v1/things/{id}"),
        (OperationInfo(id="op_x", path="/v1/things"), "/v1/things"),
        (OperationInfo(id="op_x"), "op_x"),
    ],
)
def test_display(info: OperationInfo, label: str) -> None:
    assert info.display == label
