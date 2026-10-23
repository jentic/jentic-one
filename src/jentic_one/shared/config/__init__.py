"""Configuration schema and loader for jentic-one.

The schema is split across the submodules of this package; every name is
re-exported here so callers import from ``jentic_one.shared.config``.

The private helpers that tests and extensions reach for (``_deep_merge``,
``_ONESHOT_CONFIG_CACHE`` and the like) are re-exported with redundant
``as`` aliases, so they resolve here but stay out of ``__all__``.
"""

from __future__ import annotations

from jentic_one.shared.config._common import ConfigError
from jentic_one.shared.config._common import _csv_to_list as _csv_to_list
from jentic_one.shared.config._common import _deep_merge as _deep_merge
from jentic_one.shared.config._secrets import GUARDED_FIELD_SURFACES
from jentic_one.shared.config.admin import (
    AdminAuthConfig,
    AdminConfig,
    AdminEventsConfig,
    AdminInviteConfig,
)
from jentic_one.shared.config.app import (
    AppConfig,
    PublicUrlMismatch,
    bind_origin,
    broker_links_root_on_broker,
    check_public_url_consistency,
    connect_approval_url,
    effective_auth_base_url,
    has_spa_platform_client,
    resolved_auth_base_url,
    resolved_control_egress,
)
from jentic_one.shared.config.auth import (
    AuthConfig,
    IdpConfig,
    LocalLoginConfig,
    OAuthRateLimitConfig,
    PlatformClientConfig,
    SigningKeyConfig,
    TrustedProxyNetwork,
    parse_trusted_proxy,
)
from jentic_one.shared.config.broker import (
    MEMORY_IDEMPOTENCY_TTL_S,
    SHARED_IDEMPOTENCY_TTL_S,
    BrokerConfig,
    BrokerResilienceConfig,
    IdempotencyConfig,
    JwtVerificationConfig,
    SecurityConfig,
    TrustedIssuerConfig,
)
from jentic_one.shared.config.credentials import (
    ConnectConfig,
    ControlConnectConfig,
    ControlSurfaceConfig,
    CredentialsConfig,
    DirectOAuth2ProviderConfig,
    PipedreamProviderConfig,
    ProviderConfig,
    VendorAuthConfig,
    VendorAuthorizationCodeFlowConfig,
    VendorDeviceAuthorizationFlowConfig,
    VendorFlowConfig,
    VendorIdentityProbeConfig,
    VendorRegistryConfig,
    VendorScopeConfig,
)
from jentic_one.shared.config.database import (
    POOL_PRE_PING_IDLE_S,
    POOL_RECYCLE_S,
    TCP_KEEPALIVE_COUNT,
    TCP_KEEPALIVE_IDLE_S,
    TCP_KEEPALIVE_INTERVAL_S,
    DatabaseConfig,
    DatabasesConfig,
)
from jentic_one.shared.config.egress import (
    CircuitBreakerConfig,
    ControlEgressConfig,
    EgressConfig,
    RateLimitConfig,
    RetryConfig,
    UpstreamClientConfig,
)
from jentic_one.shared.config.encryption import (
    EncryptionConfig,
    EncryptionKey,
)
from jentic_one.shared.config.loader import _CONFIG_EXTENSIONS as _CONFIG_EXTENSIONS
from jentic_one.shared.config.loader import _ONESHOT_CONFIG_CACHE as _ONESHOT_CONFIG_CACHE
from jentic_one.shared.config.loader import _apps_for_secret_guard as _apps_for_secret_guard
from jentic_one.shared.config.loader import _env_overrides as _env_overrides
from jentic_one.shared.config.loader import _oneshot_cache_key as _oneshot_cache_key
from jentic_one.shared.config.loader import (
    load_config,
    oneshot_config_source_active,
    register_config,
    registered_config_models,
)
from jentic_one.shared.config.mcp import (
    McpConfig,
    McpOAuthConfig,
)
from jentic_one.shared.config.observability import (
    LoggingConfig,
    MetricsConfig,
    ObservabilityConfig,
    TracingConfig,
)
from jentic_one.shared.config.platform import (
    EntitlementConfig,
    HeldExecutionsConfig,
    ReleaseCheckConfig,
    ServerConfig,
    TelemetryConfig,
)
from jentic_one.shared.config.registry import (
    CatalogConfig,
    IngestConfig,
    SearchConfig,
)
from jentic_one.shared.config.runtime import (
    RuntimeConfig,
    ServicesConfig,
    WorkerConfig,
)

__all__ = [
    "GUARDED_FIELD_SURFACES",
    "MEMORY_IDEMPOTENCY_TTL_S",
    "POOL_PRE_PING_IDLE_S",
    "POOL_RECYCLE_S",
    "SHARED_IDEMPOTENCY_TTL_S",
    "TCP_KEEPALIVE_COUNT",
    "TCP_KEEPALIVE_IDLE_S",
    "TCP_KEEPALIVE_INTERVAL_S",
    "AdminAuthConfig",
    "AdminConfig",
    "AdminEventsConfig",
    "AdminInviteConfig",
    "AppConfig",
    "AuthConfig",
    "BrokerConfig",
    "BrokerResilienceConfig",
    "CatalogConfig",
    "CircuitBreakerConfig",
    "ConfigError",
    "ConnectConfig",
    "ControlConnectConfig",
    "ControlEgressConfig",
    "ControlSurfaceConfig",
    "CredentialsConfig",
    "DatabaseConfig",
    "DatabasesConfig",
    "DirectOAuth2ProviderConfig",
    "EgressConfig",
    "EncryptionConfig",
    "EncryptionKey",
    "EntitlementConfig",
    "HeldExecutionsConfig",
    "IdempotencyConfig",
    "IdpConfig",
    "IngestConfig",
    "JwtVerificationConfig",
    "LocalLoginConfig",
    "LoggingConfig",
    "McpConfig",
    "McpOAuthConfig",
    "MetricsConfig",
    "OAuthRateLimitConfig",
    "ObservabilityConfig",
    "PipedreamProviderConfig",
    "PlatformClientConfig",
    "ProviderConfig",
    "PublicUrlMismatch",
    "RateLimitConfig",
    "ReleaseCheckConfig",
    "RetryConfig",
    "RuntimeConfig",
    "SearchConfig",
    "SecurityConfig",
    "ServerConfig",
    "ServicesConfig",
    "SigningKeyConfig",
    "TelemetryConfig",
    "TracingConfig",
    "TrustedIssuerConfig",
    "TrustedProxyNetwork",
    "UpstreamClientConfig",
    "VendorAuthConfig",
    "VendorAuthorizationCodeFlowConfig",
    "VendorDeviceAuthorizationFlowConfig",
    "VendorFlowConfig",
    "VendorIdentityProbeConfig",
    "VendorRegistryConfig",
    "VendorScopeConfig",
    "WorkerConfig",
    "bind_origin",
    "broker_links_root_on_broker",
    "check_public_url_consistency",
    "connect_approval_url",
    "effective_auth_base_url",
    "has_spa_platform_client",
    "load_config",
    "oneshot_config_source_active",
    "parse_trusted_proxy",
    "register_config",
    "registered_config_models",
    "resolved_auth_base_url",
    "resolved_control_egress",
]
