"""Config, registry and factory for the shared-state backend.

A process builds **one** shared-state backend from the top-level ``state:``
config section (:attr:`jentic_one.shared.context.Context.state_backend`); every
surface the process serves (auth, broker, control) shares it, and the process
closes it once at shutdown.

Backends are looked up by name in a registry. ``memory`` (the in-process
default) and ``redis`` (deprecated) are built in; an extension adds its own
with :func:`register_state_backend` before the app is built::

    from jentic_one.shared.state import (
        SharedStateBackend,
        StateBackendBuildContext,
        register_state_backend,
    )

    def build_cluster(ctx: StateBackendBuildContext) -> SharedStateBackend:
        opts = ClusterOptions.model_validate(ctx.config.options)
        return ClusterStateBackend(opts.url)

    register_state_backend("redis-cluster", build_cluster)

and selects it with ``state.backend: redis-cluster``. Built-in backends read
their own typed sections (``state.memory``, ``state.redis_url``); a registered
backend reads the free-form ``state.options`` mapping and validates it itself.
An unknown name fails at startup with the list of registered names.

The redis implementation is imported lazily so the default memory path needs
no ``redis`` dependency.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, Field, SecretStr

from jentic_one.shared.state.backend import (
    DEFAULT_MEMORY_MAX_BYTES,
    DEFAULT_MEMORY_MAX_ENTRIES,
    DEFAULT_MEMORY_MAX_TOTAL_BYTES,
    ENTRY_OVERHEAD_BYTES,
    MemoryStateBackend,
    NamespaceBudget,
    SharedStateBackend,
)

_logger = structlog.get_logger(__name__)

_DEFAULT_REDIS_KEY_PREFIX = "jentic:broker:"

#: Name of the built-in in-process backend (the default).
MEMORY_BACKEND = "memory"
#: Name of the built-in, deprecated redis backend.
REDIS_BACKEND = "redis"

#: What a backend name may look like, in config and at registration.
BACKEND_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
_BACKEND_NAME_RE = re.compile(BACKEND_NAME_PATTERN)


# Built-in budgets for the broker's ``Idempotency-Key`` entries. ``idem``
# holds one small record per key (status, fingerprint, headers capped at
# 512 bytes: about 1.2 KiB budgeted each); ``idem-body`` holds the stored
# response bodies, so a full body budget only drops bodies from replays and
# never refuses a claim. Each caller may hold at most a tenth of either, so
# one caller cannot lock every other caller out. With the 16 MiB default for
# every other namespace they fit the 192 MiB backend-wide cap, which the
# broker chart's 512Mi memory limit holds with the process baseline.
IDEM_OWNER_SHARE = 0.1

# Built-in budget for the broker's repeated-failure counter (the pre-filter in
# front of the ``execution.repeated_failure`` database scan). Each actor +
# toolkit/credential + operation key failing within two windows uses up to 4
# entries (6 when a toolkit call also carries a credential), so 10,000 entries
# hold about 2,500 concurrently failing keys. Every key is a short, fixed-shape
# bucket or marker (``repeated_failure:<window>:seen:<severity>:<32-hex digest>``
# and a short integer or marker: at most 128 bytes with its value), so the byte
# budget follows the entry bound and ``max_entries`` stays the binding limit.
# A full namespace never hides an emission: the counter fails open and every
# failure runs the scan until two windows after the last refused write. The
# owner segment is the window length, shared by every key, so no owner share.
REPEATED_FAILURE_MAX_ENTRIES = 10_000
_REPEATED_FAILURE_MAX_KEY_AND_VALUE_BYTES = 128
_BUILTIN_NAMESPACE_BUDGETS: dict[str, NamespaceBudget] = {
    "idem": NamespaceBudget(
        max_entries=250_000, max_bytes=128 * 1024 * 1024, max_owner_share=IDEM_OWNER_SHARE
    ),
    "idem-body": NamespaceBudget(
        max_entries=1_000_000, max_bytes=32 * 1024 * 1024, max_owner_share=IDEM_OWNER_SHARE
    ),
    "repeated_failure": NamespaceBudget(
        max_entries=REPEATED_FAILURE_MAX_ENTRIES,
        max_bytes=REPEATED_FAILURE_MAX_ENTRIES
        * (ENTRY_OVERHEAD_BYTES + _REPEATED_FAILURE_MAX_KEY_AND_VALUE_BYTES),
    ),
}

# Built-in per-owner shares for namespaces sized by ``max_entries_per_namespace``
# / ``max_bytes_per_namespace``. ``login-csrf`` holds one nonce per
# unauthenticated login page load, owned by the requesting client's network
# (``login-csrf:<client-key>:<nonce>``; IPv4 address or IPv6 /64, keyed so the
# address itself is not stored). A record is about 900 bytes budgeted (an
# 87-byte key, a 64-byte value and the per-entry overhead), so the 16 MiB
# default holds about 18,600 live nonces and a 0.02 share about 370 per
# client within the 5-minute nonce TTL. That is twice what the default
# ``auth.oauth_rate_limit`` authorize limiter (burst 30 + 30/min, keyed per
# client network the same way) lets one network mint in a TTL, so a large
# shared NAT or corporate egress IP hits the rate limiter before its share,
# while a single host flooding the page (including one rotating addresses
# inside its IPv6 /64) is refused without filling the namespace for everyone
# else.
LOGIN_CSRF_OWNER_SHARE = 0.02
_BUILTIN_OWNER_SHARES: dict[str, float] = {"login-csrf": LOGIN_CSRF_OWNER_SHARE}


class MemoryNamespaceBudgetConfig(BaseModel):
    """Entry-count and approximate byte budget for one memory-backend key namespace."""

    max_entries: int = Field(ge=1, description="Most live entries the namespace may hold.")
    max_bytes: int = Field(
        ge=1,
        description=(
            "Most approximate bytes (key + value + about 750 bytes of per-entry "
            "overhead) the namespace may hold."
        ),
    )
    max_owner_share: float | None = Field(
        default=None,
        gt=0,
        le=1,
        description=(
            "Most of max_entries and max_bytes that one owner (the key's second "
            "``:`` segment: for idem and idem-body the calling actor, for "
            "login-csrf the client's IPv4 address or IPv6 /64) may hold; "
            "an owner over its share is refused without affecting the others. "
            "Unset, it is 0.1 for idem and idem-body, 0.02 for login-csrf and "
            "unlimited elsewhere; 1.0 lets one owner use the whole namespace."
        ),
    )


class MemoryBackendConfig(BaseModel):
    """Bounds for the in-process (``memory``) shared-state backend.

    Every key belongs to a namespace, its prefix up to the first ``:``
    (``idem`` for Idempotency-Key records, ``idem-body`` for their stored
    response bodies, ``auth`` for the auth surface's single-use markers,
    ``login-csrf`` for login CSRF nonces, ``cb`` for circuit-breaker counters
    and their open-circuit report claims, ``broker-authfail`` and
    ``broker-authfail-emit`` for the broker's auth-failure counters,
    ``mcp-session`` and ``mcp-session-cap`` for the MCP session-event dedupe,
    ``repeated_failure`` for the broker's repeated-failure counters, and so
    on). Each namespace has its own budget, so one key kind filling up
    never starves another. A namespace that is full of live entries refuses
    new writes (the request that needed it gets a 503) rather than evicting a
    live entry; the telemetry namespaces (``broker-authfail*``, the ``cb``
    report claims, ``mcp-session*``) only skip the count or fall back to the
    database dedupe instead. Ignored by every other backend.
    """

    max_entries_per_namespace: int = Field(
        default=DEFAULT_MEMORY_MAX_ENTRIES,
        ge=1,
        description=(
            "Default most live entries one key namespace may hold before its writes are refused."
        ),
    )
    max_bytes_per_namespace: int = Field(
        default=DEFAULT_MEMORY_MAX_BYTES,
        ge=1,
        description=(
            "Default most approximate bytes (key + value + about 750 bytes of "
            "per-entry overhead) one key namespace may hold before its writes are refused."
        ),
    )
    max_total_bytes: int = Field(
        default=DEFAULT_MEMORY_MAX_TOTAL_BYTES,
        ge=1,
        description=(
            "Most approximate bytes all namespaces of the memory backend may hold "
            "together (201326592, 192 MiB, by default); a write past it is refused "
            "like a full namespace, never by evicting a live entry. A process "
            "builds one backend shared by every surface it serves, so this caps "
            "the whole process's shared state. Raise the container memory limit "
            "together with it."
        ),
    )
    namespaces: dict[str, MemoryNamespaceBudgetConfig] = Field(
        default_factory=dict,
        description=(
            "Per-namespace budget overrides, keyed by namespace (the key prefix "
            "before the first ``:``). Unless overridden here, idem (Idempotency-Key "
            "records) is 250000 entries / 134217728 bytes (128 MiB) and idem-body "
            "(their stored response bodies) 1000000 entries / 33554432 bytes "
            "(32 MiB), each with a 0.1 per-caller share; login-csrf (login page "
            "CSRF nonces) uses max_entries_per_namespace / max_bytes_per_namespace "
            "with a 0.02 per-client share, about 370 live nonces per IPv4 address "
            "or IPv6 /64 at the defaults (about 900 bytes each, 5-minute TTL), "
            "twice what the default authorize rate limit lets one client network "
            "request in that time; repeated_failure (the broker's repeated-failure "
            "counters, which gate the execution.repeated_failure database scan) "
            "is 10000 entries / 8780000 bytes (about 8.4 MiB), about 2500 "
            "concurrently failing actor + credential + operation keys, and fails "
            "open when full (every failure runs the scan); every other namespace "
            "uses max_entries_per_namespace / max_bytes_per_namespace. The "
            "sustained idempotent request rate idem can hold is min(max_entries, "
            "max_bytes / record_bytes) / broker.idempotency.ttl_s, where "
            "record_bytes is about 1000 bytes plus the response headers (capped at "
            "512 bytes): about 30 req/s at the defaults and a 1 h TTL, and a tenth "
            "of that per caller. Above it, Idempotency-Key requests get 503 "
            "idempotency_unavailable; bodies (about 830 bytes + body length each) "
            "stop being stored for replay sooner, about 5 req/s of 1 KiB bodies, "
            "without refusing requests."
        ),
    )

    def budgets(self) -> dict[str, NamespaceBudget]:
        """The per-namespace budgets: built-in defaults, then configured overrides."""
        budgets = dict(_BUILTIN_NAMESPACE_BUDGETS)
        for name, builtin_share in _BUILTIN_OWNER_SHARES.items():
            budgets[name] = NamespaceBudget(
                max_entries=self.max_entries_per_namespace,
                max_bytes=self.max_bytes_per_namespace,
                max_owner_share=builtin_share,
            )
        for name, b in self.namespaces.items():
            share = b.max_owner_share
            if share is None and name in budgets:
                share = budgets[name].max_owner_share
            budgets[name] = NamespaceBudget(
                max_entries=b.max_entries, max_bytes=b.max_bytes, max_owner_share=share
            )
        return budgets


class StateBackendConfig(BaseModel):
    """The process's shared-state backend (top-level ``state:`` section).

    One backend per process holds the rate-limit buckets, circuit-breaker
    counters, ``Idempotency-Key`` records, the auth surface's single-use
    markers, the broker's auth-failure counters and the MCP session-event
    dedupe of every surface the process serves.
    """

    backend: str = Field(
        default=MEMORY_BACKEND,
        pattern=BACKEND_NAME_PATTERN,
        description=(
            "Name of the shared-state backend to build: ``memory`` (in-process, "
            "the default), ``redis`` (deprecated), or a name an extension "
            "registered with ``register_state_backend``. An unregistered name "
            "fails at startup with the list of registered names."
        ),
    )
    redis_url: SecretStr | None = Field(
        default=None,
        description="Connection URL of the deprecated ``redis`` backend; required by it.",
    )
    redis_key_prefix: str = Field(
        default=_DEFAULT_REDIS_KEY_PREFIX,
        description=(
            "Prefix the deprecated ``redis`` backend puts on every key; set a "
            "per-environment value when deployments share one Redis."
        ),
    )
    memory: MemoryBackendConfig = Field(default_factory=MemoryBackendConfig)
    options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Settings for a backend registered by an extension, passed to its "
            "builder unvalidated (the builder validates them). Ignored by the "
            "built-in memory and redis backends."
        ),
    )


@dataclass(frozen=True, slots=True)
class StateBackendBuildContext:
    """What a :data:`StateBackendBuilder` receives.

    Attributes:
        config: The resolved ``state:`` section. A registered backend reads
            its settings from ``config.options``.
        apps: The surfaces the process serves (for example
            ``frozenset({"broker"})``), so a builder can size a pool or label
            its metrics per process shape. Do not derive key prefixes from
            it: every process serving a surface must see the same keys.
    """

    config: StateBackendConfig
    apps: frozenset[str] = field(default_factory=frozenset)


#: Builds a ready shared-state backend. Called once per process at startup;
#: the process calls the result's ``aclose()`` once at shutdown. Raise to
#: fail startup (for example on invalid ``state.options``).
StateBackendBuilder = Callable[[StateBackendBuildContext], SharedStateBackend]


def _build_memory(ctx: StateBackendBuildContext) -> SharedStateBackend:
    memory = ctx.config.memory
    return MemoryStateBackend(
        max_entries=memory.max_entries_per_namespace,
        max_bytes=memory.max_bytes_per_namespace,
        max_total_bytes=memory.max_total_bytes,
        namespace_budgets=memory.budgets(),
    )


def _build_redis(ctx: StateBackendBuildContext) -> SharedStateBackend:
    cfg = ctx.config
    if not cfg.redis_url:
        raise RuntimeError(f"the {REDIS_BACKEND} state backend requires state.redis_url")
    _logger.warning(
        "state_backend_deprecated",
        backend=REDIS_BACKEND,
        detail=(
            "the built-in redis shared-state backend is deprecated and will be "
            "removed; jentic-one supports one process per surface on the memory "
            "backend, and multi-process shared state comes from a backend an "
            "extension registers with register_state_backend"
        ),
        actionable_step=(
            "Set state.backend to memory and run one process per surface, or "
            "select a registered shared backend."
        ),
    )
    if cfg.redis_key_prefix == _DEFAULT_REDIS_KEY_PREFIX:
        _logger.warning(
            "state_backend_default_redis_key_prefix",
            redis_key_prefix=cfg.redis_key_prefix,
            detail="the redis state backend is using the default key prefix",
            actionable_step=(
                "Set a per-environment state.redis_key_prefix to avoid "
                "cross-deployment key collisions on a shared Redis."
            ),
        )
    # Imported lazily so the default memory path needs no ``redis`` dependency.
    # ``redis`` ships as an optional extra (``pip install jentic-one[redis]``),
    # so surface an actionable error rather than a bare ImportError.
    try:
        from jentic_one.shared.state.redis import RedisStateBackend
    except ImportError as exc:
        raise RuntimeError(
            f"the {REDIS_BACKEND} state backend requires the optional 'redis' "
            "dependency; install it with `pip install jentic-one[redis]` "
            "(or `uv sync --extra redis`)"
        ) from exc

    return RedisStateBackend(cfg.redis_url.get_secret_value(), key_prefix=cfg.redis_key_prefix)


_BUILTIN_BACKENDS: dict[str, StateBackendBuilder] = {
    MEMORY_BACKEND: _build_memory,
    REDIS_BACKEND: _build_redis,
}
_REGISTRY: dict[str, StateBackendBuilder] = dict(_BUILTIN_BACKENDS)
_REGISTRY_LOCK = threading.Lock()


def register_state_backend(name: str, builder: StateBackendBuilder) -> None:
    """Register a shared-state backend under *name*, selectable as ``state.backend``.

    Call at import time (for example in an extension package's ``__init__``),
    before the app is built. Idempotent for the same ``(name, builder)``.

    Raises:
        ValueError: *name* is not lowercase letters, digits, ``-`` and ``_``
            (starting with a letter or digit), is a built-in backend, or is
            already registered to a different builder.
    """
    if not _BACKEND_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid state backend name {name!r}: use lowercase letters, digits, "
            "'-' and '_', starting with a letter or digit"
        )
    if name in _BUILTIN_BACKENDS:
        raise ValueError(f"state backend {name!r} is built in and cannot be re-registered")
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(name)
        if existing is not None and existing is not builder:
            raise ValueError(f"state backend {name!r} is already registered to {existing!r}")
        _REGISTRY[name] = builder


def registered_state_backends() -> tuple[str, ...]:
    """The names ``state.backend`` may take, sorted."""
    with _REGISTRY_LOCK:
        return tuple(sorted(_REGISTRY))


def unregister_state_backend(name: str) -> None:
    """Remove a registered (non-built-in) backend. For tests only."""
    if name in _BUILTIN_BACKENDS:
        raise ValueError(f"state backend {name!r} is built in and cannot be unregistered")
    with _REGISTRY_LOCK:
        _REGISTRY.pop(name, None)


def build_state_backend(
    cfg: StateBackendConfig, *, apps: frozenset[str] = frozenset()
) -> SharedStateBackend:
    """Build the backend ``cfg.backend`` names.

    Args:
        cfg: The ``state:`` section.
        apps: The surfaces the process serves, passed to the builder.

    Returns:
        A concrete backend implementing all three state roles.

    Raises:
        RuntimeError: ``cfg.backend`` is not registered, or its builder
            refused the config (for example ``redis`` without ``redis_url``
            or without the optional ``redis`` dependency).
    """
    with _REGISTRY_LOCK:
        builder = _REGISTRY.get(cfg.backend)
        names = sorted(_REGISTRY)
    if builder is None:
        raise RuntimeError(
            f"unknown shared-state backend {cfg.backend!r} (state.backend); registered "
            f"backends: {', '.join(names)}. A backend outside these must be registered "
            "with jentic_one.shared.state.register_state_backend before the app is built."
        )
    backend = builder(StateBackendBuildContext(config=cfg, apps=apps))
    _logger.info("state_backend_built", backend=cfg.backend, apps=sorted(apps))
    return backend
