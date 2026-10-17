"""The broker's caller-facing shape for an upstream URL its egress policy refuses (#1722).

Shared so the sync execute route (through ``broker/core/exceptions``), the
runners, and the async execution worker (``shared/jobs``, which must not import
``broker/``) give the same refusal the same ``type``, ``detail`` and
``agent_directive``, and log it under the same event.

Two problem types:

* ``invalid_upstream_url`` — every refusal a caller sees before it is
  authorized for a registered operation, with the validator's message (or,
  for a withheld domain-suffix gap, the generic blocked-range message).
* ``private_name_not_in_allowed_domains`` — a DNS name that resolves into
  ``broker.egress.allowed_private_subnets`` but matches no
  ``broker.egress.allowed_internal_domains`` suffix, disclosed only to a caller
  already authorized to call the operation. An unauthorized caller gets the
  generic refusal, so the type is never an oracle for which names resolve into
  the operator's trusted subnet.

The directive is addressed to the operator through the human it prompts. It
names the config key but never the host, never the resolved address, and never
advises widening the suffix list: that list is the second factor that keeps a
public name from being pointed into the subnet, so whether a name belongs on it
is the operator's call. The operator gets the host from the
``upstream_egress_refused`` log line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import structlog

from jentic_one.shared.url_validation import (
    BLOCKED_RANGE_MESSAGE,
    EgressPolicyError,
    PrivateNameNotAllowedError,
)

logger = structlog.get_logger(__name__)

INVALID_UPSTREAM_URL = "invalid_upstream_url"
PRIVATE_NAME_NOT_IN_ALLOWED_DOMAINS = "private_name_not_in_allowed_domains"

#: The config key and env var the distinct refusal names.
INTERNAL_DOMAINS_CONFIG_KEY = "broker.egress.allowed_internal_domains"
INTERNAL_DOMAINS_ENV_VAR = "JENTIC__BROKER__EGRESS__ALLOWED_INTERNAL_DOMAINS"

PRIVATE_NAME_INSTRUCTION = (
    "The broker's egress policy refused this upstream host: it resolves into an allowed "
    f"private subnet, but no entry in {INTERNAL_DOMAINS_CONFIG_KEY} covers it. Only an "
    "operator can review that policy, and the broker has logged the refusal "
    "(upstream_egress_refused) for them. Do not retry and do not ask for the allowlist to "
    "be widened: an operator adds a suffix only for an internal service they run, never "
    "for a public domain. This is not agent-recoverable."
)


@dataclass(frozen=True, slots=True)
class EgressRefusal:
    """The caller-facing ``type``/``detail``/directive of one egress refusal (always a 400)."""

    type: str
    detail: str
    directive: dict[str, Any] | None = None


def egress_refusal(exc: ValueError, *, authorized: bool) -> EgressRefusal:
    """Map an upstream-URL refusal from the egress check to its caller-facing shape.

    ``authorized`` says whether the caller has passed authorization for a
    registered operation. Only then does a domain-suffix gap get its own type;
    otherwise it is reported exactly like any other blocked address.
    """
    if isinstance(exc, PrivateNameNotAllowedError):
        if not authorized:
            return EgressRefusal(type=INVALID_UPSTREAM_URL, detail=BLOCKED_RANGE_MESSAGE)
        return EgressRefusal(
            type=PRIVATE_NAME_NOT_IN_ALLOWED_DOMAINS,
            detail=(
                f"Upstream host '{exc.hostname}' resolves into an allowed private subnet but "
                f"is not covered by {INTERNAL_DOMAINS_CONFIG_KEY}"
            ),
            directive={
                "strategy": "prompt_human",
                "parameters": {
                    "config_key": INTERNAL_DOMAINS_CONFIG_KEY,
                    "env_var": INTERNAL_DOMAINS_ENV_VAR,
                },
                "human_readable_instruction": PRIVATE_NAME_INSTRUCTION,
            },
        )
    return EgressRefusal(type=INVALID_UPSTREAM_URL, detail=str(exc))


def egress_refusal_problem(refusal: EgressRefusal) -> dict[str, Any]:
    """The RFC 9457 body the broker's handler renders for *refusal*.

    Same members as ``broker/core/problem.broker_error_problem`` for the
    matching ``UpstreamUrlNotAllowedError`` (pinned by a parity test), for the
    worker, which cannot build that error.
    """
    body: dict[str, Any] = {
        "type": refusal.type,
        "title": refusal.detail,
        "status": 400,
        "detail": refusal.detail,
        "error_origin": "broker",
    }
    if refusal.directive is not None:
        body["agent_directive"] = refusal.directive
    return body


def egress_reason(exc: ValueError) -> str:
    """The operator-facing reason for a refusal, whatever type the caller was shown."""
    if isinstance(exc, PrivateNameNotAllowedError):
        return PRIVATE_NAME_NOT_IN_ALLOWED_DOMAINS
    if isinstance(exc, EgressPolicyError):
        return "blocked_target"
    return "invalid_url"


def upstream_host(url: str) -> str | None:
    """The host of *url* for a log line, or ``None`` when it has none or won't parse."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def log_egress_refusal(exc: ValueError, *, host: str | None, stage: str, problem_type: str) -> None:
    """Log a refusal for operators: the host and the real reason, never the resolved address.

    ``stage`` is where it was refused (``preflight``, ``connect``, ``queued``);
    ``problem_type`` is what the caller was shown, which for an unauthorized
    caller hides the ``reason``.
    """
    logger.warning(
        "upstream_egress_refused",
        host=host,
        stage=stage,
        reason=egress_reason(exc),
        problem_type=problem_type,
    )
