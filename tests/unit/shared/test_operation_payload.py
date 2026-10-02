"""The async job payload fold: ``operation`` dict + legacy flat ``operation_id``."""

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
        # The dict wins over the dual-written flat id.
        (
            {"operation": {"id": "op_x", "path": "/a", "method": "GET"}, "operation_id": "op_y"},
            OperationInfo(id="op_x", path="/a", method="GET"),
        ),
        # Legacy in-flight job: only the flat id.
        ({"operation_id": "op_legacy"}, OperationInfo(id="op_legacy")),
        # An empty dict or empty id carries no identity.
        ({"operation": {}, "operation_id": ""}, None),
        ({}, None),
    ],
)
def test_fold(payload: dict[str, Any], expected: OperationInfo | None) -> None:
    assert operation_from_job_payload(payload) == expected


def test_malformed_operation_dict_degrades_to_the_flat_id() -> None:
    """A dict missing its ``id`` must not fail the job — attribution metadata
    degrades to the dual-written flat id rather than dropping the execution."""
    payload = {"operation": {"path": "/v1/things", "method": "GET"}, "operation_id": "op_x"}
    assert operation_from_job_payload(payload) == OperationInfo(id="op_x")


def test_malformed_operation_dict_without_flat_id_is_operation_less() -> None:
    assert operation_from_job_payload({"operation": {"path": 7}}) is None


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
