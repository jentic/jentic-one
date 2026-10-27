"""Broker configuration: resilience, idempotency, JWT trust, and retired-key warnings."""

from __future__ import annotations

import os
import threading
from typing import Any

import structlog
from pydantic import BaseModel, Field, SecretStr, ValidationInfo, field_validator, model_validator

from jentic_one.shared.config._common import _normalize_optional_base_url
from jentic_one.shared.config.egress import (
    CircuitBreakerConfig,
    EgressConfig,
    RateLimitConfig,
    RetryConfig,
    UpstreamClientConfig,
)
from jentic_one.shared.state.factory import MEMORY_BACKEND

_logger = structlog.get_logger(__name__)


class BrokerResilienceConfig(BaseModel):
    """Resilience envelope: admission + rate limit / circuit.

    The rate limiter and circuit breaker run on the process's shared-state
    backend (the top-level ``state:`` section). Queue backpressure /
    async-credential / retention knobs are future work.
    """

    max_in_flight: int = Field(
        default=200,
        description=(
            "Hard admission cap on concurrently executing brokered calls, "
            "**per broker process** — replicas multiply it. At the cap, new "
            "requests are shed with 429 + ``Retry-After: shed_retry_after_s``."
        ),
    )
    shed_retry_after_s: int = Field(
        default=5,
        description=(
            "``Retry-After`` (seconds) returned with the 429 when admission "
            "sheds at ``max_in_flight``."
        ),
    )
    request_deadline_s: float = Field(
        default=30.0,
        description=(
            "Overall wall-clock budget (seconds) for one upstream call, "
            "enforced by the always-on DeadlineRunner outside the circuit "
            "breaker — distinct from the per-attempt connect/read timeout on "
            "the transport client. Exceeding it returns 504 with a ``wait`` "
            "agent directive; 0 disables the budget. Size ABOVE upstream "
            "read timeouts so a single healthy slow attempt isn't pre-empted "
            "by the envelope deadline."
        ),
    )
    readiness_saturation_threshold: float = Field(
        default=0.9,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of ``max_in_flight`` at/above which ``/ready`` reports "
            "unready, so the LB drains this instance before it hits the hard "
            "admission shed wall. Kept < 1.0 for that headroom."
        ),
    )
    upstream: UpstreamClientConfig = Field(default_factory=UpstreamClientConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)


MEMORY_IDEMPOTENCY_TTL_S = 3_600.0
SHARED_IDEMPOTENCY_TTL_S = 86_400.0


class IdempotencyConfig(BaseModel):
    """``Idempotency-Key`` replay store.

    Sync ``FULL``-mode replay over the shared-state ``AtomicStore`` (the
    ``state:`` backend). Two TTLs: a *short* ``pending_ttl_s`` claim
    (sized to the request deadline + buffer, so a crash mid-flight frees the key
    in seconds rather than the full replay window) promoted to the *long*
    ``ttl_s`` only once the response is stored. Responses over
    ``max_response_bytes`` are recorded without their body (replay still prevents
    a duplicate side-effect; only byte-for-byte body replay is dropped).

    Compliance modes (``metadata_only`` / kill-switch), ``require_for_mutations``,
    async same-``job_id`` replay, and at-rest encryption are future work.
    """

    enabled: bool = True
    ttl_s: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Replay window of a completed Idempotency-Key record, in seconds. Unset, "
            f"it is {MEMORY_IDEMPOTENCY_TTL_S:.0f} (1 h) on the memory shared-state "
            f"backend and {SHARED_IDEMPOTENCY_TTL_S:.0f} (24 h) on any other backend. On memory, "
            "every record is held in process for this long, so the TTL divides the "
            "sustained idempotent request rate the idem budget can hold."
        ),
    )
    # Claim TTL — size to the request deadline (+buffer); poison-pill guard.
    pending_ttl_s: float = 35.0
    # Bodies larger than this are NOT stored for byte-for-byte replay.
    max_response_bytes: int = 256 * 1024

    def replay_ttl_s(self, backend: str) -> float:
        """``ttl_s``, or the default for the *backend* name when it is unset."""
        if self.ttl_s is not None:
            return self.ttl_s
        if backend == MEMORY_BACKEND:
            return MEMORY_IDEMPOTENCY_TTL_S
        return SHARED_IDEMPOTENCY_TTL_S


# Asymmetric signature algs are the only ones the hardened JWT path accepts.
# HS* (HMAC) is rejected so a leaked/guessed shared secret can't be used to forge
# a token signed against a public key (the RS↔HS key-confusion attack), and
# `none` is never an algorithm.
_ASYMMETRIC_JWT_ALGS: frozenset[str] = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA"}
)


