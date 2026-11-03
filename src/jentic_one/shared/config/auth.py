"""Auth surface configuration: signing keys, IdP, platform clients, trusted proxies, and login."""

from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, Field, PrivateAttr, SecretStr, field_validator

from jentic_one.shared.config._common import ConfigError, _csv_to_list, _normalize_optional_base_url
from jentic_one.shared.config.encryption import _KEY_ID_RE

_logger = structlog.get_logger(__name__)


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
            "IdP adapter is built; /authorize stays routed and (unless "
            "auth.local_login.enabled provides the password form) ends in an "
            "OAuth server_error redirect because no sign-in path exists."
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
            "Google ``hd`` (hosted-domain) hint. **Not an access control in "
            "the OSS build**: the claim is surfaced (see "
            "IdpClaims.hosted_domain) but never compared — the default "
            "admission policy admits every brand-new email (with zero "
            "permissions until an operator grants some). Restricting sign-in "
            "to a domain requires configuring it at the IdP or installing a "
            "custom admission policy."
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

TrustedProxyNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_IPV4_MAPPED_NETWORK = ipaddress.IPv6Network("::ffff:0:0/96")

# Address space no arbitrary internet host can hold. A trusted-proxy network
# outside it lets any host in that range pick its own rate-limit identity via
# ``X-Forwarded-For``, so such an entry is warned about at load.
_NON_PUBLIC_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
_NON_PUBLIC_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
)

# Networks already warned about: config is validated more than once per
# process, and one warning per network is enough.
_warned_public_trusted_proxies: set[str] = set()


def parse_trusted_proxy(entry: str) -> TrustedProxyNetwork:
    """Parse one ``trusted_proxies`` entry into the network it trusts.

    A bare address becomes a /32 (IPv4) or /128 (IPv6); host bits in a CIDR
    are ignored (``10.1.2.3/8`` is ``10.0.0.0/8``). An IPv4-mapped IPv6 entry
    is unmapped to IPv4, matching how request addresses are compared. Zone IDs
    are rejected: matching is by address only, so a zone would silently trust
    that range on every interface.
    """
    if "%" in entry:
        raise ValueError(
            f"invalid auth.oauth_rate_limit.trusted_proxies entry {entry!r}: "
            "zone IDs ('%' suffix) are not supported"
        )
    try:
        network = ipaddress.ip_network(entry, strict=False)
    except ValueError as exc:
        raise ValueError(
            f"invalid auth.oauth_rate_limit.trusted_proxies entry {entry!r}: "
            "expected an IP address or CIDR network (e.g. 10.0.0.0/8)"
        ) from exc
    if isinstance(network, ipaddress.IPv6Network) and network.subnet_of(_IPV4_MAPPED_NETWORK):
        mapped = int(network.network_address) & 0xFFFFFFFF
        return ipaddress.IPv4Network((mapped, network.prefixlen - 96))
    return network


def _is_non_public_network(network: TrustedProxyNetwork) -> bool:
    if isinstance(network, ipaddress.IPv4Network):
        return any(network.subnet_of(allowed) for allowed in _NON_PUBLIC_IPV4_NETWORKS)
    return any(network.subnet_of(allowed) for allowed in _NON_PUBLIC_IPV6_NETWORKS)


def _warn_public_trusted_proxy_once(entry: str, network: TrustedProxyNetwork) -> None:
    if str(network) in _warned_public_trusted_proxies:
        return
    _warned_public_trusted_proxies.add(str(network))
    _logger.warning(
        "trusted_proxies_network_includes_public_addresses",
        entry=entry,
        network=str(network),
        detail=(
            "any host inside a trusted proxy network can set X-Forwarded-For "
            "and so choose its own OAuth rate-limit identity; list only the "
            "addresses your reverse proxies hold"
        ),
    )


