"""Broker capacity sweep: max throughput vs concurrency, per worker/CPU config.

Closed loop on purpose: ``oha -c N`` with no rate cap drives the broker as
hard as N concurrent callers can, so each step reports the achievable
throughput and the latency at that concurrency. (Overhead at a fixed rate is
``bench_overhead``'s job; this one finds the ceiling.)

For every ``workers x broker CPUs`` config the broker is recreated, warmed,
then stepped through the concurrency list. During each step one
``docker stats`` sample records CPU per container, so the bottleneck (broker,
Postgres, upstream, or the load generator itself) is visible next to the
throughput number. A step with fewer than ``--min-ok`` 200s is kept with
``"valid": false`` (fast 429/5xx inflate rps) and left out of the report; any
invalid step makes the exit code 1. On exit the broker is recreated with the
caller's own ``PERF_WORKERS`` / ``PERF_BROKER_CPUS`` (defaults 1 / 2).

Usage::

    uv run python -m tests.perf.bench_capacity
    uv run python -m tests.perf.bench_capacity --configs 1x2,2x2,2x4,4x4 --concurrency 8,32,128
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from typing import Any

from tests.perf.bench_overhead import (
    OHA_CONTAINER,
    Case,
    check_ok,
    fmt_num,
    oha_args,
    run_oha,
    start_oha,
    stop_oha,
    summarise,
)
from tests.perf.common import (
    DEFAULT_MIN_OK,
    PerfEnv,
    compose,
    exclusive_run,
    restore_on_exit,
    run_metadata,
    upstream_base,
    wait_healthy,
    write_result,
)

STATS_CONTAINERS = (
    "jentic-perf-broker-1",
    "jentic-perf-postgres-1",
    "jentic-perf-smoke-upstream-1",
    OHA_CONTAINER,
)


def recreate_broker(workers: int, cpus: str) -> None:
    env = {"PERF_WORKERS": str(workers), "PERF_BROKER_CPUS": cpus}
    print(f"== broker workers={workers} cpus={cpus} (recreating)", flush=True)
    compose(
        "up",
        "-d",
        "--force-recreate",
        "--no-deps",
        "--wait",
        "broker",
        env={**os.environ, **env},
    )
    wait_healthy(f"http://127.0.0.1:{os.environ.get('PERF_BROKER_PORT', '18100')}/health")


def sample_cpu(out: dict[str, float], delay_s: float) -> None:
    """One ``docker stats`` sample after ``delay_s`` (mid-step), CPU% per container."""
    time.sleep(delay_s)
    proc = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}\t{{.CPUPerc}}",
            *STATS_CONTAINERS,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        name, _, pct = line.partition("\t")
        try:
            label = "oha" if name == OHA_CONTAINER else name
            out[label.replace("jentic-perf-", "").removesuffix("-1")] = float(pct.rstrip("%"))
        except ValueError:
            continue


def step(url: str, token: str, conc: int, duration_s: float, min_ok: float) -> dict[str, Any]:
    case = Case("capacity")
    args = oha_args(url, case, token, n=None, conns=conc, rate=None, duration_s=duration_s)
    cpu: dict[str, float] = {}
    sampler = threading.Thread(target=sample_cpu, args=(cpu, duration_s / 2), daemon=True)
    sampler.start()
    summary = summarise(run_oha(args), 200)
    sampler.join(timeout=10)
    why = check_ok(summary, min_ok, f"c={conc}")
    return {
        "concurrency": conc,
        **summary,
        "cpu_pct": cpu,
        "valid": why is None,
        "invalid_reasons": [why] if why else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Broker capacity sweep (closed loop, oha)")
    parser.add_argument("--configs", default="1x2,2x2,2x4,4x4", help="WORKERSxCPUS list")
    parser.add_argument("--concurrency", default="8,16,32,64,128")
    parser.add_argument("--duration", type=float, default=15.0, help="seconds per step")
    parser.add_argument(
        "--min-ok",
        type=float,
        default=DEFAULT_MIN_OK,
        help="minimum 200 share for a step to count (default %(default)s)",
    )
    args = parser.parse_args(argv)

    env = PerfEnv.load()
    url = f"{env.broker_url}/{upstream_base(env.vendors['perf-noauth']['alias'])}/behavior/echo"
    token = env.caller("jak").token
    concs = [int(c) for c in args.concurrency.split(",") if c]
    configs = args.configs.split(",")
    env.check_fresh(callers=["jak"], run_s=len(configs) * (len(concs) * args.duration + 60))
    # On exit the broker goes back to the caller's PERF_* config (or its defaults).
    restore_workers = int(os.environ.get("PERF_WORKERS", "1"))
    restore_cpus = os.environ.get("PERF_BROKER_CPUS", "2")
    rows: list[dict[str, Any]] = []
    with (
        exclusive_run("bench_capacity"),
        restore_on_exit(
            ("remove oha sidecar", stop_oha),
            (
                f"broker back to workers={restore_workers} cpus={restore_cpus}",
                lambda: recreate_broker(restore_workers, restore_cpus),
            ),
        ),
    ):
        start_oha()
        for cfg in configs:
            workers_s, _, cpus = cfg.partition("x")
            workers = int(workers_s)
            recreate_broker(workers, cpus)
            # Warm every worker (requests are spread across processes).
            run_oha(oha_args(url, Case("warm"), token, n=400 * workers, conns=16, rate=None))
            for conc in concs:
                r = step(url, token, conc, args.duration, args.min_ok)
                r.update({"workers": workers, "broker_cpus": float(cpus), "config": cfg})
                rows.append(r)
                cpu = "  ".join(f"{k}={v:.0f}%" for k, v in sorted(r["cpu_pct"].items()))
                print(
                    f"  {cfg:<5} c={conc:<4} {fmt_num(r['rps'], '7.1f')} rps  "
                    f"p50={fmt_num(r['p50_ms'], '7.1f')}ms p99={fmt_num(r['p99_ms'], '7.1f')}ms "
                    f"ok={r['ok_ratio']:.3f}  {cpu}" + ("" if r["valid"] else "  INVALID"),
                    flush=True,
                )
        path = write_result("capacity", {**run_metadata("capacity"), "rows": rows})
        print(f"wrote {path}")
    invalid = [r for r in rows if not r["valid"]]
    for r in invalid:
        print(f"INVALID {r['config']} {'; '.join(r['invalid_reasons'])}")
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
