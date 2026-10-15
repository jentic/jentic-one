"""Fold an async job payload's ``operation`` dict into one :class:`OperationInfo`."""

from collections.abc import Mapping
from typing import Any

import structlog
from pydantic import ValidationError

from jentic_one.shared.schemas import OperationInfo

logger = structlog.get_logger(__name__)


def operation_from_job_payload(payload: Mapping[str, Any]) -> OperationInfo | None:
    """Fold a job payload's ``operation`` dict into one :class:`OperationInfo`.

    The dict carries the resolved operation (id + path template + method +
    server-relative path); an absent or empty dict leaves the caller
    operation-less.

    A malformed ``operation`` dict never fails the job: the operation identity
    is attribution metadata, so the fold degrades to no operation with a
    warning rather than dropping the execution and its record.
    """
    operation = payload.get("operation")
    if not operation:
        return None
    try:
        return OperationInfo.model_validate(operation)
    except ValidationError:
        logger.warning(
            "job_payload_operation_invalid",
            execution_id=payload.get("execution_id"),
        )
        return None
