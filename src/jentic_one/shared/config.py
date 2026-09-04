"""Configuration schema and loader for jentic-one."""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlparse

import structlog
import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from jentic_one.shared.state.factory import StateBackendConfig

_logger = structlog.get_logger(__name__)

# Per-process cache of dev-generated secrets, keyed by dotted config path.
# Keeps repeated config loads within one process (tests, re-reads) signing
# with the same key, while never persisting a secret-shaped literal anywhere:
# shipped images must not contain default credentials (AWS Marketplace
# container policy — their scanner flags hardcoded defaults even when a
# production guard would reject them at boot).
_EPHEMERAL_DEV_SECRETS: dict[str, SecretStr] = {}

# A configured value matching this is a forgotten placeholder (copied from an
# old example or config), never a real secret — any such value is publicly
# known, so signing with it is equivalent to signing with no key at all.
# Expressed as a pattern, not a literal, so no secret-shaped string ships in
# the image.
_PLACEHOLDER_SECRET_RE = re.compile(r"change.?me", re.IGNORECASE)


def _require_or_generate_secret(value: SecretStr, *, field_path: str) -> SecretStr:
    """Return the configured secret, or mint a per-process one for dev.

    A single guard for every scalar secret the app cannot safely default
    (admin ``jwt_secret``, invite ``pepper``, connect ``state_secret``). The
    shipped default for all of them is empty:

    - ``JENTIC_ENV=production``: an empty/whitespace value — or a change-me
      placeholder — is a hard ``ConfigError``. The install path (jenticctl
      install, the Helm chart's generated Secret) provides real values, so an
      unusable value reaching production means that step was skipped.
    - development/test: generate a random per-process secret so the app still
      boots with zero configuration. Cached per ``field_path`` so repeated
      loads in one process agree; deliberately NOT persisted, so restarts
      rotate it (dev sessions ending on restart beats shipping a known key).
      Per-process means multi-process dev setups (standalone surfaces,
      ``--workers > 1``) each mint their own value; each secret is consumed
      only by its own surface, so a shared value is never assumed.
    """
    secret = value.get_secret_value()
    if secret.strip() and not _PLACEHOLDER_SECRET_RE.search(secret):
        return value
    if os.environ.get("JENTIC_ENV", "development") == "production":
        raise ConfigError(
            f"{field_path} must be explicitly configured in production — "
            "empty and placeholder values are rejected "
            "(jenticctl install generates one automatically)"
        )
    if field_path not in _EPHEMERAL_DEV_SECRETS:
        _EPHEMERAL_DEV_SECRETS[field_path] = SecretStr(secrets.token_urlsafe(32))
        _logger.info("generated ephemeral dev secret", field_path=field_path)
    return _EPHEMERAL_DEV_SECRETS[field_path]


class ConfigError(Exception):
    """Raised when configuration is invalid or incomplete."""


class DatabaseConfig(BaseModel):
    """Connection parameters for a single database.

    Supports two backends:

    - ``postgres`` (default): uses host/port/user/password/name + schema_name.
    - ``sqlite``: uses ``path`` (a single-file database). The Postgres
      connection fields are ignored.
    """

    backend: Literal["postgres", "sqlite"] = Field(
        default="postgres",
        description=(
            "Database engine for this connection: ``postgres`` uses the server "
            "connection fields; ``sqlite`` uses ``path`` and ignores them."
        ),
    )
    host: str = Field(default="localhost", description="PostgreSQL server hostname.")
    port: int = Field(default=5432, description="PostgreSQL server port.")
    name: str = Field(
        default="",
        description="PostgreSQL database name. Required for the ``postgres`` backend.",
    )
    user: str = Field(default="postgres", description="PostgreSQL role to connect as.")
    password: SecretStr = Field(
        default=SecretStr(""), description="Password for the PostgreSQL role."
    )
    pool_max: int = Field(
        default=10,
        description="Maximum connections in the PostgreSQL connection pool (ignored for SQLite).",
    )
    # The identifier pattern is defense-in-depth so a hostile config value
    # cannot escape the quoted identifier (SEC-2).
    schema_name: str = Field(
        default="public",
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description=(
            "PostgreSQL schema for this connection: created if missing by the "
            "migration runner (``CREATE SCHEMA IF NOT EXISTS``) and put first on "
            "``search_path``, so several connections can share one database."
        ),
    )
    path: str | None = Field(
        default=None,
        description=(
            "SQLite: filesystem path to the database file (``:memory:`` for "
            "in-memory). Required for the ``sqlite`` backend."
        ),
    )
    busy_timeout_ms: int = Field(
        default=5000,
        description=(
            "SQLite: per-connection wait for a held write lock, in milliseconds, "
            "before failing with ``database is locked``."
        ),
    )
    journal_mode: str = Field(
        default="WAL",
        description=(
            "SQLite journal mode (persistent per database file). ``WAL`` lets a "
            "writer and readers proceed concurrently instead of blocking each other."
        ),
    )

    @model_validator(mode="after")
    def _validate_backend(self) -> DatabaseConfig:
        if self.backend == "postgres":
            if not self.name:
                raise ValueError("postgres backend requires a database 'name'")
        elif self.backend == "sqlite" and not self.path:
            raise ValueError("sqlite backend requires a 'path'")
        return self


class ServicesConfig(BaseModel):
    """Service-level settings (immutable after boot)."""

    request_timeout_s: float = 30.0
    retry_max: int = 3
    retry_backoff_s: float = 1.0


