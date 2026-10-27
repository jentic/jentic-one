"""Credential provider, vendor auth, and control surface configuration."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from jentic_one.shared.config._secrets import _require_or_generate_secret
from jentic_one.shared.config.egress import ControlEgressConfig
from jentic_one.shared.config.encryption import EncryptionConfig
from jentic_one.shared.url import validate_redirect_uri


class DirectOAuth2ProviderConfig(BaseModel):
    """Configuration for a direct OAuth2 provider (full settings land in M5)."""

    kind: Literal["direct_oauth2"] = "direct_oauth2"
    # Explicit OAuth callback URL registered with the IdP. When unset (the
    # default), the connect flow derives it per request as
    # ``{server.public_base_url or request origin}/credentials/oauth/callback``,
    # so a deployment on any port works without pinning this. Set it only to
    # override that — e.g. behind a reverse proxy whose public origin the app
    # can't see, and only if it differs from ``server.public_base_url``.
    redirect_uri: str | None = None
    default_scopes: list[str] = Field(default_factory=list)
    # How long before an access token's true expiry the broker refreshes it.
    # Applied once, at comparison time; stored ``expires_at`` is never skewed.
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

    @field_validator("redirect_uri")
    @classmethod
    def _validate_redirect_uri(cls, value: str | None) -> str | None:
        # Kept byte-identical (no trailing-slash stripping): the IdP
        # exact-matches it against the registered redirect URI.
        return validate_redirect_uri(value) if value else value


class PipedreamProviderConfig(BaseModel):
    """Configuration for a Pipedream-hosted OAuth provider (full settings land in M6)."""

    kind: Literal["pipedream"] = "pipedream"
    project_id: str
    environment: Literal["production", "development"] = "production"
    client_id: str
    client_secret: SecretStr
    connect_base_url: str = "https://api.pipedream.com/v1"
    # How long before an access token's true expiry the broker refreshes it.
    # Applied once, at comparison time; stored ``expires_at`` is never skewed.
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


# ---------------------------------------------------------------------------
# Vendor auth registry — verified vendors with known SSO integrations.
#
# This is the config-seeded catalog of vendors that support the agent-driven
# integration flow. Each entry describes the OAuth flow(s) available,
# the OAuth app credentials shipped by the platform, the scope catalog with
# read/write classification, and a generic identity-echo probe.
# ---------------------------------------------------------------------------


class VendorDeviceAuthorizationFlowConfig(BaseModel):
    """RFC 8628 device flow settings for a vendor.

    `client_id` is the platform-shipped OAuth application id (device flow is a
    public-client flow — no secret). Endpoints are the vendor's device
    authorization + token endpoints.
    """

    kind: Literal["device_authorization"] = "device_authorization"
    client_id: str
    authorization_endpoint: str
    token_endpoint: str


class VendorAuthorizationCodeFlowConfig(BaseModel):
    """OAuth 2.0 authorization-code flow settings for a vendor.

    Included so the vendor registry is flow-generic from day 1 even though
    phase 1 only wires up device flow. Requires a client secret (confidential
    client) since the redirect-based flow exchanges the code at the token
    endpoint.
    """

    kind: Literal["authorization_code"] = "authorization_code"
    client_id: str
    client_secret: SecretStr
    authorize_url: str
    token_url: str


VendorFlowConfig = Annotated[
    VendorDeviceAuthorizationFlowConfig | VendorAuthorizationCodeFlowConfig,
    Field(discriminator="kind"),
]


class VendorScopeConfig(BaseModel):
    """A single OAuth scope exposed by the vendor.

    `classification` drives the review-page UX: read scopes are pre-selected by
    default; write/admin scopes get a warning flag. `description` is
    human-facing copy displayed on the review page.
    """

    name: str
    classification: Literal["read", "write", "admin"] = "read"
    default: bool = False
    description: str = ""


class VendorIdentityProbeConfig(BaseModel):
    """Generic identity-echo protocol config for a vendor.

    After the connect flow completes, the platform calls
    `{method} {endpoint}` with the freshly minted access token, extracts
    `identity_field` (dotted JSON path) from the response body, and formats it
    into `display_template` (Python str.format). The result is stored as
    `connected_as` and returned to the caller.
    """

    endpoint: str
    method: Literal["GET", "POST"] = "GET"
    identity_field: str
    display_template: str


class VendorAuthConfig(BaseModel):
    """Config entry for one verified vendor.

    Keyed in `VendorRegistryConfig.entries` by a short slug (e.g. "github").
    """

    # Catalog api_id for this vendor's API (e.g. ``github.com/api.github.com``).
    # Decomposed at credential-create time via ``canonical_credential_scope``
    # exactly like a normal catalog import — same api_vendor / api_name /
    # catalog_api_id fields land on the credential row.
    vendor: str
    display_name: str
    flows: list[VendorFlowConfig]
    scopes: list[VendorScopeConfig] = Field(default_factory=list)
    # Optional: platform-shipped vendors always define one; admin-registered
    # OAuth apps that don't have a matching config entry connect without one
    # (the credential is still valid — the ``connected_as`` UX label is just
    # absent). Callers must guard on ``None``.
    identity_probe: VendorIdentityProbeConfig | None = None

    @field_validator("vendor")
    @classmethod
    def _vendor_is_domain_slash_name(cls, v: str) -> str:
        """Require a ``{domain}/{name}`` shape (e.g. ``github.com/api.github.com``).

        Without this check a bare ``vendor`` string (missing the ``/``) silently
        collapses through ``entry.vendor.split("/", 1)[0]`` and produces a
        credential ``api_vendor`` that mismatches the broker's per-operation
        identity check — the mismatch only surfaces on the first connect and is
        hard to diagnose from the field. Failing loud at config-load is cheaper.
        """
        if "/" not in v or v.startswith("/") or v.endswith("/"):
            raise ValueError(
                f"vendor {v!r} must be of the form '<domain>/<sub>' "
                "(e.g. 'github.com/api.github.com')"
            )
        return v


class VendorRegistryConfig(BaseModel):
    """Top-level vendor auth registry."""

    entries: dict[str, VendorAuthConfig] = Field(default_factory=dict)


class ControlConnectConfig(BaseModel):
    """Agent connect-session settings on the control surface."""

    manual_flows_enabled: bool = Field(
        default=True,
        description=(
            "Allow connect sessions that target a registry API (rather than a "
            "vendor-registry key), where a human enters the credential, or "
            "brings an OAuth app, when approving them in the dashboard. On by "
            "default; while off, `:connect` refuses API targets with "
            "`manual_flows_disabled`."
        ),
    )
    manual_flows_ttl_hours: int = Field(
        default=72,
        ge=1,
        description=(
            "Hours an API-target connect session (a human-entered credential, or "
            "an OAuth API waiting for an app) stays open before it expires. "
            "Vendor OAuth sessions keep their fixed 30-minute lifetime."
        ),
    )
    max_open_sessions_per_agent: int = Field(
        default=10,
        ge=1,
        description=(
            "Most open connect sessions one agent may hold at once while "
            "`manual_flows_enabled` is on; one more `:connect` gets 429 "
            "`too_many_open_sessions`."
        ),
    )
    max_open_sessions_per_owner: int = Field(
        default=50,
        ge=1,
        description=(
            "Most open connect sessions across one user and the agents they own "
            "while `manual_flows_enabled` is on; one more `:connect` gets 429 "
            "`too_many_open_sessions`."
        ),
    )
    rejection_cooldown_hours: int = Field(
        default=24,
        ge=0,
        description=(
            "Hours after a human rejects an agent's connect request during which "
            "the agent's repeat `:connect` for the same target gets 429 "
            "`recently_rejected`. 0 turns the cooldown off."
        ),
    )


class ControlSurfaceConfig(BaseModel):
    """Control surface configuration.

    ``egress`` is the allowlist for the platform's outbound OAuth and credential
    calls. Each ``control.egress`` key left unset takes its value from the
    matching ``broker.egress`` key. The policy is read by whichever process makes
    the call: the control surface for credential and OAuth app writes, the
    connect-session confirm, code exchange, device authorization and the
    identity probe, and the broker surface for the token refresh it runs before
    injecting a credential. With one process per surface, set ``control.egress``
    (and the ``broker.egress`` keys it inherits) identically on both.

    Unknown subkeys of ``control:`` are ignored, so a section written for
    another release keeps validating; ``control.egress`` rejects unknown keys so
    a mistyped allowlist key fails at startup instead of silently allowing less.
    """

    connect: ControlConnectConfig = Field(
        default_factory=ControlConnectConfig,
        description="Agent connect-session settings.",
    )
    egress: ControlEgressConfig = Field(
        default_factory=lambda: ControlEgressConfig(),
        description=(
            "Private-network allowlist for the platform's OAuth and credential "
            "calls: OAuth2 credential and OAuth app registration URL checks, "
            "own-client endpoints confirmed on a connect session, token "
            "exchange and refresh, device authorization, and the vendor "
            "identity probe. Each key left unset takes its value from "
            "`broker.egress`; set a key (e.g. `allowed_private_subnets: []`) to "
            "override it for these calls alone. These calls always pin DNS. "
            "The process making the call reads it, and the broker surface "
            "makes token refresh calls, so with one process per surface set it "
            "identically on the control and broker surfaces."
        ),
    )
