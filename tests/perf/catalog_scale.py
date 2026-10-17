"""Catalog read latency at scale: browse and token search over a large snapshot.

Builds a synthetic catalog (``jentic_one.testing.synthetic_catalog``: by
default 50,000 entries spread over 10 ``static`` sources) in throwaway SQLite
databases, syncs every source once, then times ``CatalogService.list_all``
for browse pages (first page and deep keyset cursors) and token searches,
exactly as ``GET /catalog`` calls it. Each timed call includes the per-request
database reads (source rows, registered and outdated sets); the in-process
snapshot is warm after the first call, which is reported separately.

Exits ``1`` when a p95 exceeds its budget (``--browse-p95-ms``,
``--search-p95-ms``). Local only; not collected by pytest and not run in CI::

    make perf-catalog
    uv run python -m tests.perf.catalog_scale --entries 10000 --iterations 50
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from alembic import command

from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import encode_catalog_cursor
from jentic_one.testing import register_static_source_kind, synthetic_catalog
from tests.harness.postgres import DB_NAMES, alembic_config_for, encryption_config

_SEARCHES = (
    "api4242",
    "api1",
    "example",
    "synthetic api",
    "api31337.example.com",
    "nothing-matches-this",
)


def _config(base: Path, *, entries: int, sources: int) -> AppConfig:
    slugs = [f"src{i:02d}" for i in range(sources)]
    return AppConfig.model_validate(
        {
            "databases": {
                name: {"backend": "sqlite", "path": str(base / f"{name}.db"), "schema_name": name}
                for name in DB_NAMES
            },
            "credentials": encryption_config(),
            "catalog": {
                "sources": [
                    cfg.model_dump(mode="json") for cfg in synthetic_catalog(entries, slugs)
                ]
            },
        }
    )


def _migrate(config: AppConfig) -> None:
    for name in DB_NAMES:
        command.upgrade(alembic_config_for(name, getattr(config.databases, name)), "head")


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
        f"{name:<8} n={len(samples):<5} p50={statistics.median(samples):7.2f} ms  "
        f"p95={p95:7.2f} ms  max={max(samples):7.2f} ms  budget={budget_ms:.0f} ms  "
        f"{'ok' if ok else 'OVER BUDGET'}"
    )
    return ok


async def _run(args: argparse.Namespace, config: AppConfig) -> bool:
    async with Context(config) as ctx:
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
    browse_ok = _report("browse", browse, args.browse_p95_ms)
    search_ok = _report("search", search, args.search_p95_ms)
    return browse_ok and search_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=int, default=50_000)
    parser.add_argument("--sources", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--browse-p95-ms", type=float, default=60.0)
    parser.add_argument("--search-p95-ms", type=float, default=150.0)
    args = parser.parse_args(argv)

    register_static_source_kind()
    with tempfile.TemporaryDirectory(prefix="jentic-perf-catalog-") as tmp:
        config = _config(Path(tmp), entries=args.entries, sources=args.sources)
        _migrate(config)
        ok = asyncio.run(_run(args, config))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
