"""Server-side "latest jentic-one release" lookup, cached in-process.

Powers ``GET /system/version``: asks GitHub for the newest published release of
the configured repo and returns its version so the UI can show an "update
available" banner. The backend already fetches from GitHub server-side for the
API catalog (``registry.services.catalog``), so this reuses the same hardened,
SSRF-guarded ``fetch_json`` helper rather than introducing a new egress path or
pushing the value in from the CLI.

Fetch-on-read with a per-process TTL cache (no background job, no DB): the first
read after the TTL expires triggers one GitHub request; concurrent readers wait
on a single-flight lock so a burst of pollers still makes at most one request.
Every failure degrades to ``None`` ("latest unknown", no banner) — the version
probe must never break the app.

Gated by config: runs only when ``release_check.enabled`` and the backend is
``local`` (a self-hosted install the operator can actually update), and
``cache_ttl_seconds == 0`` is a kill switch (air-gapped installs, no egress).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog

from jentic_one.registry.services.catalog.fetch import CatalogFetchError, fetch_json
from jentic_one.shared.config import AppConfig
from jentic_one.shared.version_compare import normalize_release

# ``CatalogFetchError`` is re-exported so callers/tests can reference the one
# failure type this module raises through without reaching into the catalog
# package (which pulls in a heavier import chain and risks a circular import).
__all__ = ["CatalogFetchError", "ReleaseChecker"]

_log = structlog.get_logger(__name__)

# GitHub's "latest published, non-prerelease, non-draft release" endpoint. It
# returns a single JSON object with a ``tag_name`` (e.g. "v0.26.0"); we then
# normalise that to a bare "X.Y.Z". (The plural ``/releases`` returns an array,
# which ``fetch_json`` rejects — and would include drafts/prereleases anyway.)
_RELEASE_URL = "https://api.github.com/repos/{repo}/releases/latest"


@dataclass(slots=True)
class _CacheEntry:
    """A resolved latest-release version and when it was cached (monotonic)."""

    version: str | None
    cached_at: float


class ReleaseChecker:
    """Resolves the latest published release, cached in-process with a TTL.

    One instance is held on the application ``Context`` so the cache and its
    single-flight lock are shared across all requests on this process. Safe to
    construct eagerly; it performs no I/O until :meth:`latest_version` is called.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._entry: _CacheEntry | None = None
        # Coalesces a burst of concurrent readers into a single GitHub request.
        self._lock = asyncio.Lock()

    async def latest_version(self) -> str | None:
        """Return the latest release as bare ``X.Y.Z``, or ``None`` if unknown.

        ``None`` covers every "no banner" case: the check is disabled, this is a
        remote backend, or the fetch failed/was unparseable. Never raises.
        """
        cfg = self._config.release_check
        ttl = cfg.cache_ttl_seconds
        # Kill switch / not applicable: disabled, air-gapped (ttl 0), or a hosted
        # backend the operator can't self-update. No egress, no cache.
        if not cfg.enabled or ttl <= 0 or self._config.server.backend != "local":
            return None

        now = time.monotonic()
        entry = self._entry
        if entry is not None and (now - entry.cached_at) < ttl:
            return entry.version

        async with self._lock:
            # Re-check under the lock: a concurrent caller may have just refreshed
            # while we waited, so we don't stampede GitHub.
            entry = self._entry
            now = time.monotonic()
            if entry is not None and (now - entry.cached_at) < ttl:
                return entry.version
            version = await self._fetch_latest(cfg.repo)
            self._entry = _CacheEntry(version=version, cached_at=time.monotonic())
            return version

    async def _fetch_latest(self, repo: str) -> str | None:
        """Fetch + normalise the latest release tag; ``None`` on any failure."""
        url = _RELEASE_URL.format(repo=repo)
        try:
            doc = await fetch_json(url, config=self._config.ingest)
        except CatalogFetchError:
            # Offline, rate-limited, private repo, 404 — all "latest unknown".
            _log.warning("system.version.release_fetch_failed", repo=repo, exc_info=True)
            return None
        tag = doc.get("tag_name")
        if not isinstance(tag, str):
            return None
        # A pre-release/oddly-tagged release normalises to None (no false banner).
        return normalize_release(tag)