class WorkerConfig(BaseModel):
    """Background job-worker durability knobs.

    The worker claims a job, sets a **visibility deadline** (``visibility_timeout_s``
    from claim), and processes it. A job left ``RUNNING`` past that deadline by a
    crashed worker/pod is reclaimed on a later poll. A handler failure requeues the
    job with capped exponential backoff up to ``max_attempts`` claims; beyond that
    it is dead-lettered (poison-message handling) rather than looped forever.
    """

    # How long a claimed (RUNNING) job stays invisible to other workers. Size it
    # safely above the longest expected handler runtime (upstream timeout + slack)
    # so a healthy-but-slow job is never reclaimed mid-flight and double-processed.
    visibility_timeout_s: float = 120.0
    # Total claims before a repeatedly-failing job is dead-lettered.
    max_attempts: int = 5
    # Backoff before a failed job becomes claimable again: min(base * 2**(n-1), max).
    retry_backoff_base_s: float = 2.0
    retry_backoff_max_s: float = 60.0
    # Bounded wait for in-flight jobs to finish on graceful drain; past
    # this the worker stops claiming and lets the still-RUNNING job be reclaimed
    # via its visibility timeout after restart (no work dropped).
    drain_timeout_s: float = 25.0


class RuntimeConfig(BaseModel):
    """Hot-reloadable runtime flags."""

    debug: bool = False
    log_level: str = "INFO"
    maintenance_mode: bool = False

    def reload(self, overrides: dict[str, Any]) -> RuntimeConfig:
        """Return a new RuntimeConfig with overrides applied."""
        data = self.model_dump()
        data.update(overrides)
        return RuntimeConfig.model_validate(data)


class LoggingConfig(BaseModel):
    """File logging sink (in addition to stdout)."""

    file_enabled: bool = False
    file_dir: str = ".jentic/logs"
    file_name: str = "app.log"
    file_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    file_backup_count: int = 5


class DatabasesConfig(BaseModel):
    """Named database connections."""

    registry: DatabaseConfig
    admin: DatabaseConfig
    control: DatabaseConfig


class MetricsConfig(BaseModel):
    """Metrics exporter configuration."""

    exporter: Literal["otlp", "prometheus", "none"] = "otlp"
    export_interval_seconds: int = 15


class TracingConfig(BaseModel):
    """Tracing exporter configuration."""

    exporter: Literal["otlp", "none"] = "otlp"


class ObservabilityConfig(BaseModel):
    """Observability settings (metrics, tracing knobs)."""

    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)


class AdminAuthConfig(BaseModel):
    """Admin authentication settings."""

    jwt_secret: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "HMAC secret that signs admin login JWTs. Required in production "
            "(empty or placeholder values are rejected at startup); in "
            "development an ephemeral per-process secret is generated when empty."
        ),
    )
    jwt_ttl_seconds: int = Field(
        default=3600,
        gt=0,
        description=(
            "Lifetime of one admin login JWT. ``POST /auth/refresh`` re-mints "
            "the token, so this bounds a single token, not the session."
        ),
    )
    session_ttl_seconds: int = Field(
        default=43200,
        gt=0,
        description=(
            "Absolute cap on a web session: refresh re-mints the login JWT only "
            "while ``now - auth_time`` stays inside this window, so a leaked "
            "token cannot be kept alive indefinitely. Must be >= "
            "``jwt_ttl_seconds``."
        ),
    )
    failed_login_lockout_threshold: int = Field(
        default=5,
        description=("Consecutive failed logins after which an admin account is locked."),
    )
    failed_login_lockout_seconds: int = Field(
        default=900,
        description=(
            "How long a locked admin account stays locked before logins are accepted again."
        ),
    )

    @model_validator(mode="after")
    def _require_or_generate_secret_in_dev(self) -> AdminAuthConfig:
        self.jwt_secret = _require_or_generate_secret(
            self.jwt_secret, field_path="admin.auth.jwt_secret"
        )
        return self

    @model_validator(mode="after")
    def _session_window_covers_jwt_ttl(self) -> AdminAuthConfig:
        # A session window shorter than one JWT would make every refresh fail
        # while the first token is still valid — always a misconfiguration.
        if self.session_ttl_seconds < self.jwt_ttl_seconds:
            raise ValueError("admin.auth.session_ttl_seconds must be >= admin.auth.jwt_ttl_seconds")
        return self


class AdminInviteConfig(BaseModel):
    """Admin invite token settings."""

    ttl_days: int = 7
    pepper: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def _require_or_generate_secret_in_dev(self) -> AdminInviteConfig:
        self.pepper = _require_or_generate_secret(self.pepper, field_path="admin.invite.pepper")
        return self


class AdminConfig(BaseModel):
    """Admin surface configuration."""

    auth: AdminAuthConfig = Field(default_factory=AdminAuthConfig)
    invite: AdminInviteConfig = Field(default_factory=AdminInviteConfig)


class SigningKeyConfig(BaseModel):
    """A single ES256 signing key for ID token issuance."""

    kid: str
    private_key_pem: SecretStr

    @field_validator("kid")
    @classmethod
    def _validate_kid(cls, v: str) -> str:
        if not _KEY_ID_RE.match(v):
            raise ValueError("kid must match [a-zA-Z0-9_-]+")
        return v


