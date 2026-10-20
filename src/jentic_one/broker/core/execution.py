"""Broker execution domain helpers (pure — no transport/DB)."""

from __future__ import annotations

import uuid

from jentic_one.broker.core.exceptions import BrokerError


def mint_execution_id() -> str:
    """Generate a unique execution ID with the exec_ prefix."""
    return f"exec_{uuid.uuid4().hex[:24]}"


def execution_error(exc: BaseException) -> str:
    """The execution record's ``error`` for a call that ended in ``exc``.

    One vocabulary for every path (buffered, a stream that fails to open, a
    stream that fails mid-body), so the same failure groups under the same
    string in ``execution_records.error`` and the ``execution.failed`` summary:

    * a :class:`BrokerError` records its problem ``type`` — the stable
      identifier a caller receives in the problem+json body (its class name if
      it carries no type). The type itself says who ended the call
      (``upstream_connect_failed``, ``upstream_timeout``,
      ``upstream_transport_error`` for the upstream; ``circuit_open``,
      ``deadline_exceeded``, ``upstream_response_too_large`` for a broker
      decision);
    * anything else records ``server_error: <ExceptionClass>`` — the type a
      caller receives for an unmapped exception, plus the class for diagnosis.

    Never the exception message or ``detail``: an ``httpx`` message can quote
    request material, and the type is what groups.
    """
    if isinstance(exc, BrokerError):
        return exc.type if exc.type != BrokerError.default_type else type(exc).__name__
    return f"server_error: {type(exc).__name__}"
