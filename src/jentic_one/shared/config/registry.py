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
    # registry DB pool so the sweep never starves live request traffic; the sweep
    # also holds one more registry connection for its single-flight advisory lock.
    update_sweep_deadline_seconds: int = 300
    update_sweep_max_concurrency: int = 4
    # Full-jitter fraction added to the per-cycle sweep interval, to de-phase the
    # scanner across replicas (thundering-herd mitigation). Each time a sweep runs,
    # the next one is due after ``interval * (1 + uniform(0, jitter_ratio))``, so N
    # replicas that start in lock-step drift apart instead of all re-probing the
    # upstream at the same interval boundary. Bounded (default 15%, capped at 100%)
    # so the cadence stays ~daily; 0 disables jitter (deterministic, e.g. for tests).
    update_sweep_jitter_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
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