class IdpConfig(BaseModel):
    """External OIDC identity provider configuration."""

    enabled: bool = Field(
        default=False,
        description=(
            "Enable login via an external OIDC identity provider. When false no "
            "IdP adapter is built and the IdP login path is absent."
        ),
    )
    provider: str = Field(
        default="oidc",
        description=(
            "Adapter selector: ``google`` supplies Google's well-known endpoints "
            "and ``hd`` claim handling; any other value uses the generic "
            "standards-compliant OIDC adapter."
        ),
    )
    issuer: str = Field(
        default="",
        description=(
            "OIDC issuer base URL. Default authorization/token/userinfo "
            "endpoints are derived from it when the explicit ``*_endpoint`` "
            "keys are unset."
        ),
    )
    client_id: str = Field(
        default="",
        description="OAuth client ID registered with the identity provider.",
    )
    client_secret: SecretStr = Field(
        default=SecretStr(""),
        description="OAuth client secret, sent on the authorization-code exchange.",
    )
    scopes: list[str] = Field(
        default_factory=lambda: ["openid", "email", "profile"],
        description="OAuth scopes requested on the IdP authorization redirect.",
    )
    authorization_endpoint: str | None = Field(
        default=None,
        description=(
            "Explicit IdP authorization endpoint URL; overrides the "
            "issuer-derived or provider well-known default."
        ),
    )
    exchange_endpoint: str | None = Field(
        default=None,
        description=(
            "Explicit IdP token (code-exchange) endpoint URL; overrides the "
            "issuer-derived or provider well-known default."
        ),
    )
    userinfo_endpoint: str | None = Field(
        default=None,
        description=(
            "Explicit IdP userinfo endpoint URL; overrides the issuer-derived "
            "or provider well-known default."
        ),
    )
    hosted_domain: str | None = Field(
        default=None,
        description=(
            "Google ``hd`` (hosted-domain) restriction. When set, only accounts "
            "whose userinfo carries a matching ``hd`` claim should be admitted. "
            "OSS surfaces the claim (see IdpClaims.hosted_domain); enforcement "
            "is left to the deployment's admission policy."
        ),
    )


class PlatformClientConfig(BaseModel):
    """A static first-party OAuth client (e.g. the operator SPA).

    Platform clients authenticate via PKCE only — no client secret. They are
    defined in config (not the oauth_clients DB table) because they are
    deployment-time constants, not admin-managed dynamic registrations.
    """

    client_id: str
    redirect_uris: list[str] = Field(min_length=1)

    @field_validator("redirect_uris", mode="after")
    @classmethod
    def _validate_redirect_uris(cls, uris: list[str]) -> list[str]:
        for uri in uris:
            parsed = urlparse(uri)
            if not parsed.scheme or not parsed.netloc:
                msg = f"invalid platform redirect_uri (must be an absolute URL): {uri}"
                raise ValueError(msg)
            if parsed.scheme not in ("https", "http"):
                msg = f"platform redirect_uri must use https or http: {uri}"
                raise ValueError(msg)
            if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
                msg = f"http redirect_uri only allowed for localhost: {uri}"
                raise ValueError(msg)
        return uris


_SPA_CLIENT_ID = "jentic-one-spa"
_SPA_CALLBACK_PATH = "/app/auth/callback"

_LOCAL_DEV_KEY_FINGERPRINT = "d35355bfb727b96b885e0ff817efd947bc5d8a88f169cabfa764932b57b3f3db"
_LOCAL_DEV_KEY_KID = "local-dev-key"


class OAuthRateLimitConfig(BaseModel):
    """Pre-auth rate limit tunables for OAuth endpoints."""

    authorize_rpm: int = Field(
        default=30,
        description=(
            "Sustained requests/minute allowed per ``client_id``+IP on the "
            "unauthenticated ``/authorize`` endpoints."
        ),
    )
    authorize_burst: int = Field(
        default=30,
        description="Burst allowance on top of ``authorize_rpm``.",
    )
    exchange_rpm: int = Field(
        default=60,
        description=(
            "Sustained requests/minute allowed per ``client_id``+IP on the "
            "token endpoint (also reused per-IP for token revocation)."
        ),
    )
    exchange_burst: int = Field(
        default=60,
        description="Burst allowance on top of ``exchange_rpm``.",
    )
    registration_rpm: int = Field(
        default=10,
        description=(
            "Sustained requests/minute allowed per IP on anonymous dynamic "
            "client registration (``POST /oauth-clients``)."
        ),
    )
    registration_burst: int = Field(
        default=5,
        description="Burst allowance on top of ``registration_rpm``.",
    )
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description=(
            "Socket IPs of reverse proxies whose ``X-Forwarded-For`` header is "
            "honored when deriving the per-client rate-limit identity. Empty "
            "(default) means the socket address is used as-is — behind a "
            "reverse proxy every request then carries the proxy's IP and all "
            "clients share one bucket, so list the proxy IPs when deploying "
            "behind one."
        ),
    )


class AuthConfig(BaseModel):
    """Platform-actors OAuth surface configuration."""

    # Expected shape: scheme://host[:port] with NO path. Unvalidated — a
    # path-bearing value (e.g. https://host/jentic) silently breaks the fixed
    # RFC 8414/9728 well-known routes: path-insertion metadata URLs derived
    # from a path-bearing issuer are not served (root and /mcp docs alike).
    canonical_base_url: str = ""
    access_ttl_seconds: int = 3600
    refresh_ttl_seconds: int = 604800
    rat_ttl_seconds: int = 900
    # TTL for the agent-ownership claim token minted at /register (see
    # auth/core/claim.py). Only meaningful when a claim-token minter is installed;
    # OSS default mints no token so this is inert.
    claim_ttl_seconds: int = 900
    assertion_max_ttl_seconds: int = 300
    auth_code_ttl_seconds: int = 300
    id_signing: list[SigningKeyConfig] = Field(default_factory=list)
    idp: IdpConfig = Field(default_factory=IdpConfig)
    platform_clients: list[PlatformClientConfig] = Field(default_factory=list)
    oauth_rate_limit: OAuthRateLimitConfig = Field(default_factory=OAuthRateLimitConfig)

    def model_post_init(self, __context: object) -> None:
        """Ensure the SPA platform client is always registered.

        If no platform_clients entry exists for the operator SPA and a
        canonical_base_url is configured, synthesise one so existing
        deployments continue to work without a config change on upgrade.
        """
        if os.environ.get("JENTIC_ENV", "development") == "production":
            from jentic_one.shared.crypto.signing import signing_key_spki_fingerprint

            for sk in self.id_signing:
                if sk.kid == _LOCAL_DEV_KEY_KID:
                    raise ConfigError(
                        f"auth.id_signing[kid={sk.kid!r}] uses the local-dev key id "
                        "which must not be used in production"
                    )
                fp = signing_key_spki_fingerprint(sk.private_key_pem.get_secret_value())
                if fp == _LOCAL_DEV_KEY_FINGERPRINT:
                    raise ConfigError(
                        f"auth.id_signing[kid={sk.kid!r}] contains the local-dev "
                        "signing key material which must not be used in production"
                    )
        spa_present = any(pc.client_id == _SPA_CLIENT_ID for pc in self.platform_clients)
        if not spa_present and self.canonical_base_url:
            base = self.canonical_base_url.rstrip("/")
            self.platform_clients.append(
                PlatformClientConfig(
                    client_id=_SPA_CLIENT_ID,
                    redirect_uris=[f"{base}{_SPA_CALLBACK_PATH}"],
                )
            )


