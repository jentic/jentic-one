"""Fold an async job payload's operation keys into one :class:`OperationInfo`."""

from collections.abc import Mapping
from typing import Any

import structlog
from pydantic import ValidationError

from jentic_one.shared.schemas import OperationInfo

logger = structlog.get_logger(__name__)


def operation_from_job_payload(payload: Mapping[str, Any]) -> OperationInfo | None:
    """Fold a job payload's operation keys into one :class:`OperationInfo`.

    Async job payloads dual-write the ``operation`` dict (id + path template +
    method) and the flat ``operation_id`` (rolling-deploy shim, #1382). The dict
    wins; a payload carrying only the flat id folds into an id-only
    ``OperationInfo``; neither key leaves the caller operation-less.

    A malformed ``operation`` dict never fails the job: the operation identity
    is attribution metadata, so the fold degrades to the flat id (or to no
    operation) with a warning rather than dropping the execution and its record.
    """
    operation = payload.get("operation")
    if operation:
        try:
            return OperationInfo.model_validate(operation)
        except ValidationError:
            logger.warning(
                "job_payload_operation_invalid",
                operation_id=payload.get("operation_id"),
            )
    legacy_id = payload.get("operation_id")
    if legacy_id:
        return OperationInfo(id=str(legacy_id))
    return None
