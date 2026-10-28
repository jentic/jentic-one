"""Platform configuration: server, telemetry, release check, entitlement, held executions."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

from jentic_one.shared.config._common import _csv_to_list, _normalize_optional_base_url
from jentic_one.shared.config.mcp import McpConfig


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

    public_base_url: str = ""
    """The single public origin of this deployment (e.g.
    ``https://jentic.example.com``).

    Every absolute URL the app builds for external consumption on the
    control/auth surfaces — the OAuth connect ``redirect_uri``, the OIDC issuer
    / JWT-Bearer audience, the DCR ``registration_client_uri``, the SPA login
    callback, and connect-session approval links — falls back to this when its
    own more specific knob is unset. Explicit per-field values still win
    (needed behind a reverse proxy that fronts multiple surfaces on distinct
    origins). The broker's ``jobs_api_base_url`` is deliberately independent:
    it names another service's origin, not this one. Left unset, request-scoped consumers derive
    from the incoming request's origin and request-less ones from the serving
    bind (``http://{host}:{port}``), so zero-config local dev on any port just
    works — set this only when clients reach the app on an origin it can't
    see (reverse proxy, ingress, port mapping)."""

    _normalize_public_base_url = field_validator("public_base_url")(_normalize_optional_base_url)


class TelemetryConfig(BaseModel):
    """Anonymous product-telemetry settings.

    Defaults to **OFF**: an instance whose config omits this block (non-onboarded
    or hand-rolled) sends nothing. The onboarding CLI writes ``enabled``
    explicitly (a yes-default ``[Y]/n`` prompt), which is where the
    on-by-default install experience comes from. ``instance_id`` seeds the
    durable admin-DB identity row on first startup for opted-in instances.
    ``host_os`` is the operator's OS family, stamped by the CLI at install
    time so a Docker-run instance reports the host's OS rather than the
    container's; sent once per boot, on the ``instance_booted`` event.
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


class HeldExecutionsConfig(BaseModel):
    """Require-approval holds: held execution jobs awaiting a human reviewer.

    An execute call matching a permission rule with ``effect="require-approval"``
    is enqueued as a ``held`` job with a ``pending`` approval; a reviewer (the
    agent's owner or an ``org:admin``) approves or denies it, or it expires.
    """

    ttl_seconds: int = Field(
        default=86_400,
        gt=0,
        description=(
            "Seconds a pending approval lives before the expiry sweep marks it "
            "``expired`` and fails its held job with a permission-denied result. "
            "Defaults to 24 hours."
        ),
    )
    max_pending_per_agent: int = Field(
        default=10,
        ge=1,
        description=(
            "Maximum pending approvals per agent. An execute call that would file "
            "another hold beyond this is denied with the "
            "``approval_pending_limit_reached`` problem type."
        ),
    )
    result_retention_seconds: int = Field(
        default=86_400,
        gt=0,
        description=(
            "Seconds the result of an approved execution stays readable via "
            "``GET /jobs/{id}/result`` before the result sweep removes it. "
            "Defaults to 24 hours."
        ),
    )