_KEY_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class EncryptionKey(BaseModel):
    """A single named encryption key."""

    id: str
    material: SecretStr

    @field_validator("id")
    @classmethod
    def _validate_id_format(cls, v: str) -> str:
        if not _KEY_ID_RE.match(v):
            raise ValueError("key id must match [a-zA-Z0-9_-]+")
        return v


class EncryptionConfig(BaseModel):
    """Envelope-encryption keyset configuration."""

    active_id: str = "v1"
    entries: list[EncryptionKey] = Field(default_factory=list)


class DirectOAuth2ProviderConfig(BaseModel):
    """Configuration for a direct OAuth2 provider (full settings land in M5)."""

    kind: Literal["direct_oauth2"] = "direct_oauth2"
    redirect_uri: str
    default_scopes: list[str] = Field(default_factory=list)
    expiry_skew_seconds: int = 60
    # Extra query params appended to every authorize URL.
    #
    # Defaults force the consent screen and ask Google to issue a refresh
    # token: without `prompt=consent` an IdP that already has user consent
    # silently redirects back with a code (so re-connect looks like a
    # no-op), and without `access_type=offline` Google will not issue a
    # `refresh_token` after the first ever consent — leaving long-lived
    # access broken.
    #
    # `prompt=consent` is OIDC-standard (RFC 8252 / OIDC Core 3.1.2.1) and
    # recognised by Google, Microsoft, Okta, Auth0, GitHub, etc. Providers
    # that don't understand a param generally ignore it.
    # `access_type=offline` is Google-specific; other IdPs ignore it. To
    # request a refresh token from Microsoft / Okta / Auth0, add the
    # `offline_access` scope to `default_scopes` instead.
    #
    # NOTE: Keys here can override the OAuth2-spec params built by the provider
    # (`response_type`, `client_id`, `redirect_uri`, `state`, `scope`). The
    # connect flow will break if you do this.
    # Kept open as an escape hatch for unusual IdPs.
    authorize_extra_params: dict[str, str] = Field(
        default_factory=lambda: {"prompt": "consent", "access_type": "offline"}
    )


class PipedreamProviderConfig(BaseModel):
    """Configuration for a Pipedream-hosted OAuth provider (full settings land in M6)."""

    kind: Literal["pipedream"] = "pipedream"
    project_id: str
    environment: Literal["production", "development"] = "production"
    client_id: str
    client_secret: SecretStr
    connect_base_url: str = "https://api.pipedream.com/v1"
    expiry_skew_seconds: int = 60


ProviderConfig = Annotated[
    DirectOAuth2ProviderConfig | PipedreamProviderConfig,
    Field(discriminator="kind"),
]


class ConnectConfig(BaseModel):
    """Configuration for the OAuth connect flow."""

    state_secret: SecretStr = SecretStr("")
    state_ttl_seconds: int = 600

    @model_validator(mode="after")
    def _require_or_generate_secret_in_dev(self) -> ConnectConfig:
        self.state_secret = _require_or_generate_secret(
            self.state_secret, field_path="credentials.connect.state_secret"
        )
        return self


class CredentialsConfig(BaseModel):
    """Credentials subsystem configuration."""

    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    connect: ConnectConfig = Field(default_factory=ConnectConfig)


class AccessRequestsConfig(BaseModel):
    """Access requests subsystem configuration."""

    ttl_days: int = 7
    canonical_base_url: str = ""


class ControlSurfaceConfig(BaseModel):
    """Control surface configuration."""

    access_requests: AccessRequestsConfig = Field(default_factory=AccessRequestsConfig)


class UpstreamClientConfig(BaseModel):
    """Bounds for the single shared outbound ``httpx.AsyncClient``.

    The timeouts are httpx semantics: ``read_timeout_s`` is the *between-bytes*
    gap timeout (per read), **not** a whole-stream cap — a trickle that keeps
    sending under the limit can hold a pool slot open. The overall transfer
    deadline is owned by the response-streaming guard, not here.
    """

    connect_timeout_s: float = 5.0
    read_timeout_s: float = 30.0
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
    ``503``) is retried only for idempotent methods or when an
    ``Idempotency-Key`` is present — a blind ``POST`` retry is a double-spend.
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


