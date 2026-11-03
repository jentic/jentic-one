"""Top-level ``AppConfig`` and the URLs derived from it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jentic_one.shared.config._state_compat import _fold_deprecated_state_section
from jentic_one.shared.config.admin import AdminConfig
from jentic_one.shared.config.auth import (
    _SPA_CALLBACK_PATH,
    _SPA_CLIENT_ID,
    AuthConfig,
    PlatformClientConfig,
)
from jentic_one.shared.config.broker import BrokerConfig, SecurityConfig
from jentic_one.shared.config.credentials import (
    ControlSurfaceConfig,
    CredentialsConfig,
    DirectOAuth2ProviderConfig,
    VendorRegistryConfig,
)
from jentic_one.shared.config.database import DatabasesConfig
from jentic_one.shared.config.egress import EgressConfig
from jentic_one.shared.config.observability import LoggingConfig, ObservabilityConfig
from jentic_one.shared.config.platform import (
    EntitlementConfig,
    HeldExecutionsConfig,
    ReleaseCheckConfig,
    ServerConfig,
    TelemetryConfig,
)
from jentic_one.shared.config.registry import CatalogConfig, IngestConfig, SearchConfig
from jentic_one.shared.config.runtime import RuntimeConfig, ServicesConfig, WorkerConfig
from jentic_one.shared.state.factory import StateBackendConfig
from jentic_one.shared.url import is_loopback_host, is_loopback_url, origins_equivalent


class AppConfig(BaseModel):
    """Top-level application configuration."""

    # Reject unknown top-level keys that are neither a known field nor a
    # registered extension — surfaces misconfig loudly instead of dropping it.
    # Registered extension sections are extracted by load_config() before
    # validation, so they never reach this model as unknown keys.
    model_config = ConfigDict(extra="forbid")

    databases: DatabasesConfig
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    control: ControlSurfaceConfig = Field(default_factory=ControlSurfaceConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    credentials: CredentialsConfig = Field(default_factory=CredentialsConfig)
    vendors: VendorRegistryConfig = Field(default_factory=VendorRegistryConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    release_check: ReleaseCheckConfig = Field(default_factory=ReleaseCheckConfig)
    entitlement: EntitlementConfig = Field(default_factory=EntitlementConfig)
    held_executions: HeldExecutionsConfig = Field(default_factory=HeldExecutionsConfig)
    apps: list[str] = Field(default_factory=lambda: ["registry", "admin", "control", "auth"])
    state: StateBackendConfig = Field(
        default_factory=StateBackendConfig,
        description=(
            "The process's shared-state backend, built once and shared by every "
            "surface the process serves. The deprecated broker.resilience.backend "
            "section (and its JENTIC__BROKER__RESILIENCE__BACKEND__* env vars) is "
            "still read into it for one release."
        ),
    )

    # Validated extension sub-configs, keyed by their registered section name.
    # Populated by load_config() from top-level keys matching the registry
    # (see register_config). Empty unless a section has been registered.
    extensions: dict[str, BaseModel] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _read_deprecated_state_backend_section(cls, data: Any) -> Any:
        """Fold the deprecated ``broker.resilience.backend`` section into ``state``."""
        return _fold_deprecated_state_section(data)

    @model_validator(mode="after")
    def _ensure_spa_platform_client(self) -> AppConfig:
        """Register the operator SPA as a platform client when config omits it.

        ``AuthConfig`` already does this when ``auth.canonical_base_url`` is
        set; this covers the remaining cases, where only
        ``server.public_base_url`` (or nothing — the serving bind) names the
        origin, so SPA login works on any port without pinning a callback.
        """
        if not any(pc.client_id == _SPA_CLIENT_ID for pc in self.auth.platform_clients):
            base = resolved_auth_base_url(self).rstrip("/")
            redirect_uris = [f"{base}{_SPA_CALLBACK_PATH}"]
            host = self.server.host
            if not effective_auth_base_url(self) and (
                host in _ALL_INTERFACES_HOSTS or is_loopback_host(host)
            ):
                # Derived from a local bind: the SPA builds its callback from
                # window.location.origin, and a local browser reaches the same
                # process as either 127.0.0.1 or localhost — register both
                # whichever alias the bind itself names.
                for alias in ("127.0.0.1", "localhost"):
                    uri = f"http://{alias}:{self.server.port}{_SPA_CALLBACK_PATH}"
                    if uri not in redirect_uris:
                        redirect_uris.append(uri)
            try:
                spa_client = PlatformClientConfig(
                    client_id=_SPA_CLIENT_ID, redirect_uris=redirect_uris
                )
            except ValueError:
                # e.g. a plain-http bind on a LAN IP, which platform redirect
                # URIs refuse. SPA login then needs an explicit https
                # public_base_url; ``_serve`` warns via has_spa_platform_client.
                return self
            self.auth.platform_clients.append(spa_client)
        return self

    @model_validator(mode="after")
    def _worker_concurrency_fits_admin_db(self) -> AppConfig:
        """Refuse a ``worker.concurrency`` the admin database cannot sustain.

        SQLite: a job's handler transaction holds the write lock across its
        upstream call, so a second concurrent job's claim and outcome writes
        time out — a finished job's outcome is lost and the job re-runs on
        reclaim. Postgres: each in-flight job holds about two admin
        connections at once (handler transaction plus its authorization or
        credential-audit session); at ``2 * concurrency >= pool_max`` every
        connection can be held by an outer transaction while its inner one
        waits, so jobs and HTTP requests sharing the pool time out together.
        """
        concurrency = self.worker.concurrency
        if concurrency <= 1:
            return self
        admin = self.databases.admin
        if admin.backend == "sqlite":
            raise ValueError(
                f"worker.concurrency={concurrency} is not supported with a sqlite "
                "admin database; set worker.concurrency to 1"
            )
        if 2 * concurrency >= admin.pool_max:
            raise ValueError(
                f"worker.concurrency={concurrency} needs about {2 * concurrency} "
                "admin-DB connections (2 per in-flight job), which does not fit "
                f"databases.admin.pool_max={admin.pool_max}; raise pool_max above "
                f"{2 * concurrency} or lower worker.concurrency to "
                f"{max(1, (admin.pool_max - 1) // 2)}"
            )
        return self

    def extension(self, name: str) -> BaseModel | None:
        """Return a registered extension config by section name (None if absent)."""
        return self.extensions.get(name)


def resolved_control_egress(config: AppConfig) -> EgressConfig:
    """The egress policy for the platform's OAuth and credential calls.

    Each ``control.egress`` key that is set (not ``None``) wins; every other
    key, and ``dns_pinning_enabled``, comes from ``broker.egress``. With neither
    configured this is the strict default. Callers always pin DNS, whatever
    ``dns_pinning_enabled`` says.
    """
    overrides = {
        name: value
        for name, value in config.control.egress.model_dump().items()
        if value is not None
    }
    return config.broker.egress.model_copy(update=overrides)


_ALL_INTERFACES_HOSTS = frozenset({"", "0.0.0.0", "::"})


def has_spa_platform_client(config: AppConfig) -> bool:
    """Whether the operator SPA (``jentic-one-spa``) is a registered platform client.

    ``False`` only when neither config declares it nor a callback could be
    synthesized for the resolved origin (e.g. a plain-http non-loopback bind);
    SPA login then fails until an https ``server.public_base_url`` is set.
    """
    return any(pc.client_id == _SPA_CLIENT_ID for pc in config.auth.platform_clients)


def broker_links_root_on_broker(config: AppConfig) -> bool:
    """Whether a standalone broker would root its admin-side links on itself.

    ``True`` when this process serves the broker without the admin app and
    ``broker.jobs_api_base_url`` is unset: a held call's ``review_url`` and the
    ``_links`` of held and async executions then fall back to the broker's own
    request origin, where the review page, ``/jobs`` and
    ``/executions/holds`` are not served.
    """
    return (
        "broker" in config.apps
        and "admin" not in config.apps
        and not config.broker.jobs_api_base_url
    )


def bind_origin(config: AppConfig) -> str:
    """The origin this process serves on, as a client on the same host reaches it.

    ``http://{server.host}:{server.port}``, with the all-interfaces binds
    (``0.0.0.0`` / ``::``) reported as ``127.0.0.1`` — the address that
    actually answers. IPv6 literals are bracketed.
    """
    host = config.server.host
    if host in _ALL_INTERFACES_HOSTS:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{config.server.port}"


def effective_auth_base_url(config: AppConfig) -> str:
    """The auth surface's configured public base URL: its own canonical, else the shared one.

    ``""`` when neither is set. Request-scoped callers (discovery issuer, the
    authorize callback / SPA links, the MCP origin gate) pair this with the
    incoming request's origin as the final fallback — see
    ``shared.web.links.deployment_base_url``. Request-less callers use
    ``resolved_auth_base_url`` instead.
    """
    return config.auth.canonical_base_url or config.server.public_base_url or ""


def resolved_auth_base_url(config: AppConfig) -> str:
    """The auth base URL for callers that have no request to derive from.

    The OIDC ``id_token`` issuer, the JWT-Bearer assertion audience, the DCR
    ``registration_client_uri`` and ``GET /instance`` need an absolute origin
    even with nothing configured. They fall back to ``bind_origin``, so a local
    install on any port is self-consistent without pinning a URL anywhere.
    """
    return effective_auth_base_url(config) or bind_origin(config)


def connect_approval_url(config: AppConfig, session_id: str) -> str:
    """The owner-facing deep link to an agent's connect session (token-less).

    Lands on the Agents page with the session id as the ``approve`` query
    param, where the target agent's owner or ``org:admin`` reviews it without
    the ``poll_token``. Absolute even with no public URL configured (it is
    relayed out-of-band by agents), via :func:`resolved_auth_base_url`. Shared
    by the connect session's ``approval_url`` and the broker denials'
    ``provisioning_url`` so both always name the same address.
    """
    base = resolved_auth_base_url(config).rstrip("/")
    return f"{base}/app/agents?approve={session_id}"


@dataclass(frozen=True, slots=True)
class PublicUrlMismatch:
    """One explicitly-configured public URL whose origin doesn't match serving."""

    field: str
    configured: str
    expected: str