class OAuthRateLimitConfig(BaseModel):
    """Pre-auth rate limit tunables for OAuth endpoints."""

    authorize_rpm: int = Field(
        default=30,
        description=(
            "Sustained requests/minute allowed per client network (IPv4 "
            "address or IPv6 /64) across the unauthenticated authorization "
            "flow: ``/authorize``, the IdP callback, the consent page, its "
            "submit and inline agent create, and the local-login form share "
            "one bucket, whatever "
            "``client_id`` they name. A sign-in spends two to six requests "
            "(more after a mistyped password), so the defaults admit at "
            "least five back-to-back sign-ins and then at least five a minute "
            "per network; raise both for a large shared NAT or egress IP. "
            "Behind a proxy, set "
            "``auth.oauth_rate_limit.trusted_proxies`` or every client "
            "shares the proxy's bucket."
        ),
    )
    authorize_burst: int = Field(
        default=30,
        description="Burst allowance on top of ``authorize_rpm``.",
    )
    exchange_rpm: int = Field(
        default=60,
        description=(
            "Sustained requests/minute allowed per ``client_id`` + client "
            "network (IPv4 address or IPv6 /64) on the token endpoint (also "
            "reused per client network for token revocation)."
        ),
    )
    exchange_burst: int = Field(
        default=60,
        description="Burst allowance on top of ``exchange_rpm``.",
    )
    registration_rpm: int = Field(
        default=10,
        description=(
            "Sustained requests/minute allowed per client network (IPv4 "
            "address or IPv6 /64) on anonymous dynamic client registration "
            "(``POST /oauth-clients``)."
        ),
    )
    registration_burst: int = Field(
        default=5,
        description="Burst allowance on top of ``registration_rpm``.",
    )
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description=(
            "Reverse proxies whose ``X-Forwarded-For`` header is honored when "
            "deriving the per-client rate-limit identity: IP addresses or CIDR "
            "networks (e.g. ``10.0.0.0/8``, ``fd00::/8``), matched against the "
            "socket address and each forwarded hop by network membership. "
            "Accepts a YAML list or a comma-separated string. Invalid entries "
            "fail config load. Empty (default) means the socket address is used "
            "as-is — behind a reverse proxy every request then carries the "
            "proxy's IP and all clients share one bucket, so list the proxy "
            "IPs or pod CIDR when deploying behind one. Only list ranges your "
            "proxies alone hold: any host inside a trusted network can choose "
            "its own rate-limit identity."
        ),
    )
    approval_status_rpm: int = Field(
        default=120,
        description=(
            "Sustained requests/minute allowed on the approval-pending status "
            "poll (``GET /oauth/approval/status``). Its own namespace, so "
            "polling can never drain the ``/authorize`` or registration "
            "quota: one pending tab polls at 12 rpm, so the default keeps "
            "~10 concurrent pending tabs behind one NAT inside the bucket, "
            "and the page honors ``Retry-After`` with backoff, so saturation "
            "degrades to a slower cadence rather than a thundering retry."
        ),
    )
    approval_status_burst: int = Field(
        default=60,
        description="Burst allowance on top of ``approval_status_rpm``.",
    )

    # (entries, parsed networks): keyed on the entries so an assignment or a
    # ``model_copy(update=...)`` after load can never leave a stale trust set.
    _trusted_networks_cache: tuple[tuple[str, ...], tuple[TrustedProxyNetwork, ...]] = PrivateAttr(
        default=((), ())
    )

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split_trusted_proxies(cls, value: Any) -> Any:
        # The env var arrives as one string; a YAML list passes through.
        return _csv_to_list(value) if isinstance(value, str) else value

    @field_validator("trusted_proxies", mode="after")
    @classmethod
    def _validate_trusted_proxies(cls, value: list[str]) -> list[str]:
        entries = [entry.strip() for entry in value]
        for entry in entries:
            network = parse_trusted_proxy(entry)
            if not _is_non_public_network(network):
                _warn_public_trusted_proxy_once(entry, network)
        return entries

    def model_post_init(self, __context: object) -> None:
        """Parse ``trusted_proxies`` at load so request-time matching never parses it."""
        entries = tuple(self.trusted_proxies)
        self._trusted_networks_cache = (entries, tuple(parse_trusted_proxy(e) for e in entries))

    @property
    def trusted_networks(self) -> tuple[TrustedProxyNetwork, ...]:
        """The parsed ``trusted_proxies`` networks, for ``client_ip`` matching.

        Parsed once per distinct entry list; a request only compares the
        entries against the cache key.
        """
        entries = tuple(self.trusted_proxies)
        cached_entries, networks = self._trusted_networks_cache
        if entries != cached_entries:
            networks = tuple(parse_trusted_proxy(e.strip()) for e in entries)
            self._trusted_networks_cache = (entries, networks)
        return networks


class LocalLoginConfig(BaseModel):
    """Local-account login form on the ``/authorize`` flow (no external IdP).

    Default **off**: ``/authorize`` behaviour is byte-identical (including the
    ``server_error`` redirect when no IdP is configured) and ``GET|POST /login``
    answer the framework's plain 404. Enabling it makes the standards-track
    native-app sign-in flow (RFC 8252: DCR + system browser + loopback redirect
    + PKCE) work against the first-party password account store, without any
    client ever handling a password. An external IdP always wins: when
    ``auth.idp.enabled`` is true the login form is never offered (no mixed
    mode in v1).
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Offer a first-party password login form on the /authorize flow. "
            "Off by default; when auth.idp.enabled is true the external IdP "
            "always wins and the form is never offered."
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
    local_login: LocalLoginConfig = Field(default_factory=LocalLoginConfig)
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

    _normalize_canonical_base_url = field_validator("canonical_base_url")(
        _normalize_optional_base_url
    )