def _csv_to_list(value: Any) -> list[str]:
    """Coerce a comma-separated string into a list; pass through lists unchanged."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise TypeError(f"expected list or comma-separated string, got {type(value).__name__}")


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
            '``["10.50.0.0/16"]``). The cloud-metadata IPs (169.254.169.254 / '
            "fd00:ec2::254) are never exempted, even when a listed range covers "
            "them. Accepts a YAML list or a comma-separated string."
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
        for cidr in value:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid CIDR in allowed_private_subnets: {cidr!r}") from exc
        return value


class BrokerResilienceConfig(BaseModel):
    """Resilience envelope: admission + rate limit / circuit.

    The shared-state ``backend`` selection drives *both* the rate limiter and
    the circuit breaker (memory default; Redis is cluster-wide). Queue
    backpressure / async-credential / retention knobs are future work.
    """

    max_in_flight: int = 200
    shed_retry_after_s: int = 5
    # Overall wall-clock budget (seconds) for one upstream call, enforced by the
    # always-on DeadlineRunner *outside* the circuit breaker (and, once it lands,
    # the retry loop) — distinct from the per-attempt connect/read timeout on the
    # transport client. Exceeding it returns 504 with a `wait` agent directive.
    # 0 disables the budget (unbounded call). Size ABOVE upstream read timeouts so
    # a single healthy slow attempt isn't pre-empted by the envelope deadline.
    request_deadline_s: float = 30.0
    # Fraction of ``max_in_flight`` at/above which ``/ready`` reports unready so
    # the LB drains this instance *before* it hits the hard admission shed wall.
    # Kept < 1.0 for that headroom.
    readiness_saturation_threshold: float = Field(default=0.9, gt=0.0, le=1.0)
    upstream: UpstreamClientConfig = Field(default_factory=UpstreamClientConfig)
    backend: StateBackendConfig = Field(default_factory=StateBackendConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)


class IdempotencyConfig(BaseModel):
    """``Idempotency-Key`` replay store.

    Sync ``FULL``-mode replay over the shared-state ``AtomicStore`` (memory
    default; Redis ⇒ cross-instance). Two TTLs: a *short* ``pending_ttl_s`` claim
    (sized to the request deadline + buffer, so a crash mid-flight frees the key
    in seconds rather than the full replay window) promoted to the *long*
    ``ttl_s`` only once the response is stored. Responses over
    ``max_response_bytes`` are recorded without their body (replay still prevents
    a duplicate side-effect; only byte-for-byte body replay is dropped).

    Compliance modes (``metadata_only`` / kill-switch), ``require_for_mutations``,
    async same-``job_id`` replay, and at-rest encryption are future work.
    """

    enabled: bool = True
    # 24h replay window (spec default); tune DOWN for high-volume Redis footprint.
    ttl_s: float = 86_400.0
    # Claim TTL — size to the request deadline (+buffer); poison-pill guard.
    pending_ttl_s: float = 35.0
    # Bodies larger than this are NOT stored for byte-for-byte replay.
    max_response_bytes: int = 256 * 1024


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
    jwks_url: str
    algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])

    @field_validator("jwks_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        # A plaintext JWKS endpoint is MITM-able into trusting attacker keys.
        if not value.startswith("https://"):
            raise ValueError(f"jwks_url must be https://: {value!r}")
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
    # Number of failed executions for one actor+toolkit+operation within the
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

    upstream_timeout_s: float = 30.0
    resolve_cache_ttl_seconds: float = 3.0
    # Short TTL (seconds) for the per-instance toolkit-derivation cache.
    # Wraps the cross-DB `derive_toolkits` lookup so the per-request Admin+Control
    # double hit is served from cache for header-less requests. Agent/credential
    # bindings change infrequently, so a short TTL bounds revocation staleness
    # (the cache is per instance, so a grant/revoke is consistent cluster-wide
    # only after the TTL lapses on each node) while removing the hot-path lookup.
    # Authorization correctness never depends on the cache — it is a latency
    # optimization over the authoritative DB lookup. 0 disables it.
    toolkit_cache_ttl_s: float = 3.0
    # Short TTL (seconds) for the per-instance permission-rule cache.
    # Caches the ordered toolkit_permission_rules per toolkit_id. Same staleness
    # trade-off as toolkit_cache_ttl_s — a rule change propagates after the TTL.
    rule_cache_ttl_s: float = 3.0
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
    # Public base URL of the account-linking/provisioning UI. When set, a 424
    # (credential not provisioned) carries a `prompt_human` directive with a
    # `provisioning_url` the agent can relay to the user. None keeps the
    # directive but omits the URL. The URL is non-secret (where to *go* to
    # provision, never the credential itself).
    account_linking_base_url: str | None = None
    resilience: BrokerResilienceConfig = Field(default_factory=BrokerResilienceConfig)
    idempotency: IdempotencyConfig = Field(default_factory=IdempotencyConfig)
    egress: EgressConfig = Field(default_factory=EgressConfig)


class SearchConfig(BaseModel):
    """Search configuration.

    The built-in mode is "lexical" (BM25 on SQLite, native full-text on
    PostgreSQL). ``search_mode`` is validated against the registered
    SearchStrategy set at resolve time (``resolve_strategy``), so an unknown mode
    fails loudly with the available modes for the active dialect rather than at
    config load. Additional modes (e.g. "semantic", "vector") can be registered
    via ``register_strategy`` without editing this schema.
    """

    # Gate ingest-time construction of the lexical search_text projection.
    enabled: bool = True
    # Toggle query-time search independently of ingest-time indexing.
    search_enabled: bool = True
    # Search mode name; resolved against the SearchStrategy registry per dialect.
    # "lexical" is the built-in mode.
    search_mode: str = "lexical"


class IngestConfig(BaseModel):
    """Spec ingestion settings (fetch limits, timeouts, egress policy)."""

    # Public-API specs in the catalog (Stripe, AWS, Azure, …) routinely exceed a
    # few MB; a 5 MiB cap made large specs both un-previewable and un-importable.
    # 25 MiB comfortably covers the largest specs in jentic-public-apis while
    # still bounding memory per fetch.
    max_spec_bytes: int = 25 * 1024 * 1024
    fetch_timeout_s: float = 30.0
    max_redirects: int = 5
    # SSRF/egress policy for fetching URL sources. Strict by default (private
    # ranges blocked); opt in to internal targets for in-cluster/corporate
    # imports — mirrors broker.egress. See EgressConfig.
    egress: EgressConfig = Field(default_factory=EgressConfig)


class CatalogConfig(BaseModel):
    """Public API catalog settings (manifest source + staleness)."""

    manifest_url: str = (
        "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi/apis.json"
    )
    # Lazy refresh-on-read: a manifest older than this is refreshed on the next
    # list()/get(). Zero disables auto-refresh (manual :refresh only).
    manifest_max_age_seconds: int = 86400
    # Update-notify (Flow 3): the standalone ``CatalogUpdateScanner`` (started in
    # app_factory when both the registry + admin DBs are present) runs one sweep per
    # this interval, conditionally re-fetching the spec URLs of upstream-tracked APIs
    # (If-None-Match) and emitting a ``catalog.update_available`` event when the
    # upstream spec changed. The manual ``POST /catalog:refresh`` also triggers a
    # sweep. A given API is re-probed at most once per this interval (a persistent
    # per-API gate, so scanner + manual refresh don't double-probe). Zero disables the
    # sweep + scanner entirely (kill switch for air-gapped installs — no event spam,
    # no egress). Standalone-registry deployments (no admin DB) get no scanner.
    update_check_interval_seconds: int = 86400
    # Aggregate guardrails for one update-notify sweep. The sweep is offloaded off
    # the triggering read (fire-and-forget), but it still probes N registered specs
    # over the network, so bound the batch: stop after this many wall-clock seconds
    # and run at most this many probes concurrently. Concurrency is kept below the
    # registry DB pool so the sweep never starves live request traffic.
    update_sweep_deadline_seconds: int = 300
    update_sweep_max_concurrency: int = 4
    # Full-jitter fraction added to the per-cycle sweep interval, to de-phase the
    # scanner across replicas (thundering-herd mitigation). Each time a sweep runs,
    # the next one is due after ``interval * (1 + uniform(0, jitter_ratio))``, so N
    # replicas that start in lock-step drift apart instead of all re-probing the
    # upstream at the same interval boundary. Bounded (default 15%, capped at 100%)
    # so the cadence stays ~daily; 0 disables jitter (deterministic, e.g. for tests).
    update_sweep_jitter_ratio: float = Field(default=0.15, ge=0.0, le=1.0)


class McpOAuthConfig(BaseModel):
    """Interactive-OAuth settings for the MCP surface.

    Minimal seam: the full ``server.mcp`` sub-config (``server.mcp.enabled``
    etc.) extends this model in place — fields here must keep their names and
    defaults.
    """

    enabled: bool = False
    """Master switch for the interactive-OAuth surface. Off (the default) the
    anonymous DCR front door ``POST /oauth-clients`` returns a plain 404,
    indistinguishable from not-shipped."""
    auto_approve_clients: bool = False
    """D9 (amended): auto-approve DCR registrations (``approval_status='approved'``
    + ``active=true`` at registration). Default **false** everywhere —
    approval-first posture: new registrations land ``pending`` + inactive in
    the admin queue until an operator approves them. Setting this ``true`` is
    an explicit opt-in for deployments that accept self-registered clients
    without review (e.g. self-hosted single-user installs)."""
    registration_gc_days: int = 90
    """Long-TTL GC policy for never-consented DCR rows: rows are
    GC-eligible only after this many days, and rows with any grant history are
    never GC'd. Policy knob only — no sweep runs yet."""