def check_public_url_consistency(config: AppConfig) -> list[PublicUrlMismatch]:
    """Find explicitly-set public URLs whose origin disagrees with the server's.

    Two checks, both pure and side-effect-free so ``_serve`` only has to log
    the result. Never raises: a bad-shaped value simply won't compare equal and
    is reported, matching the "warn, don't crash" contract. Unset fields are
    skipped — they self-derive and cannot be wrong.

    1. The per-surface override (``auth.canonical_base_url``) is compared
       (loopback/bind-host aware) against ``server.public_base_url`` when set.
       When it is unset, the bind is the only known origin only on a loopback
       bind; on an all-interfaces bind the public origin is unknowable (proxy,
       ingress, port mapping), so the check is skipped rather than flag every
       correctly-proxied override. The broker's ``jobs_api_base_url`` names
       another service and is not compared.
    2. When the server binds a **loopback** host, it can only be reached from
       the same machine on exactly that port — no port mapping or gateway can
       sit in front of it. A loopback ``server.public_base_url`` or provider
       ``redirect_uri`` on any other port is then unreachable: the classic
       "OAuth callback points at the wrong port" misconfiguration. An
       all-interfaces bind (containers, clusters) is skipped here, because a
       port mapping / NodePort legitimately fronts it on a different port.
    """
    loopback_bind = is_loopback_host(config.server.host)
    expected = config.server.public_base_url or (bind_origin(config) if loopback_bind else "")
    candidates: list[tuple[str, str | None]] = [
        ("auth.canonical_base_url", config.auth.canonical_base_url),
    ]

    mismatches: list[PublicUrlMismatch] = []
    for field_path, value in candidates:
        if expected and value and not origins_equivalent(value, expected):
            mismatches.append(
                PublicUrlMismatch(field=field_path, configured=value, expected=expected)
            )

    if loopback_bind:
        served = bind_origin(config)
        loopback_candidates: list[tuple[str, str | None]] = [
            ("server.public_base_url", config.server.public_base_url),
        ]
        for provider_id, provider in config.credentials.providers.items():
            if isinstance(provider, DirectOAuth2ProviderConfig):
                loopback_candidates.append(
                    (f"credentials.providers.{provider_id}.redirect_uri", provider.redirect_uri)
                )
        for field_path, value in loopback_candidates:
            if value and is_loopback_url(value) and not origins_equivalent(value, served):
                mismatches.append(
                    PublicUrlMismatch(field=field_path, configured=value, expected=served)
                )
    return mismatches
