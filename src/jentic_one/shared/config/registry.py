"""Registry configuration: search, ingest, and catalog."""

from __future__ import annotations

from pydantic import BaseModel, Field

from jentic_one.shared.config.egress import EgressConfig


class SearchConfig(BaseModel):
    """Search configuration.

    The built-in mode is "lexical" (BM25 on SQLite, native full-text on
    PostgreSQL). ``search_mode`` is validated against the registered
    SearchStrategy set at resolve time (``resolve_strategy``), so an unknown mode
    fails loudly with the available modes for the active dialect rather than at
    config load. Additional modes (e.g. "semantic", "vector") can be registered
    via ``register_strategy`` without editing this schema.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Build the lexical search text for operations at import time. APIs "
            "imported while this is off are not findable by lexical search until "
            "re-imported."
        ),
    )
    search_enabled: bool = Field(
        default=True,
        description=(
            "Serve search queries. Off, search requests fail with a "
            "search-disabled error; indexing at import is controlled separately by "
            "``enabled``."
        ),
    )
    search_mode: str = Field(
        default="lexical",
        description=(
            "Search strategy name, resolved per database dialect against the "
            "registered strategies. ``lexical`` (BM25 on SQLite, native full-text "
            "on PostgreSQL) is built in; an unknown mode fails on first use with "
            "the available modes listed."
        ),
    )


class IngestConfig(BaseModel):
    """Spec ingestion settings (fetch limits, timeouts, egress policy)."""

    # Public-API specs in the catalog (Stripe, AWS, Azure, …) routinely exceed a
    # few MB; 25 MiB comfortably covers the largest specs in jentic-public-apis
    # while still bounding memory per fetch.
    max_spec_bytes: int = Field(
        default=25 * 1024 * 1024,
        description=(
            "Largest API spec (bytes) accepted from a URL fetch or an inline import "
            "source, fetched for a catalog preview, or produced by applying an "
            "overlay (default 25 MiB)."
        ),
    )
    fetch_timeout_s: float = Field(
        default=30.0,
        description="Timeout (seconds) for fetching a spec or the catalog manifest from a URL.",
    )
    max_redirects: int = Field(
        default=5,
        description=(
            "Maximum HTTP redirects followed when fetching a spec or the catalog "
            "manifest; each hop is re-checked against the egress policy."
        ),
    )
    # SSRF/egress policy for fetching URL sources. Strict by default (private
    # ranges blocked); opt in to internal targets for in-cluster/corporate
    # imports — mirrors broker.egress. See EgressConfig.
    egress: EgressConfig = Field(default_factory=EgressConfig)
    # Enforced before the body is parsed, so an oversized batch never reaches
    # the job queue. The 2x default leaves room for one maximum-size inline spec
    # plus typical JSON-string escaping, and scales when ``max_spec_bytes`` is
    # raised. It is not a guarantee: control characters, or encoders that write
    # non-ASCII as ``\uXXXX`` or HTML-escape ``<>&``, expand a byte to six.
    max_import_request_bytes: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Cap on the raw `POST /apis` request body (all sources together). "
            "Unset means twice `max_spec_bytes`, which covers one maximum-size "
            "inline spec with typical JSON-string escaping; raise it for batches "
            "of heavily escaped specs (`\\uXXXX` or HTML-escaped content). Each "
            "inline source's content is separately capped at `max_spec_bytes`."
        ),
    )

    @property
    def import_request_cap_bytes(self) -> int:
        """Effective ``POST /apis`` body cap (``max_import_request_bytes`` or its default)."""
        if self.max_import_request_bytes is not None:
            return self.max_import_request_bytes
        return 2 * self.max_spec_bytes


class CatalogConfig(BaseModel):
    """Public API catalog settings (manifest source + staleness)."""

    manifest_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis/openapi/apis.json"
        ),
        description=(
            "Manifest source for the public API catalog. Full default: "
            "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/"
            "apis/openapi/apis.json"
        ),
    )
    manifest_max_age_seconds: int = Field(
        default=86400,
        description=(
            "Age (seconds) after which the cached catalog manifest is refreshed on "
            "the next catalog read. 0 disables automatic refresh (only "
            "``POST /catalog:refresh``)."
        ),
    )
    # The scanner starts only when both the registry and admin DBs are present.
    # A persistent per-API gate keeps the scanner and a manual refresh from
    # probing the same API twice within one interval.
    update_check_interval_seconds: int = Field(
        default=86400,
        description=(
            "Seconds between update-check sweeps, which conditionally re-fetch the "
            "spec URLs of upstream-tracked APIs and emit "
            "``catalog.update_available`` when an upstream spec changed; each API "
            "is probed at most once per interval. ``POST /catalog:refresh`` also "
            "triggers a sweep. 0 disables the check entirely (no events, no egress)."
        ),
    )
    # The sweep runs off the triggering read but still probes every tracked spec
    # over the network, so the batch is bounded. It also holds one more registry
    # connection for its single-flight advisory lock.
    update_sweep_deadline_seconds: int = Field(
        default=300,
        description=(
            "Wall-clock budget (seconds) for one update-check sweep; it stops when exceeded."
        ),
    )
    update_sweep_max_concurrency: int = Field(
        default=4,
        description=(
            "Maximum concurrent spec probes in one update-check sweep. Keep it below "
            "the registry database pool size so sweeps never starve request traffic."
        ),
    )
    update_sweep_jitter_ratio: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description=(
            "Random extra delay added to each sweep interval, as a fraction of it "
            "(next sweep after ``interval * (1 + uniform(0, ratio))``), so replicas "
            "started together drift apart. 0 disables jitter."
        ),
    )
    # Catalog preview (``GET /catalog/{id}/operations``) fetches the upstream
    # spec (up to ``ingest.max_spec_bytes``); the slimmed operation projection is
    # kept in-process so repeated previews of the same spec within the TTL cost
    # one upstream fetch. Entries are bounded by count, not bytes, so keep the
    # count small: a projection of a very large spec can itself be several MB.
    # The cache is per process: ``POST /catalog:refresh`` clears only the
    # serving process's copy, and other workers/replicas refetch after the TTL.
    preview_cache_ttl_seconds: int = Field(
        default=300,
        ge=0,
        description=(
            "How long a catalog preview's upstream spec projection is reused before "
            "refetching. Zero disables the cache. The cache is per process: "
            "`POST /catalog:refresh` clears it only on the process that serves the "
            "refresh, so other workers or replicas can serve the previous projection "
            "until this TTL expires."
        ),
    )
    preview_cache_failure_ttl_seconds: int = Field(
        default=30,
        ge=0,
        description=(
            "How long a failed catalog preview fetch (upstream unavailable, oversized "
            "or unparseable spec) is remembered per spec URL before it is retried, so "
            "repeated previews of a broken spec do not refetch it on every request. "
            "Zero retries on the next request. Ignored when the preview cache is "
            "disabled."
        ),
    )
    preview_cache_max_entries: int = Field(
        default=16,
        ge=0,
        description=(
            "Maximum number of catalog spec projections held in memory per process "
            "(least recently used evicted first). Zero disables the cache."
        ),
    )