class McpConfig(BaseModel):
    """``server.mcp`` sub-config."""

    enabled: bool = False
    """Master switch for the daemon-native Streamable HTTP ``/mcp`` endpoint.
    Off (the default) the mounted app answers the framework's
    plain route-not-found 404 — unless ``oauth.enabled`` keeps the
    discovery-challenge behaviour alive (401 + ``WWW-Authenticate``) so OAuth
    clients can still walk the RFC 9728 chain ahead of the endpoint being
    turned on. Flipping this on is an explicit operator choice."""
    broker_url: str = "http://127.0.0.1:8100"
    """Broker base URL for the MCP ``execute`` tools' server-side
    control-plane→broker proxy hop (the broker stays MCP-free,
    so the mounted app forwards execute calls to the broker exactly like the
    CLI does). The default matches the local install topology (plain-HTTP
    loopback broker on 8100); compose/split deployments set the broker
    service's URL. Plaintext ``http://`` is refused for non-loopback hosts —
    the caller's bearer rides this hop (SEC-1 posture)."""
    oauth: McpOAuthConfig = Field(default_factory=McpOAuthConfig)


class ServerConfig(BaseModel):
    """HTTP server settings."""

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    backend: Literal["local", "remote"] = "local"
    """Self-declared backend locality surfaced by ``GET /instance``: ``local`` for
    a self-hosted install on the operator's own machine/network, ``remote`` for a
    hosted install run elsewhere (e.g. Jentic Cloud). A hint for clients to tell
    which backend they reached — not an authorization signal. Defaults to
    ``local``; the hosted platform sets ``remote`` in its own config."""
    mcp: McpConfig = Field(default_factory=McpConfig)


