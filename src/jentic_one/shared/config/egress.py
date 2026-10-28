"""Upstream client, rate limit, circuit breaker, retry, and egress configuration."""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from jentic_one.shared.config._common import _csv_to_list


class UpstreamClientConfig(BaseModel):
    """Bounds for the single shared outbound ``httpx.AsyncClient``.

    The timeouts are httpx semantics: ``read_timeout_s`` is the *between-bytes*
    gap timeout (per read), **not** a whole-stream cap — a trickle that keeps
    sending under the limit can hold a pool slot open. The overall transfer
    deadline is owned by the response-streaming guard, not here.
    """

    connect_timeout_s: float = 5.0
    read_timeout_s: float = Field(
        default=30.0,
        description=(
            "Per-read *between-bytes* gap timeout (seconds, httpx semantics) on "
            "upstream responses — not a whole-stream cap. Pairs with "
            "``broker.resilience.request_deadline_s``, which must be sized above "
            "it so one healthy slow attempt isn't pre-empted by the envelope "
            "deadline."
        ),
    )
    write_timeout_s: float = 30.0
    pool_timeout_s: float = 2.0
    # Negotiate HTTP/2 via ALPN, falling back to 1.1 when the upstream doesn't
    # offer it (safe default). Requires the `h2` package. Some upstreams (APNs,
    # certain CDNs) refuse 1.1; a deployment that must pin 1.1 sets this false.
    http2: bool = True
    max_connections: int = 200
    max_keepalive: int = 50
    # httpx has no native per-host limit; enforced with a per-host semaphore
    # bulkhead in the runner so one slow upstream can't drain the whole pool.
    max_per_host: int = 50
    # Global default / fallback request-body cap.
    max_request_bytes: int = 10 * 1024 * 1024
    # Per-Content-Type overrides, matched most-specific-first: exact
    # ("application/json") then wildcard ("audio/*") then the global default.
    # A missing/unknown type uses the global default (never unbounded).
    max_request_bytes_by_type: dict[str, int] = Field(
        default_factory=lambda: {
            "application/json": 2 * 1024 * 1024,
            "multipart/form-data": 50 * 1024 * 1024,
        }
    )
    # Response-side counterpart to the request-body cap. The runner
    # enforces this *mid-stream* while reading the upstream body, aborting the
    # connection the moment it's exceeded so a hostile/large upstream can't OOM
    # the instance. 0 disables the cap (unbounded — not recommended).
    max_response_bytes: int = 10 * 1024 * 1024
    # Stream the upstream response straight through to the client instead of
    # whole-buffering it. On by default; applies only to the sync
    # proxy path with no Idempotency-Key (idempotent requests + the async worker
    # keep buffering, since replay/persistence need the full body). Disable to
    # force the buffered path everywhere.
    stream_passthrough_enabled: bool = True
    # Whole-stream transfer deadline for a streamed response. Unlike
    # ``read_timeout_s`` (a between-bytes gap), this bounds the *total* time the
    # body may take to transfer, so a steady trickle — or a slow client draining
    # the proxied body — can't pin the upstream connection/pool slot forever. The
    # stream is aborted (and the upstream torn down) when it's exceeded. 0
    # disables the deadline.
    transfer_deadline_s: float = 300.0


class RateLimitConfig(BaseModel):
    """Per-caller token-bucket rate limit.

    Keyed on the resolved ``actor_id`` and enforced in a post-auth dependency
    (the actor isn't known at admission time, so this can't be a plain
    pre-auth middleware). The token bucket itself lives on the shared-state
    backend (``RateLimitStore``); with the memory backend the limit is
    per-instance, with Redis it is cluster-wide — no call-site change.
    """

    enabled: bool = True
    # Sustained refill rate, requests-per-minute. ``burst`` is the bucket
    # capacity (max instantaneous spend) and the ``RateLimit-Limit`` value.
    default_rpm: int = 600
    burst: int = 100


class CircuitBreakerConfig(BaseModel):
    """Per-upstream circuit breaker.

    Counts failures/totals per rolling ``window_s`` on the shared-state
    backend's atomic counters; when the failure ratio crosses
    ``failure_ratio`` the circuit opens (a ``set_if_absent`` latch) and
    fast-fails for ``cooldown_s``. ``observation`` mode tracks + emits
    ``would_block`` but still calls the upstream (safe-rollout dry run).
    """

    enabled: bool = True
    enforcement_mode: Literal["blocking", "observation"] = "blocking"
    failure_ratio: float = 0.5
    # Minimum calls in the window before the ratio can trip — avoids opening on
    # a single early failure (1/1 = 100% ratio).
    min_calls: int = 10
    window_s: int = 30
    cooldown_s: int = 15


