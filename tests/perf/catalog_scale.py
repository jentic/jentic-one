"""Catalog read latency at scale: browse and token search over a large snapshot.

Builds a synthetic catalog (``jentic_one.testing.synthetic_catalog``: by
default 50,000 entries spread over 10 ``static`` sources) in throwaway SQLite
databases, syncs every source once, then times ``CatalogService.list_all``
for browse pages (first page and deep keyset cursors) and token searches,
exactly as ``GET /catalog`` calls it. Each timed call includes the per-request
database reads (source rows, registered and outdated sets); the in-process
snapshot is warm after the first call, which is reported separately.

``--asgi`` also times the same browse and search mix end to end through
``GET /catalog`` on the registry app (in-process ASGI, auth resolved to a
public reader), so routing, validation and serialisation are included.
``--backend postgres`` runs against the fixture Postgres on ``JENTIC_PG_PORT``
(use a throwaway container: the run empties the catalog tables). The process's
peak RSS is printed at the end.

Exits ``1`` when a p95 exceeds its budget (``--browse-p95-ms``,
``--search-p95-ms``). Local only; not collected by pytest and not run in CI::

    make perf-catalog
    uv run python -m tests.perf.catalog_scale --entries 10000 --iterations 50
    JENTIC_PG_PORT=55453 uv run python -m tests.perf.catalog_scale --backend postgres --asgi
"""

from __future__ import annotations

import argparse
import asyncio
import random
import resource
import statistics
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from alembic import command
from sqlalchemy import delete

from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_staged_entries import CatalogStagedEntry
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import encode_catalog_cursor
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.testing import register_static_source_kind, synthetic_catalog
from tests.harness.postgres import (
    DB_NAMES,
    alembic_config_for,
    encryption_config,
    ensure_schema,
    postgres_config,
)

_SEARCHES = (
    "api4242",
    "api1",
    "example",
    "synthetic api",
    "api31337.example.com",
    "nothing-matches-this",
)


def _catalog(entries: int, sources: int) -> dict[str, object]:
    slugs = [f"src{i:02d}" for i in range(sources)]
    return {"sources": [cfg.model_dump(mode="json") for cfg in synthetic_catalog(entries, slugs)]}


def _sqlite_config(base: Path, *, entries: int, sources: int) -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {
                name: {"backend": "sqlite", "path": str(base / f"{name}.db"), "schema_name": name}
                for name in DB_NAMES
            },
            "credentials": encryption_config(),
            "catalog": _catalog(entries, sources),
        }
    )


def _postgres_config(*, entries: int, sources: int) -> AppConfig:
    base = postgres_config().model_dump(mode="python")
    return AppConfig.model_validate({**base, "catalog": _catalog(entries, sources)})


def _migrate(config: AppConfig) -> None:
    for name in DB_NAMES:
        db = getattr(config.databases, name)
        if db.backend == "postgresql":
            asyncio.run(ensure_schema(db))
        command.upgrade(alembic_config_for(name, db), "head")


async def _empty_catalog(ctx: Context) -> None:
    """Start from no catalog rows (a reused Postgres keeps them between runs)."""
    async with ctx.registry_db.session() as session:
        for table in (
            CatalogStagedEntry,
            CatalogEntryRow,
            CatalogSyncRun,
            CatalogSourceRow,
        ):
            await session.execute(delete(table))
        await session.commit()


def _peak_rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Bytes on macOS, KiB on Linux.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


async def _time(fn: Callable[[], Awaitable[object]], iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def _p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=100, method="inclusive")[94]


def _report(name: str, samples: list[float], budget_ms: float) -> bool:
    p95 = _p95(samples)
    ok = p95 <= budget_ms
    print(
        f"{name:<10} n={len(samples):<5} p50={statistics.median(samples):7.2f} ms  "
        f"p95={p95:7.2f} ms  max={max(samples):7.2f} ms  budget={budget_ms:.0f} ms  "
        f"{'ok' if ok else 'OVER BUDGET'}"
    )
    return ok