class TelemetryConfig(BaseModel):
    """Anonymous product-telemetry settings (issue #446).

    Defaults to **OFF**: an instance whose config omits this block (non-onboarded
    or hand-rolled) sends nothing. The onboarding CLI writes ``enabled``
    explicitly (a yes-default ``[Y]/n`` prompt) so the on-by-default UX lives in
    the prompt, not the code default. ``instance_id`` seeds the durable admin-DB
    identity row on first startup for opted-in instances. ``host_os`` is the
    operator's OS family, stamped by the CLI at install time so a Docker-run
    instance reports the host's OS rather than the container's; sent once per
    boot, on the ``instance_booted`` event.
    """

    enabled: bool = False
    instance_id: str | None = None
    host_os: str | None = None
    """Host OS family stamped at install time by the onboarding CLI (from Go's
    ``runtime.GOOS``): the recommended install runs the app in Docker, where
    runtime detection would always report the container's Linux instead of the
    operator's machine. ``None`` (hand-rolled config) falls back to runtime
    detection. Consumed once per boot, on the ``instance_booted`` telemetry
    event; values outside the closed ``HostOs`` enum degrade to ``other``."""
    endpoint: str = "https://api.jentic.com/api/v1"
    """Ingest endpoint for telemetry events. Not intended for operator override —
    this is a hardcoded Jentic service URL. Exposed in config only for internal
    testing and development environments."""
    flush_interval_s: float = 30.0
    max_batch: int = 100
    queue_max: int = 10_000
    request_timeout_s: float = 5.0

    @field_validator("instance_id")
    @classmethod
    def _instance_id_fits_column(cls, value: str | None) -> str | None:
        """Reject an operator-set id wider than the ``instance_id`` DB column (64).

        Without this, an over-long id would only fail deep in ``resolve_instance_id``
        at insert time, get swallowed by the best-effort startup guard, and silently
        disable telemetry with just a warning. Validating here surfaces the
        misconfiguration loudly at config load instead.
        """
        if value is not None and len(value) > 64:
            raise ValueError(
                f"telemetry.instance_id must be at most 64 characters, got {len(value)}"
            )
        return value


class ReleaseCheckConfig(BaseModel):
    """ "Update available" check for the running jentic-one build itself.

    Powers ``GET /system/version``: the backend asks GitHub for the newest
    published release of ``repo`` and compares it against the running build so the
    web console can surface an "update available" banner (and the user menu can
    always show the current version). This covers *jentic-one's own* release;
    ``CatalogConfig`` tracks the public *API catalog* instead.

    Runs only on a ``local`` backend (a self-hosted install the operator can
    actually update); the hosted platform (``server.backend == "remote"``) skips
    it. The result is cached in-process for ``cache_ttl_seconds`` (fetch-on-read,
    no background job), so at most one GitHub request happens per TTL regardless
    of how many clients poll. Every failure degrades to "latest unknown" (no
    banner) rather than erroring — the version probe must never break the app.
    """

    enabled: bool = True
    # ``owner/name`` slug of the GitHub repo whose releases represent this build.
    # The check hits ``https://api.github.com/repos/{repo}/releases/latest``.
    repo: str = "jentic/jentic-one"
    # In-process cache lifetime for the resolved latest release. Zero is a kill
    # switch (disables the check outright — air-gapped installs, no egress),
    # mirroring the catalog scanner's ``interval <= 0`` convention.
    cache_ttl_seconds: int = 21600  # 6h


