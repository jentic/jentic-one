"""Shared, non-secret DISPLAY helpers for execution lifecycle events.

Both execution emit sites — the sync/streaming broker service
(``broker/services/execution/service.py``) and the async worker
(``shared/jobs/execution_handler.py``) — build the ``execution.completed`` /
``execution.failed`` event ``summary`` + ``data`` from these helpers, so the two
paths cannot drift: an outbound webhook carries the same human summary and the
same safe display fields whichever path ran the call.

They live in ``shared`` (not ``broker``) precisely so the async worker can reuse
them without importing ``broker`` — the ``shared → broker`` import boundary is
arch-enforced (``tests/arch/test_module_boundaries.py``). Every field is a
pre-resolved identifier or metric already on the execution context — ids,
toolkit/operation slugs, the discovery-driven api vendor/name/version, the
measured duration, the upstream HTTP status. None is a secret, a credential, or
an upstream response body (the same anti-exfiltration boundary the fan-out
enforces).
"""

from __future__ import annotations

from typing import Any

from jentic_one.shared.models import ExecutionStatus

#: Cap on the human summary length so a long upstream error can't bloat the
#: persisted event / outbound payload.
MAX_EVENT_SUMMARY_LEN = 128


def execution_display_data(
    *,
    execution_id: str,
    toolkit_id: str | None,
    operation_id: str | None,
    api_vendor: str | None,
    api_name: str | None,
    api_version: str | None,
    duration_ms: int | None,
    http_status: int | None,
) -> dict[str, Any]:
    """Build the non-secret DISPLAY ``data`` for an execution lifecycle event.

    Empty/absent fields are dropped so the payload only carries what actually
    resolved at the emit site (no ``null`` noise on the wire, and the Slack
    relay's field renderer stays clean).
    """
    api = {
        key: value
        for key, value in (
            ("vendor", api_vendor),
            ("name", api_name),
            ("version", api_version),
        )
        if value
    }
    data: dict[str, Any] = {"execution_id": execution_id}
    if toolkit_id:
        data["toolkit_id"] = toolkit_id
    if operation_id:
        data["operation_id"] = operation_id
    if api:
        data["api"] = api
    if duration_ms is not None:
        data["duration_ms"] = duration_ms
    if http_status is not None:
        data["http_status"] = http_status
    return data


def execution_summary(
    *,
    status: ExecutionStatus,
    operation_id: str | None,
    api_vendor: str | None,
    duration_ms: int | None,
    error_msg: str | None,
) -> str:
    """Human-readable one-liner for an execution lifecycle event.

    Prefers the operation id (falling back to the api vendor, then a generic
    label) so a Slack/email reader sees *what ran* instead of a bare execution
    id. On the failed branch the already-sanitised ``error_msg`` is appended —
    it is the same short, upstream-status-only string persisted to the execution
    record (never an upstream response body).
    """
    what = operation_id or api_vendor or "operation"
    if status == ExecutionStatus.COMPLETED:
        if duration_ms is not None:
            return f"Execution of {what} completed in {duration_ms}ms"
        return f"Execution of {what} completed"
    reason = (error_msg or "unknown")[:MAX_EVENT_SUMMARY_LEN]
    return f"Execution of {what} failed: {reason}"
