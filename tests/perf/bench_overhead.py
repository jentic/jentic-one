"""Broker overhead A/B bench: the same request direct-to-upstream vs via the broker.

Runs ``oha`` *inside* the perf compose network (open loop: fixed ``-q`` rate
with ``--latency-correction``, so a slow response cannot hide queueing delay)
against the smoke-upstream harness twice per case — once directly, once through
the broker — and records both latency distributions plus the per-percentile
delta. Broker variants (streaming off, cold caches, ...) are applied by
recreating the broker container with the variant's ``PERF_*`` environment.

Percentile deltas are *indicative*: percentiles are not additive, so
``p99(broker) - p99(direct)`` is not the p99 of the overhead. ``mean`` deltas
are exact. Report both; never subtract server-side histogram quantiles.

Usage::

    uv run python -m tests.perf.bench_overhead                 # full matrix
    uv run python -m tests.perf.bench_overhead --quick         # 10 rps only, short
    uv run python -m tests.perf.bench_overhead --only noauth-post-small,bearer-post-small
    uv run python -m tests.perf.bench_overhead --list
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from tests.perf.common import (
    ECHO_PATH,
    GET_PATH,
    RESULTS_DIR,
    PerfEnv,
    compose,
    run_metadata,
    upstream_base,
    wait_healthy,
    write_result,
)

BROKER_HOST_HEALTH = "http://127.0.0.1:{port}/health"
SMALL_BODY = '{"probe":true}'
#: Bodies written under results/bodies and handed to oha via ``-D`` (argv caps ~128 KB).
BODY_SIZES = {"1k": 1024, "100k": 100 * 1024, "1m": 1024 * 1024}
#: Variant name -> PERF_* overrides applied to the broker container.
VARIANTS: dict[str, dict[str, str]] = {
    "default": {},
    "buffered": {"PERF_STREAMING": "false"},
    "cold-cache": {"PERF_RESOLVE_TTL": "0", "PERF_TOOLKIT_TTL": "0", "PERF_RULE_TTL": "0"},
}


@dataclass
class Case:
    name: str
    vendor: str = "perf-noauth"
    method: str = "POST"
    path: str = ECHO_PATH
    body: str | None = SMALL_BODY
    body_file: str | None = None
    caller: str = "jak"
    headers: dict[str, str] = field(default_factory=dict)
    variant: str = "default"
    #: Rates (rps) to run; None = the CLI default set.
    rates: tuple[int, ...] | None = None
    #: Hard cap on requests per run (async jobs must not flood admin.jobs).
    max_requests: int | None = None
    #: Accept non-200 success (e.g. 202 for respond-async).
    expect_status: int = 200
    #: Skip the direct baseline (no meaningful direct equivalent).
    no_direct: bool = False


def cases(env: PerfEnv) -> list[Case]:
    noauth = env.vendors["perf-noauth"]["api"]
    pin = (
        f"{noauth['vendor']}:{noauth['name']}:{noauth['version']}"
        f"=rev_{noauth['revision_id'].replace('-', '')}"
    )
    out = [
        # Payload / method
        Case("noauth-post-small"),
        Case("noauth-get", method="GET", path=GET_PATH, body=None),
        *(Case(f"noauth-post-{label}", body=None, body_file=label) for label in BODY_SIZES),
        # Credential type (same route, one vendor each)
        Case("bearer-post-small", vendor="perf-bearer"),
        Case("apikey-hdr-post-small", vendor="perf-apikey-hdr"),
        Case("apikey-qry-post-small", vendor="perf-apikey-qry"),
        Case("apikey-cookie-post-small", vendor="perf-apikey-cookie"),
        Case("sigv4-post-small", vendor="perf-sigv4"),
        # Caller auth (jak_ is the default above)
        Case("noauth-post-small-at", caller="at"),
        Case("noauth-post-small-jwt", caller="jwt"),
        # Revision pin (extra registry queries)
        Case("noauth-post-small-pinned", headers={"Jentic-Revision": pin}),
        # Streaming vs buffered persistence path
        Case("noauth-post-small-buffered", variant="buffered"),
        # Idempotency replay (same key every request => all but the first replay).
        # First-call cost needs a unique key per request: see the Locust
        # IdempotentFirstCallUser. Held on the buffered variant so the
        # persistence path is constant between the two.
        Case(
            "noauth-idem-replay-buffered",
            variant="buffered",
            headers={"Idempotency-Key": "perf-idem-replay-fixed"},
            no_direct=True,
        ),
        # Cold caches: rates kept low so requests don't overlap (SingleFlight
        # still coalesces concurrent rule fetches at TTL=0).
        Case("noauth-post-small-cold", variant="cold-cache", rates=(1, 10)),
        Case("noauth-post-small-cold-at", variant="cold-cache", caller="at", rates=(1, 10)),
        Case("noauth-post-small-cold-jwt", variant="cold-cache", caller="jwt", rates=(1, 10)),
        # respond-async: oha only sees time-to-202; completion is a Locust user.
        Case(
            "noauth-async-202",
            headers={"Prefer": "respond-async"},
            expect_status=202,
            rates=(10,),
            max_requests=200,
            no_direct=True,
        ),
    ]
    return out


def ensure_bodies() -> None:
    bodies = RESULTS_DIR / "bodies"
    bodies.mkdir(parents=True, exist_ok=True)
    for label, size in BODY_SIZES.items():
        path = bodies / f"{label}.json"
        if not path.exists() or path.stat().st_size != size + 10:
            # A JSON string padded to exactly `size` payload bytes.
            path.write_text('{"pad":"' + ("x" * size) + '"}')


def apply_variant(name: str, current: str | None) -> str:
    if name == current:
        return name
    overrides = VARIANTS[name]
    env = {**os.environ, **overrides}
    print(f"== variant {name}: {overrides or 'defaults'} (recreating broker)")
    compose("up", "-d", "--force-recreate", "--no-deps", "--wait", "broker", env=env)
    port = os.environ.get("PERF_BROKER_PORT", "18100")
    wait_healthy(BROKER_HOST_HEALTH.format(port=port))
    return name


OHA_CONTAINER = "jentic-perf-oha"


def start_oha() -> None:
    """One long-lived oha container (exec per run) instead of `compose run` each time."""
    subprocess.run(["docker", "rm", "-f", OHA_CONTAINER], capture_output=True, check=False)
    compose(
        "--profile",
        "tools",
        "run",
        "-d",
        "--name",
        OHA_CONTAINER,
        "--entrypoint",
        "sleep",
        "oha",
        "infinity",
        capture=True,
    )


def stop_oha() -> None:
    subprocess.run(["docker", "rm", "-f", OHA_CONTAINER], capture_output=True, check=False)


def oha(url: str, case: Case, rate: int, n: int, token: str | None) -> dict[str, Any]:
    conns = max(4, min(64, rate // 2))
    args = [
        "docker",
        "exec",
        OHA_CONTAINER,
        "oha",
        "-n",
        str(n),
        "-c",
        str(conns),
        "-q",
        str(rate),
        "--latency-correction",
        "--no-tui",
        "--output-format",
        "json",
        "-m",
        case.method,
        "-t",
        "35s",
    ]
    if case.body is not None:
        args += ["-d", case.body, "-T", "application/json"]
    elif case.body_file is not None:
        args += ["-D", f"/results/bodies/{case.body_file}.json", "-T", "application/json"]
    if token:
        args += ["-H", f"Authorization: Bearer {token}"]
    for key, value in case.headers.items():
        args += ["-H", f"{key}: {value}"]
    args.append(url)
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"oha failed ({proc.returncode}): {proc.stderr[-500:]}")
    return dict(json.loads(proc.stdout))


def summarise(raw: dict[str, Any], expect_status: int) -> dict[str, Any]:
    pct = raw.get("latencyPercentiles", {})
    summary = raw.get("summary", {})
    codes = {str(k): v for k, v in (raw.get("statusCodeDistribution") or {}).items()}
    total = sum(codes.values()) + sum((raw.get("errorDistribution") or {}).values())
    ok = codes.get(str(expect_status), 0)
    ms = {k: (v * 1000 if v is not None else None) for k, v in pct.items()}
    return {
        "mean_ms": (summary.get("average") or 0) * 1000,
        "p50_ms": ms.get("p50"),
        "p90_ms": ms.get("p90"),
        "p99_ms": ms.get("p99"),
        "p999_ms": ms.get("p99.9"),
        "max_ms": (summary.get("slowest") or 0) * 1000,
        "rps": summary.get("requestsPerSec"),
        "ok_ratio": (ok / total) if total else 0.0,
        "status_codes": codes,
        "errors": raw.get("errorDistribution") or {},
    }


def delta(broker: dict[str, Any], direct: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key in ("mean_ms", "p50_ms", "p90_ms", "p99_ms"):
        b, d = broker.get(key), direct.get(key)
        out[key] = (b - d) if b is not None and d is not None else None
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Broker overhead A/B bench (oha, in-network)")
    parser.add_argument("--rates", default="1,10,50", help="comma-separated rps set")
    parser.add_argument("--duration", type=float, default=20.0, help="seconds per run")
    parser.add_argument("--min-requests", type=int, default=50)
    parser.add_argument("--quick", action="store_true", help="10 rps, 10 s runs")
    parser.add_argument("--only", default="", help="comma-separated case names")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    env = PerfEnv.load()
    all_cases = cases(env)
    if args.list:
        for c in all_cases:
            print(f"{c.name:<32} variant={c.variant:<11} vendor={c.vendor} caller={c.caller}")
        return 0
    if args.quick:
        args.rates, args.duration = "10", 10.0
    rates = tuple(int(r) for r in args.rates.split(",") if r)
    wanted = {s for s in args.only.split(",") if s}
    selected = [c for c in all_cases if not wanted or c.name in wanted]
    selected.sort(key=lambda c: list(VARIANTS).index(c.variant))
    ensure_bodies()

    rows: list[dict[str, Any]] = []
    direct_cache: dict[tuple[str, str, str, str | None, str | None, int], dict[str, Any]] = {}
    current: str | None = None
    start_oha()
    try:
        for case in selected:
            previous = current
            current = apply_variant(case.variant, current)
            # A freshly recreated broker has cold pools/caches/JIT-ish import
            # paths: warm it harder than between cases on a warm broker.
            warm_n = 200 if current != previous else 20
            case_rates = case.rates or rates
            for rate in sorted({min(r, 10) for r in case_rates}) if args.quick else case_rates:
                row = run_case(env, case, rate, args, warm_n, direct_cache)
                rows.append(row)
                warm_n = 20
    finally:
        stop_oha()
    apply_variant("default", current)
    path = write_result("overhead", {**run_metadata("overhead"), "rows": rows})
    print(f"wrote {path}")
    return 0


def run_case(
    env: PerfEnv,
    case: Case,
    rate: int,
    args: argparse.Namespace,
    warm_n: int,
    direct_cache: dict[tuple[str, str, str, str | None, str | None, int], dict[str, Any]],
) -> dict[str, Any]:
    vendor = env.vendors[case.vendor]
    alias_base = upstream_base(vendor["alias"])
    token = env.caller(case.caller).token
    broker_url = f"{env.broker_url}/{alias_base}{case.path}"
    n = max(args.min_requests, int(rate * args.duration))
    if case.max_requests is not None:
        n = min(n, case.max_requests)
        warm_n = min(warm_n, 20)
    oha(broker_url, case, max(20, min(rate, 50)), warm_n, token)
    broker = summarise(oha(broker_url, case, rate, n, token), case.expect_status)
    direct: dict[str, Any] | None = None
    if not case.no_direct:
        key = (vendor["alias"], case.method, case.path, case.body, case.body_file, rate)
        if key not in direct_cache:
            plain = Case(
                case.name,
                method=case.method,
                path=case.path,
                body=case.body,
                body_file=case.body_file,
            )
            oha(f"{alias_base}{case.path}", plain, max(20, min(rate, 50)), 20, None)
            direct_cache[key] = summarise(
                oha(f"{alias_base}{case.path}", plain, rate, n, None), 200
            )
        direct = direct_cache[key]
    row: dict[str, Any] = {
        "case": case.name,
        "variant": case.variant,
        "vendor": case.vendor,
        "credential_type": vendor["credential_type"],
        "caller": case.caller,
        "method": case.method,
        "rate": rate,
        "requests": n,
        "broker": broker,
        "direct": direct,
        "overhead": delta(broker, direct) if direct else None,
    }
    ov = row["overhead"] or {}
    print(
        f"  {case.name:<32} {rate:>4} rps  broker p50={broker['p50_ms']:.2f}ms "
        f"p99={broker['p99_ms']:.2f}ms ok={broker['ok_ratio']:.3f}  "
        + (
            f"overhead mean={ov.get('mean_ms', 0):.2f}ms p50={ov.get('p50_ms', 0):.2f}ms"
            if direct
            else "(no direct baseline)"
        ),
        flush=True,
    )
    return row


if __name__ == "__main__":
    sys.exit(main())
