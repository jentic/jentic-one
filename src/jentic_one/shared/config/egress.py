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

    connect_timeout_s: float = Field(
        default=5.0,
        description="Timeout (seconds) for establishing an upstream connection.",
    )
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
    write_timeout_s: float = Field(
        default=30.0,
        description="Per-write timeout (seconds) while sending a request body upstream.",
    )
    pool_timeout_s: float = Field(
        default=2.0,
        description=(
            "How long (seconds) a call waits for a free connection from the shared "
            "pool before failing."
        ),
    )
    # Requires the ``h2`` package.
    http2: bool = Field(
        default=True,
        description=(
            "Negotiate HTTP/2 via ALPN, falling back to HTTP/1.1 when the upstream "
            "does not offer it. Some upstreams (APNs, certain CDNs) refuse 1.1; set "
            "false only to pin HTTP/1.1."
        ),
    )
    max_connections: int = Field(
        default=200,
        description="Maximum open upstream connections in the shared client pool.",
    )
    max_keepalive: int = Field(
        default=50,
        description="Maximum idle keep-alive connections the shared pool retains.",
    )
    # httpx has no native per-host limit, so the runner enforces this with a
    # per-host semaphore bulkhead.
    max_per_host: int = Field(
        default=50,
        description=(
            "Maximum concurrent calls to one upstream host, so one slow upstream "
            "cannot drain the whole connection pool."
        ),
    )
    max_request_bytes: int = Field(
        default=10 * 1024 * 1024,
        description=(
            "Default cap (bytes) on an execute request body, for content types "
            "without an entry in ``max_request_bytes_by_type``; over it the broker "
            "answers 413."
        ),
    )
    max_request_bytes_by_type: dict[str, int] = Field(
        default_factory=lambda: {
            "application/json": 2 * 1024 * 1024,
            "multipart/form-data": 50 * 1024 * 1024,
        },
        description=(
            "Per-Content-Type request-body caps (bytes), matched most specific "
            "first: exact type (``application/json``), then wildcard "
            "(``audio/*``), then ``max_request_bytes``. A missing or unknown type "
            "gets the default, never unbounded."
        ),
    )
    # Enforced mid-stream so a hostile or oversized upstream cannot OOM the instance.
    max_response_bytes: int = Field(
        default=10 * 1024 * 1024,
        description=(
            "Cap (bytes) on an upstream response body, enforced while reading: the "
            "connection is aborted as soon as it is exceeded. 0 disables the cap "
            "(unbounded; not recommended)."
        ),
    )
    # Keyed and async executions buffer because replay and persistence need the
    # full body.
    stream_passthrough_enabled: bool = Field(
        default=True,
        description=(
            "Stream upstream responses straight through to the client instead of "
            "buffering them. Applies to synchronous executions only; async "
            "executions always buffer, and so do executions with an "
            "``Idempotency-Key`` when ``broker.idempotency.enabled`` is true. "
            "Off forces buffering everywhere."
        ),
    )
    transfer_deadline_s: float = Field(
        default=300.0,
        description=(
            "Total time (seconds) a streamed response body may take to transfer, "
            "so a steady trickle or a slow-draining client cannot hold an upstream "
            "connection forever; the stream is aborted when it is exceeded. Unlike "
            "``read_timeout_s`` this bounds the whole body. 0 disables the deadline."
        ),
    )


class RateLimitConfig(BaseModel):
    """Per-caller token-bucket rate limit.

    Keyed on the resolved ``actor_id`` and enforced in a post-auth dependency
    (the actor isn't known at admission time, so this can't be a plain
    pre-auth middleware). The token bucket itself lives on the shared-state
    backend (``RateLimitStore``); with the memory backend the limit is
    per-instance, with Redis it is cluster-wide — no call-site change.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Rate-limit broker executions per caller; over the limit the broker "
            "answers 429 with ``RateLimit-*`` and ``Retry-After`` headers. "
            "Per instance on the memory state backend, cluster-wide on Redis."
        ),
    )
    default_rpm: int = Field(
        default=600,
        description="Sustained refill rate of each caller's token bucket, in requests per minute.",
    )
    burst: int = Field(
        default=100,
        description=(
            "Token-bucket capacity: the most requests a caller can spend at once. "
            "Also the ``RateLimit-Limit`` header value."
        ),
    )


class CircuitBreakerConfig(BaseModel):
    """Per-upstream circuit breaker.

    Counts failures/totals per rolling ``window_s`` on the shared-state
    backend's atomic counters; when the failure ratio crosses
    ``failure_ratio`` the circuit opens (a ``set_if_absent`` latch) and
    fast-fails for ``cooldown_s``. ``observation`` mode tracks + emits
    ``would_block`` but still calls the upstream (safe-rollout dry run).
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Track upstream failures per host and fast-fail calls to a host whose "
            "circuit is open (5xx responses and transport errors count as failures)."
        ),
    )
    enforcement_mode: Literal["blocking", "observation"] = Field(
        default="blocking",
        description=(
            "``blocking`` fast-fails calls while the circuit is open; "
            "``observation`` still calls the upstream and only counts "
            "``circuit_would_block_total``, to validate thresholds before blocking."
        ),
    )
    failure_ratio: float = Field(
        default=0.5,
        description="Failure fraction within ``window_s`` at which the circuit opens.",
    )
    min_calls: int = Field(
        default=10,
        description=(
            "Minimum calls in the window before the failure ratio can open the "
            "circuit, so a single early failure does not trip it."
        ),
    )
    window_s: int = Field(
        default=30,
        description="Rolling window (seconds) over which failures and totals are counted.",
    )
    cooldown_s: int = Field(
        default=15,
        description="Seconds an open circuit fast-fails calls before the upstream is tried again.",
    )


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

    enabled: bool = Field(
        default=False,
        description=(
            "Retry failed upstream attempts when safe: connect failures for any "
            "method, post-send failures (read timeouts, ``retry_statuses``) only "
            "for idempotent HTTP methods. Bounded by the request deadline. "
            "Streamed executions are never retried, and streaming is the default "
            "synchronous path while "
            "``broker.resilience.upstream.stream_passthrough_enabled`` is true."
        ),
    )
    max_attempts: int = Field(
        default=3,
        description=(
            "Total attempts including the first (3 = the initial call plus up to 2 retries)."
        ),
    )
    base_backoff_s: float = Field(
        default=0.2,
        description=(
            "Base delay (seconds) of the exponential backoff "
            "``base_backoff_s * 2**(attempt-1)``, capped at ``max_backoff_s``, "
            "then fully jittered and clamped to the request deadline."
        ),
    )
    max_backoff_s: float = Field(
        default=5.0,
        description="Cap (seconds) on one retry backoff before jitter.",
    )
    retry_statuses: list[int] = Field(
        default_factory=lambda: [429, 502, 503, 504],
        description=(
            "Upstream status codes treated as transient and retried; an upstream "
            "``Retry-After`` on them is honoured, capped at the request deadline."
        ),
    )


class EgressConfig(BaseModel):
    """Outbound SSRF/egress policy for upstream calls.

    Defaults are **strict** (both lists empty): every private range and the
    cloud-metadata host are blocked. The two allowlists exist because the
    broker is frequently installed *inside* a corporate network to bridge to
    internal/legacy APIs, where the default block is a false positive. Both are
    opt-in, auditable operator decisions; the cloud-metadata IP is a **hard,
    non-overridable** deny so a credential-stealing SSRF can never be
    allowlisted by accident.
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
