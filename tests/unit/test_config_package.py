"""The ``jentic_one.shared.config`` package re-exports its submodules' public API."""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path
from types import ModuleType

import pytest

from jentic_one.shared import config as config_package

_SUBMODULES: list[ModuleType] = [
    importlib.import_module(f"{config_package.__name__}.{info.name}")
    for info in pkgutil.iter_modules(config_package.__path__)
]

_EXPECTED_PUBLIC: frozenset[str] = frozenset(
    {
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
        "DEFAULT_LATENCY_BUCKETS_MS",
        "DatabaseConfig",
        "DatabasesConfig",
        "DirectOAuth2ProviderConfig",
        "EgressConfig",
        "EncryptionConfig",
        "EncryptionKey",
        "EntitlementConfig",
        "GUARDED_FIELD_SURFACES",
        "HeldExecutionsConfig",
        "IdempotencyConfig",
        "IdpConfig",
        "IngestConfig",
        "JwtVerificationConfig",
        "LocalLoginConfig",
        "LoggingConfig",
        "MAX_LATENCY_BUCKETS",
        "MEMORY_IDEMPOTENCY_TTL_S",
        "McpConfig",
        "McpOAuthConfig",
        "MetricsConfig",
        "OAuthRateLimitConfig",
        "ObservabilityConfig",
        "POOL_PRE_PING_IDLE_S",
        "POOL_RECYCLE_S",
        "PipedreamProviderConfig",
        "PlatformClientConfig",
        "ProviderConfig",
        "PublicUrlMismatch",
        "RateLimitConfig",
        "ReleaseCheckConfig",
        "RetryConfig",
        "RuntimeConfig",
        "SHARED_IDEMPOTENCY_TTL_S",
        "SearchConfig",
        "SecurityConfig",
        "ServerConfig",
        "ServicesConfig",
        "SigningKeyConfig",
        "TCP_KEEPALIVE_COUNT",
        "TCP_KEEPALIVE_IDLE_S",
        "TCP_KEEPALIVE_INTERVAL_S",
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
    }
)
"""The package's public API, frozen so a name dropped from every submodule fails here."""

_EXPECTED_PRIVATE: frozenset[str] = frozenset(
    {
        "_CONFIG_EXTENSIONS",
        "_ONESHOT_CONFIG_CACHE",
        "_apps_for_secret_guard",
        "_csv_to_list",
        "_deep_merge",
        "_env_overrides",
        "_oneshot_cache_key",
    }
)
"""Private helpers that tests and extensions import from the package itself."""


def _defined_names(module: ModuleType) -> set[str]:
    """Top-level names a submodule defines itself (not ones it imports)."""
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.TypeAlias):
            names.add(node.name.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _owner(name: str) -> ModuleType:
    """The one submodule that defines ``name``."""
    owners = [module for module in _SUBMODULES if name in _defined_names(module)]
    assert len(owners) == 1, f"{name} defined in {[m.__name__ for m in owners]}"
    return owners[0]


def test_submodules_are_discovered() -> None:
    assert len(_SUBMODULES) > 1


@pytest.mark.parametrize("module", _SUBMODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_public_names_are_reexported(module: ModuleType) -> None:
    """Every public name a submodule defines is importable from the package."""
    missing = sorted(
        name
        for name in _defined_names(module)
        if not name.startswith("_")
        and (
            getattr(config_package, name, None) is not getattr(module, name)
            or name not in config_package.__all__
        )
    )
    assert not missing, f"{module.__name__} names not re-exported: {missing}"


def test_all_matches_the_frozen_public_api() -> None:
    """``__all__`` is exactly the frozen public API: nothing lost, nothing private."""
    assert set(config_package.__all__) == _EXPECTED_PUBLIC


@pytest.mark.parametrize("name", sorted(_EXPECTED_PUBLIC | _EXPECTED_PRIVATE))
def test_expected_name_resolves_to_its_owner(name: str) -> None:
    """Each expected name resolves from the package to the object its submodule defines."""
    owner = _owner(name)
    assert getattr(config_package, name) is getattr(owner, name)
