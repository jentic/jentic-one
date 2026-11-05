"""Platform configuration: server, telemetry, release check, entitlement, held executions."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

from jentic_one.shared.config._common import _csv_to_list, _normalize_optional_base_url
from jentic_one.shared.config.mcp import McpConfig


class ServerConfig(BaseModel):
    """HTTP server settings."""

    host: str = Field(
        default="0.0.0.0",
        description=(
            "Interface the HTTP server binds to. ``0.0.0.0`` / ``::`` listen on all "
            "interfaces; a loopback address keeps the app reachable from this "
            "machine only."
        ),
    )
    port: int = Field(default=8000, description="TCP port the HTTP server listens on.")
    reload: bool = Field(
        default=False,
        description=(
            "Run uvicorn with auto-reload on code changes (development only). "
            "Ignored, with a warning, when the admin database is SQLite or the "
            "config comes from a one-shot source such as a pipe."
        ),
    )
    # The hosted platform sets ``remote`` in its own config.
    backend: Literal["local", "remote"] = Field(
        default="local",
        description=(
            "Backend locality reported by ``GET /instance``: ``local`` for a "
            "self-hosted install on the operator's own machine or network, "
            "``remote`` for a hosted install run elsewhere. A hint for clients, not "
            "an authorization signal; ``remote`` also skips the release check."
        ),
    )
    mcp: McpConfig = Field(default_factory=McpConfig)

    # Explicit per-key values still win over this (needed behind a reverse proxy
    # that fronts multiple surfaces on distinct origins). The broker's
    # ``jobs_api_base_url`` is deliberately independent: it names another
    # service's origin, not this one.
    public_base_url: str = Field(
        default="",
        description=(
            "The public origin of this deployment (e.g. "
            "``https://jentic.example.com``). Absolute URLs the app hands to "
            "external clients — the OAuth connect ``redirect_uri``, the OIDC "
            "issuer and JWT-Bearer audience, DCR ``registration_client_uri``, "
            "the SPA login callback, approval links — fall back to it when "
            "their own more specific key is unset. Unset, they derive from the "
            "incoming request's origin, or from the bind "
            "(``http://{host}:{port}``) when there is no request; set it when "
            "clients reach the app through a reverse proxy, ingress or port "
            "mapping."
        ),
    )

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

    enabled: bool = Field(
        default=False,
        description=(
            "Send anonymous product telemetry. Off when the block is omitted; the "
            "onboarding CLI writes it explicitly."
        ),
    )
    instance_id: str | None = Field(
        default=None,
        description=(
            "Opaque instance id reported with telemetry (at most 64 characters). "
            "When set it is authoritative and overwrites the id stored in the "
            "admin database; unset, a random UUID is generated on first startup "
            "and kept."
        ),
    )
    host_os: str | None = Field(
        default=None,
        description=(
            "Host OS family stamped by the onboarding CLI, so an instance running "
            "in Docker reports the operator's OS rather than the container's "
            "Linux. Unset falls back to runtime detection; unrecognised values are "
            "reported as ``other``. Sent once per boot, on the ``instance_booted`` "
            "event."
        ),
    )
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

    enabled: bool = Field(
        default=True,
        description=(
            "Check GitHub for a newer jentic-one release so the web console can "
            'show an "update available" banner. Runs only when ``server.backend`` '
            "is ``local``."
        ),
    )
    # ``owner/name`` slug of the GitHub repo whose releases represent this build.
    # The check hits ``https://api.github.com/repos/{repo}/releases/latest``.
    repo: str = "jentic/jentic-one"
    # Zero as a kill switch mirrors the catalog scanner's ``interval <= 0``
    # convention.
    cache_ttl_seconds: int = Field(
        default=21600,
        description=(
            "Seconds the latest-release lookup is cached in process (default 6 h), "
            "so at most one GitHub request is made per TTL. 0 disables the check "
            "entirely (air-gapped installs, no egress)."
        ),
    )


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

    enabled: bool = Field(
        default=False,
        description=(
            "Gate the HTTP surface on an active AWS Marketplace subscription "
            "(503 except health checks when AWS reports the deployment is not "
            "entitled). Off makes no AWS calls."
        ),
    )
    product_code: str | None = Field(
        default=None,
        description=(
            "Marketplace product code issued by the AWS Marketplace portal for the "
            "container product. Required when ``enabled``."
        ),
    )
    region: str = Field(
        default="us-east-1",
        description="AWS region the License Manager / Metering Service calls go to.",
    )
    # The live listing is contract priced, hence the default; the usage variant
    # stays available in case the listing's pricing model changes.
    pricing_model: Literal["usage", "contract"] = Field(
        default="contract",
        description=(
            "Which Marketplace API the check calls: ``contract`` uses License "
            "Manager ``CheckoutLicense`` (requires ``license_sku``); ``usage`` uses "
            "Metering Service ``RegisterUsage`` (hourly/usage pricing)."
        ),
    )
    refresh_interval_seconds: int = Field(
        default=3600,
        description="Seconds between entitlement re-checks after the one at startup.",
    )
    grace_period_seconds: int = Field(
        default=86400,
        description=(
            "How long (seconds) the last definitive verdict holds while the AWS "
            "API is unreachable or erroring, before the gate fails closed. An "
            'explicit "not entitled" answer locks out immediately.'
        ),
    )
    license_sku: str | None = Field(
        default=None,
        description=(
            "Contract pricing only: the Marketplace product ID from the portal "
            "(``CheckoutLicense`` ``ProductSKU``) — not the product code; the "
            "portal issues both. Required when ``pricing_model`` is ``contract``."
        ),
    )
    # The live listing defines ``users`` and ``executions``
    # (env: JENTIC__ENTITLEMENT__LICENSE_DIMENSIONS).
    license_dimensions: Annotated[list[str], BeforeValidator(_csv_to_list)] = Field(
        default_factory=list,
        description=(
            "Contract pricing only: entitlement dimension keys checked out from the "
            "buyer's license; every one must be granted. Accepts a YAML list or a "
            "comma-separated string."
        ),
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