class TrustedIssuerConfig(BaseModel):
    """A single trusted token issuer (IdP) and where to fetch its public keys.

    The verifier holds one JWKS client per issuer; an unknown ``kid`` triggers a
    bounded re-fetch (key rotation picked up without a restart). ``algorithms``
    is the per-issuer asymmetric allowlist — HMAC/``none`` are rejected here so an
    issuer can't be downgraded to a symmetric/forgeable signature.
    """

    issuer: str
    jwks_url: str = Field(
        description=(
            "URL of the issuer's published JWKS. Must be https:// when "
            "JENTIC_ENV=production; plain http:// is accepted outside production "
            "(local/CI stacks) and logs a warning at config load."
        ),
    )
    algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])

    @field_validator("jwks_url")
    @classmethod
    def _https_in_production(cls, value: str, info: ValidationInfo) -> str:
        # Only http(s) is a JWKS endpoint: the fetcher (urllib) would also open
        # file:// and ftp:// URLs.
        if value.startswith("https://"):
            return value
        if not value.startswith("http://"):
            raise ValueError(
                f"jwks_url must be https:// (or http:// outside production): {value!r}"
            )
        issuer = info.data.get("issuer")
        # A plaintext JWKS endpoint is MITM-able into trusting attacker keys, so
        # production (the same JENTIC_ENV gate as the secret guards; unset is
        # development) keeps https mandatory.
        if os.environ.get("JENTIC_ENV", "development") == "production":
            raise ValueError(
                f"jwks_url must be https:// in production (JENTIC_ENV=production) "
                f"for trusted issuer {issuer!r}; http:// is only allowed outside production"
            )
        _logger.warning(
            "trusted_issuer_jwks_url_plain_http",
            issuer=issuer,
            detail=(
                "this trusted issuer's JWKS is fetched over plain http, so its signing "
                "keys are not integrity-protected in transit; use https:// outside "
                "local/CI stacks (required when JENTIC_ENV=production)"
            ),
        )
        return value

    @field_validator("algorithms")
    @classmethod
    def _asymmetric_only(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("algorithms must list at least one asymmetric alg")
        bad = [a for a in value if a not in _ASYMMETRIC_JWT_ALGS]
        if bad:
            raise ValueError(f"only asymmetric JWT algs allowed (no HMAC/none): rejected {bad}")
        return value


class JwtVerificationConfig(BaseModel):
    """Hardened inbound-JWT verification for the broker edge.

    When ``trusted_issuers`` is non-empty the broker verifies self-contained JWTs
    against the issuers' published JWKS (asymmetric, key-rotation-aware) and
    enforces ``iss`` (trusted set), ``aud`` (``audience``), and ``exp``/``nbf``
    with ``leeway_s`` clock-skew. Empty (the default) leaves the asymmetric path
    off — the legacy HS256 ``broker.jwt_secret`` path (dev/test) still applies.
    """

    audience: str | None = None
    # Clock-skew tolerance applied to exp/nbf/iat (seconds).
    leeway_s: float = 60.0
    trusted_issuers: list[TrustedIssuerConfig] = Field(default_factory=list)


class SecurityConfig(BaseModel):
    """Platform security event thresholds."""

    auth_failure_event_threshold: int = Field(default=10, ge=1)
    # How far ahead of an OAuth token's ``expires_at`` the credential-expiry
    # scanner starts emitting ``credential.expiring_soon`` (a token sits in the
    # warning state for this whole window until it actually expires).
    credential_expiring_soon_window_h: int = Field(default=72, ge=1)
    # The credential-expiry scanner runs on the worker tick clock; it sweeps
    # every Nth tick rather than every tick (a token's expiry shifts on the
    # scale of hours, so a sweep per minute is ample). One tick ≈ the worker
    # poll interval (~2s), so the default ~60 ticks is roughly every 2 minutes.
    credential_expiry_sweep_interval_ticks: int = Field(default=60, ge=1)
    # Number of failed executions for one actor+credential+operation within the
    # rolling window before an ``execution.repeated_failure`` event is emitted.
    execution_repeated_failure_threshold: int = Field(default=5, ge=1)
    # Rolling window (seconds) over which repeated failures are counted and over
    # which a single ``execution.repeated_failure`` event is deduplicated.
    execution_repeated_failure_window_s: int = Field(default=300, ge=1)
    # Failure count at/above which the repeated-failure event escalates from
    # ``error`` to ``critical`` severity. Sized above the base threshold.
    execution_repeated_failure_critical_threshold: int = Field(default=20, ge=1)


class BrokerConfig(BaseModel):
    """Broker surface configuration."""

    upstream_timeout_s: float = Field(
        default=30.0,
        description=(
            "Timeout (seconds) handed to the execution runner for one upstream "
            "call on the buffered sync path and the async job worker. Distinct "
            "from the transport-level ``broker.resilience.upstream`` timeouts "
            "and the ``request_deadline_s`` envelope."
        ),
    )
    resolve_cache_ttl_seconds: float = 3.0
    # Short TTL (seconds) for the per-instance credential-binding derivation
    # cache. Wraps the cross-DB `derive_credentials` lookup so the per-request
    # Admin+Control double hit is served from cache. Agent/credential bindings
    # change infrequently, so a short TTL bounds revocation staleness (the
    # cache is per instance, so a grant/revoke is consistent cluster-wide only
    # after the TTL lapses on each node) while removing the hot-path lookup.
    # Authorization correctness never depends on the cache — it is a latency
    # optimization over the authoritative DB lookup. 0 disables it.
    # Key name is legacy (it originally bounded the toolkit-derivation cache,
    # deleted in theme-5 Phase 6b); kept for operator config compatibility.
    toolkit_cache_ttl_s: float = 3.0
    # Short TTL (seconds) for the per-instance permission-rule cache.
    # Caches the ordered rules per (agent, credential) binding. Same staleness
    # trade-off as toolkit_cache_ttl_s — a rule change propagates after the TTL.
    rule_cache_ttl_s: float = 3.0
    # Upper bound on entries in the per-worker permission-rule LRU. Size
    # against agents x credentials — each active (agent, credential) binding
    # is one entry. Eviction is LRU by entry count (the TTL only bounds
    # staleness, never memory).
    rule_cache_max_entries: int = 5_000
    streaming_persist_timeout_s: float = Field(
        default=10.0,
        gt=0,
        description=(
            "Budget (seconds) for writing one streaming execution's record and "
            "lifecycle event. The write runs after the response, off the request, "
            "so it never delays the client; past the budget it is abandoned, "
            "logged and counted on ``app.background_tasks`` "
            "(``kind=streaming_execution_persist``, ``outcome=timed_out``)."
        ),
    )
    streaming_persist_max_concurrency: int = Field(
        default=5,
        gt=0,
        description=(
            "Per-process cap on concurrent post-response persists of streaming "
            "execution records (each holds one admin-DB connection). Excess "
            "persists wait for a slot, up to "
            "``broker.streaming_persist_acquire_timeout_s``; the waiting and "
            "writing counts are exported as "
            "``broker.streaming_execution.persist_pending``. The effective cap "
            "is clamped to ``databases.admin.pool_max - 1`` (minimum 1; a "
            "``streaming_persist_limit_clamped`` warning is logged when it "
            "applies), so with ``pool_max >= 2`` persists never take every "
            "connection of the admin base pool. ``pool_max`` is the base pool "
            "size only; SQLAlchemy's default overflow can open up to 10 more "
            "connections on top of it."
        ),
    )
    streaming_persist_acquire_timeout_s: float = Field(
        default=5.0,
        gt=0,
        description=(
            "How long (seconds) a streaming execution persist waits for one of "
            "the ``broker.streaming_persist_max_concurrency`` slots. Past it the "
            "record is abandoned: logged as "
            "``streaming_execution_persist_abandoned`` and counted on "
            "``broker.streaming_execution.persist_failures`` "
            "(``reason=backlog_timeout``). The wait runs after the response is "
            "complete or the client has disconnected, when the request no "
            "longer holds its admission slot, so the wait delays neither the "
            "client nor ``/ready``. The timeout bounds how long a stalled "
            "admin database can keep records queued in memory, including at "
            "shutdown; it does not bound the write itself once a slot is held."
        ),
    )
    # Absolute public base URL of the admin jobs API, used to build the 202
    # `_links.self` pointer for async executions (e.g. "https://api.example.com").
    # None keeps the legacy broker-relative `/jobs/{id}` link.
    jobs_api_base_url: str | None = None
    # Shared secret for the self-contained-JWT path. None disables
    # the JWT path entirely (opaque tokens only). The minimal verifier is HS256
    # signature + exp; TODO hardens (JWKS, iss/aud, alg allowlist) before
    # this is enabled in production.
    jwt_secret: SecretStr | None = None
    # Hardened inbound-JWT verification: trusted-issuer JWKS, iss/aud/nbf
    # + clock-skew, strict asymmetric alg allowlist. When trusted_issuers is set
    # it supersedes the HS256 jwt_secret path for self-contained JWTs.
    jwt_verification: JwtVerificationConfig = Field(default_factory=JwtVerificationConfig)
    resilience: BrokerResilienceConfig = Field(default_factory=BrokerResilienceConfig)
    idempotency: IdempotencyConfig = Field(default_factory=IdempotencyConfig)
    egress: EgressConfig = Field(default_factory=EgressConfig)

    _normalize_public_urls = field_validator("jobs_api_base_url")(_normalize_optional_base_url)

    @model_validator(mode="before")
    @classmethod
    def _reject_retired_direct_bindings_flag(cls, data: Any) -> Any:
        """Fail loudly on ``broker.direct_bindings_enabled: false``.

        The flag was deleted in theme-5 Phase 6b along with the toolkit
        path it selected — direct bindings are the only path. Unknown keys
        are otherwise ignored here, so without this check an operator who
        pinned ``false`` to stay on toolkits would boot on direct bindings
        without noticing. ``true`` (the old default) is harmless: ignored,
        with a one-time deprecation warning.
        """
        if isinstance(data, dict) and "direct_bindings_enabled" in data:
            value = data["direct_bindings_enabled"]
            if value is False or str(value).strip().lower() in {"false", "0", "no", "off"}:
                raise ValueError(
                    "broker.direct_bindings_enabled was removed in theme-5 Phase 6b: "
                    "the toolkit path it selected no longer exists and direct "
                    "agent-credential bindings are the only access path. Remove "
                    "the setting (config file or JENTIC__BROKER__DIRECT_BINDINGS_ENABLED); "
                    "if toolkit-bound agents lose access, run the Phase-6a flattening "
                    "(docs/development/releasing.md)."
                )
            _warn_retired_direct_bindings_flag_once()
            data = {k: v for k, v in data.items() if k != "direct_bindings_enabled"}
        return data

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_account_linking_base_url(cls, data: Any) -> Any:
        """Ignore ``broker.account_linking_base_url`` with a one-time warning.

        Denial directives link to the agent's open connect session on the
        deployment's own UI (``connect_approval_url``), so there is no
        separate account-linking origin to configure. A leftover value is
        harmless and never fails boot.
        """
        if isinstance(data, dict) and _RETIRED_ACCOUNT_LINKING_KEY in data:
            _warn_retired_account_linking_base_url_once()
            data = {k: v for k, v in data.items() if k != _RETIRED_ACCOUNT_LINKING_KEY}
        return data


_retired_direct_bindings_flag_warned = threading.Event()


def _warn_retired_direct_bindings_flag_once() -> None:
    """One deprecation WARNING per process for a leftover ``true`` (it is ignored).

    Config is validated more than once per process (reloads, per-surface
    copies), so the line is latched to avoid repeating on every validation.
    """
    if _retired_direct_bindings_flag_warned.is_set():
        return
    _retired_direct_bindings_flag_warned.set()
    _logger.warning(
        "config_retired_setting_ignored",
        setting="broker.direct_bindings_enabled",
        detail=(
            "removed in theme-5 Phase 6b; direct agent-credential bindings are the "
            "only access path and the value is ignored"
        ),
        actionable_step=(
            "Remove broker.direct_bindings_enabled from the config file or "
            "JENTIC__BROKER__DIRECT_BINDINGS_ENABLED from the environment."
        ),
    )


_RETIRED_ACCOUNT_LINKING_KEY = "account_linking_base_url"
_retired_account_linking_warned = threading.Event()


def _warn_retired_account_linking_base_url_once() -> None:
    """One WARNING per process for the leftover setting (it is ignored).

    Latched like :func:`_warn_retired_direct_bindings_flag_once`: config is
    validated more than once per process.
    """
    if _retired_account_linking_warned.is_set():
        return
    _retired_account_linking_warned.set()
    _logger.warning(
        "config_retired_setting_ignored",
        setting=f"broker.{_RETIRED_ACCOUNT_LINKING_KEY}",
        detail=(
            "denial directives carry a provisioning_url only when the agent has an "
            "open connect session, linking to it on this deployment's UI; the value "
            "is ignored"
        ),
        actionable_step=(
            f"Remove broker.{_RETIRED_ACCOUNT_LINKING_KEY} from the config file or "
            "JENTIC__BROKER__ACCOUNT_LINKING_BASE_URL from the environment."
        ),
    )