class EntitlementConfig(BaseModel):
    """AWS Marketplace license gate for the Marketplace-listed deployment.

    Powers the entitlement checker (``integrations/aws_marketplace``): on
    startup, and every ``refresh_interval_seconds`` after, the process asks
    AWS whether this deployment's Marketplace subscription is still active, and
    locks the HTTP surface (503, health excepted) when it definitively is not.
    Defaults to **OFF**: a non-Marketplace install that omits this block wires
    nothing and never makes an AWS call.

    Failure posture: an *unreachable* or *erroring* AWS API is never grounds
    for lockout by itself — the last definitive verdict holds for
    ``grace_period_seconds`` before the gate fails closed. Only an explicit
    "not entitled" answer from AWS locks out immediately.
    """

    enabled: bool = False
    # The Marketplace product code, issued by the AWS Marketplace portal when
    # the container product is created. Required whenever ``enabled``.
    product_code: str | None = None
    region: str = "us-east-1"
    # Which paid listing model the check calls: ``contract`` → License Manager
    # ``CheckoutLicense`` (needs ``license_sku``); ``usage`` → Metering Service
    # ``RegisterUsage`` (hourly/usage pricing). The live listing is contract
    # priced (decided 2026-08-20), hence the default; the usage variant is kept
    # until the listing is public in case the model changes during review.
    pricing_model: Literal["usage", "contract"] = "contract"
    refresh_interval_seconds: int = 3600
    grace_period_seconds: int = 86400
    # Contract pricing only (License Manager); unused for usage pricing.
    # This is the Marketplace **product ID** from the portal (CheckoutLicense
    # ``ProductSKU``) — NOT the product code above; the portal issues both.
    license_sku: str | None = None
    # Contract pricing only: the listing's entitlement dimension keys the gate
    # checks out (all must be granted by the buyer's license). The live listing
    # defines ``users`` and ``executions``. Accepts a YAML list or a
    # comma-separated string (env: JENTIC__ENTITLEMENT__LICENSE_DIMENSIONS).
    license_dimensions: Annotated[list[str], BeforeValidator(_csv_to_list)] = Field(
        default_factory=list
    )
    # Test-only endpoint override (same posture as ``TelemetryConfig.endpoint``):
    # points the client at a stub server for deployed-gate rehearsal —
    # moto/LocalStack do not implement these AWS APIs. Not for operators.
    endpoint: str | None = None

    @model_validator(mode="after")
    def _require_gate_inputs(self) -> EntitlementConfig:
        if not self.enabled:
            return self
        if not self.product_code:
            raise ValueError("entitlement.enabled requires entitlement.product_code")
        if self.pricing_model == "contract" and not self.license_sku:
            raise ValueError(
                "entitlement.pricing_model 'contract' requires entitlement.license_sku"
            )
        return self


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
    search: SearchConfig = Field(default_factory=SearchConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    release_check: ReleaseCheckConfig = Field(default_factory=ReleaseCheckConfig)
    entitlement: EntitlementConfig = Field(default_factory=EntitlementConfig)
    apps: list[str] = Field(default_factory=lambda: ["registry", "admin", "control", "auth"])

    # Validated extension sub-configs, keyed by their registered section name.
    # Populated by load_config() from top-level keys matching the registry
    # (see register_config). Empty unless a section has been registered.
    extensions: dict[str, BaseModel] = Field(default_factory=dict)

    def extension(self, name: str) -> BaseModel | None:
        """Return a registered extension config by section name (None if absent)."""
        return self.extensions.get(name)


# --- Extension config registry -----------------------------------------------
# A downstream package registers extra sub-config models at import time; by
# default the registry is empty. Keyed by the top-level YAML/env section name.
# load_config() pulls any matching top-level key out of the merged config and
# validates it with the registered model, storing the result in
# AppConfig.extensions[name].
_CONFIG_EXTENSIONS: dict[str, type[BaseModel]] = {}


def register_config(name: str, model: type[BaseModel]) -> None:
    """Register an extension sub-config model under a top-level config key.

    Idempotent for the same (name, model); raises on a conflicting re-register so
    two extensions can't fight over one key. Call at import time (e.g. in a
    registering package's __init__) before load_config() runs.
    """
    # Collision guard: an extension key must not shadow a core AppConfig field
    # (e.g. "broker", "search") nor the reserved "extensions" container itself —
    # either would break the parser or silently override core config.
    if name in AppConfig.model_fields or name == "extensions":
        raise ConfigError(
            f"Config extension name {name!r} collides with a core AppConfig field "
            "or the reserved 'extensions' key"
        )
    existing = _CONFIG_EXTENSIONS.get(name)
    if existing is not None and existing is not model:
        raise ConfigError(f"Config extension {name!r} already registered to {existing!r}")
    _CONFIG_EXTENSIONS[name] = model


def registered_config_models() -> dict[str, type[BaseModel]]:
    """Snapshot of the extension registry (for tests/introspection)."""
    return dict(_CONFIG_EXTENSIONS)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, preferring override values."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce_indexed_dicts_to_lists(value: Any) -> Any:
    """Recursively turn digit-keyed dicts into lists.

    The env convention (``JENTIC__SECTION__KEY``) can only ever build nested
    dicts, so a *list*-valued field addressed by index —
    ``JENTIC__AUTH__ID_SIGNING__0__KID`` — arrives as ``{"0": {"kid": ...}}``
    rather than ``[{"kid": ...}]`` and fails validation with ``list_type``.

    A dict is treated as a list when its keys are exactly the contiguous integer
    sequence ``0..n-1`` (as strings); it's then rebuilt in index order. Any other
    dict (real string keys, or a sparse/1-based set) is left untouched and
    recursed into, so ordinary config is unaffected.
    """
    if isinstance(value, dict):
        coerced = {k: _coerce_indexed_dicts_to_lists(v) for k, v in value.items()}
        keys = list(coerced.keys())
        if keys and all(k.isdigit() for k in keys):
            ordered = sorted(keys, key=int)
            if [int(k) for k in ordered] == list(range(len(ordered))):
                return [coerced[k] for k in ordered]
        return coerced
    if isinstance(value, list):
        return [_coerce_indexed_dicts_to_lists(item) for item in value]
    return value


def _env_overrides() -> dict[str, Any]:
    """Build a nested dict from JENTIC__* environment variables.

    Convention: JENTIC__SECTION__KEY=value → {"section": {"key": "value"}}

    A numeric path segment addresses a list index, so
    ``JENTIC__AUTH__ID_SIGNING__0__KID`` builds ``{"0": {...}}`` here and is
    coerced to a one-element list by :func:`_coerce_indexed_dicts_to_lists`.
    """
    prefix = "JENTIC__"
    result: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = [p.lower() for p in key[len(prefix) :].split("__")]
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value
    # Top-level keys are section names (never all-digit), so the result stays a
    # dict; the coercion only reshapes nested indexed segments.
    return cast("dict[str, Any]", _coerce_indexed_dicts_to_lists(result))


def load_config(path: Path | None = None) -> AppConfig:
    """Load and validate application configuration.

    Resolution order:
    1. YAML file (explicit path > JENTIC_CONFIG_FILE env var > ./jentic-one.yaml)
    2. Environment variable overrides (JENTIC__SECTION__KEY)

    Raises ConfigError on validation failure.
    """
    file_data: dict[str, Any] = {}

    config_path = path
    if config_path is None:
        env_path = os.environ.get("JENTIC_CONFIG_FILE")
        if env_path:
            config_path = Path(env_path)
        else:
            default_path = Path("jentic-one.yaml")
            if default_path.exists():
                config_path = default_path

    if config_path is not None:
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
        with open(config_path) as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                file_data = loaded

    env_data = _env_overrides()
    merged = _deep_merge(file_data, env_data)

    if "apps" in merged and isinstance(merged["apps"], str):
        merged["apps"] = [item.strip() for item in merged["apps"].split(",") if item.strip()]

    # Extract registered extension sections before validating the core model, so
    # extra="forbid" accepts them and each is validated by its own model.
    extensions: dict[str, BaseModel] = {}
    for name, model in _CONFIG_EXTENSIONS.items():
        if name in merged:
            raw = merged.pop(name)
            try:
                extensions[name] = model.model_validate(raw)
            except ValidationError as e:
                raise ConfigError(f"Invalid config for extension {name!r}: {e}") from e
    if extensions:
        merged["extensions"] = extensions

    try:
        return AppConfig.model_validate(merged)
    except Exception as e:
        raise ConfigError(f"Configuration validation failed: {e}") from e