def _asgi_client(ctx: Context) -> httpx.AsyncClient:
    app = create_app(ctx)
    reader = Identity(
        sub="usr_perf", email="usr_perf@perf.local", permissions=["capabilities:read"]
    )

    async def _identity(_: object = None) -> Identity:
        return reader

    app.dependency_overrides[resolve_identity] = _identity
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://perf",
        headers={"Authorization": "Bearer perf"},
    )


async def _run(args: argparse.Namespace, config: AppConfig) -> bool:
    async with Context(config) as ctx:
        await _empty_catalog(ctx)
        svc = CatalogService(ctx)
        start = time.perf_counter()
        result = await svc.refresh()
        print(f"synced   {result.count} entries in {time.perf_counter() - start:.1f} s")

        start = time.perf_counter()
        first = await svc.list_all(permissions=frozenset(), limit=args.limit)
        print(f"cold     first read (snapshot build) {(time.perf_counter() - start) * 1000:.1f} ms")
        assert first.catalog_total == args.entries, first.catalog_total

        rng = random.Random(1853)
        slugs = [f"src{i:02d}" for i in range(args.sources)]

        async def _browse() -> None:
            # Half first pages, half deep keyset pages from a random position.
            cursor = None
            if rng.random() < 0.5:
                entry = f"{rng.choice(slugs)}/api-{rng.randrange(args.entries)}"
                cursor = encode_catalog_cursor(entry)
            await svc.list_all(permissions=frozenset(), cursor=cursor, limit=args.limit)

        async def _search() -> None:
            await svc.list_all(permissions=frozenset(), q=rng.choice(_SEARCHES), limit=args.limit)

        browse = await _time(_browse, args.iterations)
        search = await _time(_search, args.iterations)
        ok = _report("browse", browse, args.browse_p95_ms)
        ok = _report("search", search, args.search_p95_ms) and ok

        if args.asgi:
            async with _asgi_client(ctx) as client:

                async def _get(params: dict[str, str | int]) -> None:
                    r = await client.get("/catalog", params=params)
                    assert r.status_code == 200, r.text

                async def _http_browse() -> None:
                    params: dict[str, str | int] = {"limit": args.limit}
                    if rng.random() < 0.5:
                        entry = f"{rng.choice(slugs)}/api-{rng.randrange(args.entries)}"
                        params["cursor"] = encode_catalog_cursor(entry)
                    await _get(params)

                async def _http_search() -> None:
                    await _get({"q": rng.choice(_SEARCHES), "limit": args.limit})

                http_browse = await _time(_http_browse, args.iterations)
                http_search = await _time(_http_search, args.iterations)
            ok = _report("GET browse", http_browse, args.browse_p95_ms) and ok
            ok = _report("GET search", http_search, args.search_p95_ms) and ok
        await _empty_catalog(ctx)
    print(f"peak RSS {_peak_rss_mib():.0f} MiB")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=int, default=50_000)
    parser.add_argument("--sources", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--browse-p95-ms", type=float, default=60.0)
    parser.add_argument("--search-p95-ms", type=float, default=150.0)
    parser.add_argument("--backend", choices=("sqlite", "postgres"), default="sqlite")
    parser.add_argument("--asgi", action="store_true", help="also time GET /catalog over ASGI")
    args = parser.parse_args(argv)

    register_static_source_kind()
    print(f"backend  {args.backend}")
    if args.backend == "postgres":
        config = _postgres_config(entries=args.entries, sources=args.sources)
        _migrate(config)
        ok = asyncio.run(_run(args, config))
        return 0 if ok else 1
    with tempfile.TemporaryDirectory(prefix="jentic-perf-catalog-") as tmp:
        config = _sqlite_config(Path(tmp), entries=args.entries, sources=args.sources)
        _migrate(config)
        ok = asyncio.run(_run(args, config))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