class RetryConfig(BaseModel):
    """Idempotency-aware upstream retry.

    The ``RetryRunner`` decorator retries a failed attempt **only** when it is
    safe to do so: a connect-phase failure (no bytes on the wire) is retryable
    for any method, while a post-send failure (read timeout, mirrored ``429``/
    ``503``) is retried only for idempotent HTTP methods — ``Idempotency-Key``
    is broker-consumed and stripped from outbound headers, so a post-send
    ``POST`` or ``PATCH`` retry would double-spend (#1799).
    Each attempt still flows through the circuit breaker (retry *outside*,
    breaker *inside*); the whole loop is bounded by the request deadline.
    """

    enabled: bool = False
    # Total attempts including the first (so 3 ⇒ initial + up to 2 retries).
    max_attempts: int = 3
    # Exponential backoff: ``base_backoff_s * 2**(attempt-1)``, capped at
    # ``max_backoff_s``, then jittered (full jitter) and clamped to the deadline.
    base_backoff_s: float = 0.2
    max_backoff_s: float = 5.0
    # Retry these upstream status codes (transient overload / rate limit). An
    # upstream ``Retry-After`` on these is honored (capped at the deadline).
    retry_statuses: list[int] = Field(default_factory=lambda: [429, 502, 503, 504])


class EgressConfig(BaseModel):
    """Outbound SSRF/egress policy for upstream calls.

    Defaults are **strict** (both lists empty) — identical to the historical
    hard-coded behaviour: every private range and the cloud-metadata host are
    blocked. The two allowlists exist because the broker is frequently installed
    *inside* a corporate network to bridge to internal/legacy APIs, where the
    default block is a false positive. Both are opt-in, auditable operator
    decisions; the cloud-metadata IP is a **hard, non-overridable** deny so a
    credential-stealing SSRF can never be allowlisted by accident.
    """

    allowed_private_subnets: Annotated[list[str], BeforeValidator(_csv_to_list)] = Field(
        default_factory=list,
        description=(
            "CIDRs exempted from the private-IP egress block (e.g. "
            '``["10.50.0.0/16"]``). The cloud-metadata and platform-credential '
            "IPs (e.g. 169.254.169.254, 169.254.170.2, fd00:ec2::254, "
            "100.100.100.200) are never exempted, even when a listed range "
            "covers them. Accepts a YAML list or a comma-separated string."
        ),
    )
    allowed_internal_domains: Annotated[list[str], BeforeValidator(_csv_to_list)] = Field(
        default_factory=list,
        description=(
            'Domain suffixes (e.g. ``[".svc.cluster.local"]``) whose resolved '
            "private IP is permitted. The resolved IP must still fall in an "
            "allowed subnet. Accepts a YAML list or a comma-separated string."
        ),
    )
    dns_pinning_enabled: bool = Field(
        default=True,
        description=(
            "Pin the outbound connection to the IP validated at connect time, "
            "closing the DNS-rebinding TOCTOU between pre-request validation "
            "and the runner's own resolution. Disable only to debug egress "
            "issues."
        ),
    )

    @field_validator("allowed_private_subnets")
    @classmethod
    def _validate_cidrs(cls, value: list[str]) -> list[str]:
        return _validate_cidr_list(value)


def _validate_cidr_list(value: list[str]) -> list[str]:
    for cidr in value:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(f"invalid CIDR in allowed_private_subnets: {cidr!r}") from exc
    return value


def _csv_to_optional_list(value: Any) -> list[str] | None:
    return None if value is None else _csv_to_list(value)


class ControlEgressConfig(BaseModel):
    """Private-network allowlist for the platform's OAuth and credential calls.

    Holds only the two allowlist keys. A key left unset takes its value from
    the matching ``broker.egress`` key. These calls always pin DNS, so there is
    no ``dns_pinning_enabled`` key here, and unknown keys are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_private_subnets: Annotated[list[str] | None, BeforeValidator(_csv_to_optional_list)] = (
        Field(
            default=None,
            description=(
                "CIDRs exempted from the private-IP block for these calls (e.g. "
                '``["10.50.0.0/16"]``); unset inherits '
                "`broker.egress.allowed_private_subnets`, and `[]` blocks every "
                "private range for these calls even when the broker allows one. "
                "The cloud-metadata and platform-credential IPs are never exempted. "
                "Accepts a YAML list or a comma-separated string."
            ),
        )
    )
    allowed_internal_domains: Annotated[
        list[str] | None, BeforeValidator(_csv_to_optional_list)
    ] = Field(
        default=None,
        description=(
            'Domain suffixes (e.g. ``[".corp.internal"]``) whose resolved '
            "private IP is permitted for these calls; unset inherits "
            "`broker.egress.allowed_internal_domains`. The resolved IP must "
            "still fall in an allowed subnet. Accepts a YAML list or a "
            "comma-separated string."
        ),
    )

    @field_validator("allowed_private_subnets")
    @classmethod
    def _validate_cidrs(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_cidr_list(value)
