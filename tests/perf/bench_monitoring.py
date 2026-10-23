"""Admin monitoring dashboard load: latency, aggregate consistency, cache pressure.

The perf suite's other load generators all drive the broker's ``POST /execute``.
This one drives the **admin reads** behind the monitoring dashboard —
``GET /monitoring/executions`` and ``GET /monitoring/usage`` — which have a very
different cost profile: no upstream, no credential resolution, but heavy
aggregation over ``admin.execution_records`` behind a 30-second TTL cache.

Three phases, each independently selectable with ``--phase``:

``ramp``
    Concurrency ladder. Each simulated viewer opens the dashboard repeatedly; one
    *view* is a fan-out of several calls (the real page issues one
    ``/monitoring/executions`` plus one ``/monitoring/usage`` per group-by tab),
    which is why the TTL cache exists at all. Reports p50/p95/p99 per step and the
    share of calls that reached the table.

``consistency``
    The accuracy phase, and the reason this is more than a latency benchmark. A
    writer thread inserts execution records while readers poll the aggregates, and
    every response is checked against invariants that must hold whatever rows
    exist (:func:`~tests.perf.monitoring_probe.usage_violations`). ``overall_stats``
    and ``time_buckets`` are separate statements inside one read/write session, so
    under READ COMMITTED each re-snapshots and an insert landing between them can
    make the window total disagree with the sum of its buckets. A latency-only
    stress run would never see that.

``cache``
    Cache pressure. The caches are keyed per method and hold 256 entries with a
    30-second TTL, shared by every admin. Issuing many distinct query shapes
    multiplies entries; this phase sweeps the number of distinct shapes across the
    256-entry bound and reports the miss rate on each side of it.

Cache accounting is DB-side (scans of ``admin.execution_records`` in
``pg_stat_user_tables``), because the caches are in-process and the local perf
config exports no metrics. A hit adds no scan; a miss does.

Usage::

    # Against the compose perf stack (reads app_url/admin_token from PerfEnv):
    uv run python -m tests.perf.bench_monitoring

    # Against any app + Postgres, no compose stack needed:
    uv run python -m tests.perf.bench_monitoring \
        --base-url http://127.0.0.1:8900 --token-file /tmp/token.txt \
        --pg-container my-pg --seed 20000
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tests.perf.common import PerfEnv, exclusive_run, run_metadata, write_result
from tests.perf.monitoring_probe import (
    Call,
    MonitoringClient,
    ProbeError,
    Psql,
    execution_scans,
    executions_violations,
    percentiles,
    seed_executions,
    usage_violations,
)

#: One dashboard view's fan-out: the overview strip plus a tab per group-by.
VIEW_PATHS: tuple[str, ...] = (
    "/monitoring/executions?days=7",
    "/monitoring/usage?group_by=api&top_limit=10",
    "/monitoring/usage?group_by=agent&top_limit=10",
    "/monitoring/usage?group_by=credential&top_limit=10",
)

#: The service's cache bound, mirrored here so the sweep can straddle it.
#: Source: ``_USAGE_CACHE_MAX_ENTRIES`` in admin/services/monitoring_service.py.
CACHE_MAX_ENTRIES = 256


def _one_view(client: MonitoringClient) -> list[Call]:
    return [client.get(path) for path in VIEW_PATHS]


def ramp(
    client: MonitoringClient,
    psql: Psql,
    *,
    concurrency: list[int],
    views_per_level: int,
) -> list[dict[str, Any]]:
    """Serving latency and table-touch rate at each concurrency level.

    Every path is fetched once before the first level, so the cold aggregation and
    the module/pool warm-up land outside the measurement. Without that priming the
    lowest level absorbs both and reports a tail that belongs to start-up, not to
    load: it shows as a large p99 beside a small p50, and a non-zero
    ``table_scans`` at that level alone.

    With a primed cache these numbers are **cached-read serving latency**, not the
    cost of aggregating. ``table_scans`` per level is what separates the two: zero
    means every response came from the 30s TTL cache, so the latency is queueing
    and serialisation. :func:`cache_pressure` is where aggregation gets priced.
    """
    steps: list[dict[str, Any]] = []
    primed = _one_view(client)
    if any(c.status != 200 for c in primed):
        bad = sorted({c.status for c in primed if c.status != 200})
        raise ProbeError(
            f"ramp priming saw {bad} — a refused call aggregates nothing, so the "
            "ramp would measure refusals; re-authenticate and re-run"
        )
    for level in concurrency:
        scans_before = execution_scans(psql)
        calls: list[Call] = []
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=level) as pool:
            futures = [pool.submit(_one_view, client) for _ in range(views_per_level)]
            for fut in futures:
                calls.extend(fut.result())
        wall_s = time.perf_counter() - started
        scans = execution_scans(psql) - scans_before

        ok = [c for c in calls if c.status == 200]
        if len(ok) != len(calls):
            # Fast refusals return in ~1ms without aggregating, so a partial run
            # would report a flatteringly low p95 and a zero scan count.
            bad = sorted({c.status for c in calls if c.status != 200})
            raise ProbeError(
                f"ramp at c={level}: only {len(ok)}/{len(calls)} returned 200 (saw {bad}) — "
                "a refused call skips the aggregation and would understate latency"
            )
        lat = percentiles([c.ms for c in ok])
        by_path: dict[str, dict[str, float]] = {}
        for path in VIEW_PATHS:
            same = [c.ms for c in ok if c.label == path]
            if same:
                by_path[path] = percentiles(same)
        steps.append(
            {
                "concurrency": level,
                "views": views_per_level,
                "calls": len(calls),
                "ok": len(ok),
                "non_200": sorted({c.status for c in calls if c.status != 200}),
                "wall_s": round(wall_s, 3),
                "views_per_s": round(views_per_level / wall_s, 2) if wall_s else 0.0,
                "latency_ms": {k: round(v, 2) for k, v in lat.items()},
                "latency_ms_by_path": {
                    p: {k: round(v, 2) for k, v in d.items()} for p, d in by_path.items()
                },
                "table_scans": scans,
                "scans_per_call": round(scans / len(calls), 3) if calls else 0.0,
            }
        )
        print(
            f"  c={level:<4} {len(ok)}/{len(calls)} ok  "
            f"p50 {lat['p50']:7.1f}ms  p95 {lat['p95']:7.1f}ms  p99 {lat['p99']:7.1f}ms  "
            f"{steps[-1]['views_per_s']:6.1f} views/s  scans {scans}",
            flush=True,
        )
    return steps


def consistency(
    client: MonitoringClient,
    psql: Psql,
    *,
    seconds: float,
    writers: int,
    write_batch: int,
    readers: int,
) -> dict[str, Any]:
    """Poll the aggregates while rows are being inserted; collect invariant breaches."""
    stop = threading.Event()
    violations: list[dict[str, Any]] = []
    lock = threading.Lock()
    counts = {"reads": 0, "writes": 0, "read_errors": 0}

    def write_loop() -> None:
        while not stop.is_set():
            with lock:
                # Distinct trace ids per batch; the offset is read under the lock
                # so two writers never share a series.
                offset = counts["writes"]
            try:
                seed_executions(
                    psql,
                    write_batch,
                    spread_s=3600,
                    actors=8,
                    apis=4,
                    analyze=False,
                    id_offset=offset,
                )
            except ProbeError:
                return
            with lock:
                counts["writes"] += write_batch

    def read_loop() -> None:
        checks = (
            ("/monitoring/usage?group_by=api&top_limit=10", usage_violations),
            ("/monitoring/executions?days=7", executions_violations),
        )
        for path, check in itertools.cycle(checks):
            if stop.is_set():
                return
            try:
                call = client.get(path)
            except ProbeError:
                with lock:
                    counts["read_errors"] += 1
                continue
            with lock:
                counts["reads"] += 1
            if call.status != 200 or call.body is None:
                with lock:
                    violations.append({"path": path, "problem": f"status {call.status}"})
                continue
            for problem in check(call.body):
                with lock:
                    violations.append({"path": path, "problem": problem})

    threads = [threading.Thread(target=write_loop, daemon=True) for _ in range(writers)]
    threads += [threading.Thread(target=read_loop, daemon=True) for _ in range(readers)]
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=30)

    # Group by a number-free kind: the same defect reports different values each
    # time, and a cached inconsistent body is re-served for the whole 30s TTL, so
    # raw strings would scatter into thousands of one-off keys.
    grouped: dict[str, int] = {}
    distinct: set[str] = set()
    for v in violations:
        kind = re.sub(r"-?\d+(\.\d+)?", "N", v["problem"])
        grouped[kind] = grouped.get(kind, 0) + 1
        distinct.add(f"{v['path']}|{v['problem']}")
    print(
        f"  {counts['reads']} reads / {counts['writes']} rows written -> "
        f"{len(distinct)} distinct invariant violations "
        f"({len(violations)} observations; one inconsistent body is cached and "
        "re-served for its whole 30s TTL, so observations overstate how often "
        "the defect occurs)",
        flush=True,
    )
    for kind, n in sorted(grouped.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>5}x {kind}", flush=True)
    return {
        "seconds": seconds,
        "writers": writers,
        "write_batch": write_batch,
        "readers": readers,
        **counts,
        "violation_count": len(violations),
        "distinct_violation_count": len(distinct),
        "violations_by_kind": grouped,
        "violation_samples": violations[:20],
    }


def cache_pressure(
    client: MonitoringClient,
    psql: Psql,
    *,
    shape_counts: list[int],
    repeats: int,
) -> list[dict[str, Any]]:
    """Miss rate as the number of distinct query shapes crosses the entry bound.

    Each shape is a distinct ``since`` (so a distinct cache key). With ``shapes``
    below the bound every shape should be a hit after its first fetch; above it,
    eviction forces repeated misses.
    """
    rows: list[dict[str, Any]] = []
    for shapes in shape_counts:
        paths = [
            f"/monitoring/usage?group_by=api&top_limit=10&since={1_700_000_000 + i * 600}"
            for i in range(shapes)
        ]
        # Prime: every shape fetched once, so later passes could all be hits.
        statuses: dict[int, int] = {}
        for path in paths:
            primed = client.get(path)
            statuses[primed.status] = statuses.get(primed.status, 0) + 1
        scans_before = execution_scans(psql)
        calls = 0
        for _ in range(repeats):
            for path in paths:
                call = client.get(path)
                statuses[call.status] = statuses.get(call.status, 0) + 1
                calls += 1
        scans = execution_scans(psql) - scans_before
        # A non-200 never reaches the table, so it would read as a perfect cache
        # hit. Refuse the measurement rather than report a flattering zero.
        bad = {s: n for s, n in statuses.items() if s != 200}
        if bad:
            raise ProbeError(
                f"cache phase at shapes={shapes}: {bad} non-200 responses — a refused call "
                "touches no table and would read as a cache hit; re-authenticate and re-run"
            )
        rows.append(
            {
                "distinct_shapes": shapes,
                "beyond_bound": shapes > CACHE_MAX_ENTRIES,
                "calls_after_priming": calls,
                "table_scans": scans,
                "scans_per_call": round(scans / calls, 3) if calls else 0.0,
            }
        )
        print(
            f"  shapes={shapes:<5}{'(over bound)' if shapes > CACHE_MAX_ENTRIES else '':<13}"
            f"{calls:>6} calls  {scans:>6} scans  {rows[-1]['scans_per_call']:.3f}/call",
            flush=True,
        )
    return rows


def _resolve_auth(args: argparse.Namespace) -> tuple[str, str]:
    """Base URL and admin token, from explicit flags or else from ``PerfEnv``.

    ``PerfEnv`` is only consulted for what the flags leave out, and login
    credentials count as a way to supply the token: given a base URL and
    ``--login-email``/``--login-password`` the client mints its own on the first
    401, so a run against a standalone app needs no ``.perf-env.json`` and no
    compose stack at all.
    """
    token = Path(args.token_file).read_text().strip() if args.token_file else (args.token or "")
    base = args.base_url or ""
    can_login = bool(args.login_email and args.login_password)
    if not base or not (token or can_login):
        env = PerfEnv.load()
        base = base or env.app_url
        token = token or env.admin_token
        if not token:
            raise SystemExit(
                "no admin token: run `make perf-provision`, pass --token-file, "
                "or pass --login-email with --login-password"
            )
    return base, token


def _int_list(raw: str) -> list[int]:
    return [int(part) for part in raw.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Admin monitoring read load + accuracy under load")
    parser.add_argument("--base-url", help="admin surface base URL (default: PerfEnv.app_url)")
    parser.add_argument("--token", help="admin bearer token (default: PerfEnv.admin_token)")
    parser.add_argument("--token-file", help="file holding the admin bearer token")
    parser.add_argument(
        "--login-email",
        help="re-mint the token on a 401 with these credentials (needs --login-password)",
    )
    parser.add_argument("--login-password", help="password for --login-email")
    parser.add_argument(
        "--pg-container",
        help="docker container running Postgres; omit to use the compose 'postgres' service",
    )
    parser.add_argument(
        "--phase",
        default="ramp,consistency,cache",
        help="comma-separated subset of ramp,consistency,cache",
    )
    parser.add_argument("--seed", type=int, default=0, help="rows to insert before measuring")
    parser.add_argument("--seed-spread-s", type=int, default=7 * 86400)
    parser.add_argument("--seed-actors", type=int, default=25)
    parser.add_argument("--seed-apis", type=int, default=8)
    parser.add_argument("--concurrency", default="1,4,16,32")
    parser.add_argument(
        "--views",
        type=int,
        default=250,
        help="dashboard views per ramp step; each is 4 calls, so 250 gives 1000 "
        "samples per step and 250 per path — enough for a p99 to mean something",
    )
    parser.add_argument("--consistency-seconds", type=float, default=15.0)
    parser.add_argument("--consistency-readers", type=int, default=4)
    parser.add_argument("--consistency-writers", type=int, default=2)
    parser.add_argument("--consistency-write-batch", type=int, default=200)
    parser.add_argument("--cache-shapes", default="8,64,256,512")
    parser.add_argument("--cache-repeats", type=int, default=2)
    args = parser.parse_args(argv)

    phases = {p.strip() for p in args.phase.split(",") if p.strip()}
    unknown = phases - {"ramp", "consistency", "cache"}
    if unknown:
        raise SystemExit(f"unknown phase(s): {', '.join(sorted(unknown))}")

    base, token = _resolve_auth(args)
    credentials = (
        (args.login_email, args.login_password)
        if args.login_email and args.login_password
        else None
    )
    client = MonitoringClient(base, token, credentials=credentials)
    psql = Psql(container=args.pg_container)

    probe = client.get("/monitoring/usage?group_by=api&top_limit=1")
    if probe.status != 200:
        raise SystemExit(
            f"GET /monitoring/usage returned {probe.status} — the token needs org:admin "
            "and the admin surface must be up"
        )

    payload: dict[str, Any] = {
        **run_metadata("monitoring"),
        "base_url": base,
        "view_fan_out": list(VIEW_PATHS),
        "cache_max_entries": CACHE_MAX_ENTRIES,
        "reauth_enabled": credentials is not None,
    }

    # Every host-side tool serialises on results/.perf.lock: two of them
    # driving the same app and Postgres would each read the other's latency
    # and scan counts as its own, and both artifacts would still look sound.
    with exclusive_run("monitoring"):
        if args.seed:
            print(f"seeding {args.seed} execution records…", flush=True)
            total = seed_executions(
                psql,
                args.seed,
                spread_s=args.seed_spread_s,
                actors=args.seed_actors,
                apis=args.seed_apis,
            )
            print(f"  admin.execution_records now holds {total} rows", flush=True)
            payload["seeded"] = {"inserted": args.seed, "table_total": total}

        if "ramp" in phases:
            print("ramp — dashboard views at rising concurrency:", flush=True)
            payload["ramp"] = ramp(
                client,
                psql,
                concurrency=_int_list(args.concurrency),
                views_per_level=args.views,
            )
        if "consistency" in phases:
            print("consistency — aggregates while rows are inserted:", flush=True)
            payload["consistency"] = consistency(
                client,
                psql,
                seconds=args.consistency_seconds,
                writers=args.consistency_writers,
                write_batch=args.consistency_write_batch,
                readers=args.consistency_readers,
            )
        if "cache" in phases:
            print(
                f"cache pressure — distinct shapes vs the {CACHE_MAX_ENTRIES}-entry bound:",
                flush=True,
            )
            payload["cache"] = cache_pressure(
                client,
                psql,
                shape_counts=_int_list(args.cache_shapes),
                repeats=args.cache_repeats,
            )

        payload["token_relogins"] = client.relogins
        path = write_result("monitoring", payload)
        print(f"wrote {path}")
        print(f"ARTIFACT: {json.dumps(str(path))}")
    breaches = int(payload.get("consistency", {}).get("violation_count", 0))
    return 1 if breaches else 0


if __name__ == "__main__":
    sys.exit(main())
