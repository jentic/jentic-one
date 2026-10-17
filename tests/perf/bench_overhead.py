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

The A/B is interleaved: every (case, rate) measures direct immediately before
the broker run, so host drift (Docker Desktop, thermal) lands in both sides
instead of in the overhead. ``--repeat K`` alternates direct/broker K times and
records the spread (min/median/max) of the mean overhead. K must be odd, so the
row's headline numbers are one real repeat: the one whose mean overhead is the
median (an even K would have no single middle repeat).

A case whose broker or direct run has fewer than ``--min-ok`` expected-status
responses is written with ``"valid": false`` (and left out of the report): a
401/429/5xx returns in a few ms without reaching the upstream, so a failing run
would otherwise read as *lower* overhead. Any invalid case makes the exit code 1.

Usage::

    uv run python -m tests.perf.bench_overhead                 # full matrix
    uv run python -m tests.perf.bench_overhead --quick         # 10 rps only, short
    uv run python -m tests.perf.bench_overhead --only noauth-post-small,bearer-post-small
    uv run python -m tests.perf.bench_overhead --repeat 3      # spread per case
    uv run python -m tests.perf.bench_overhead --list
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tests.perf.common import (
    DEFAULT_MIN_OK,
    ECHO_PATH,
    GET_PATH,
    RESULTS_DIR,
    PerfEnv,
    compose,
    exclusive_run,
    restore_on_exit,
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
    # Long TTLs so a measurement window never straddles a cache expiry (dbcost).
    "warm-cache": {"PERF_RESOLVE_TTL": "300", "PERF_TOOLKIT_TTL": "300", "PERF_RULE_TTL": "300"},
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


#: Per-process name, so one tool's cleanup can never remove another's sidecar.
OHA_CONTAINER = f"jentic-perf-oha-{os.getpid()}"


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


def oha_args(
    url: str,
    case: Case,
    token: str | None,
    *,
    n: int | None,
    conns: int,
    rate: int | None,
    duration_s: float | None = None,
) -> list[str]:
    """Build a ``docker exec <oha>`` argv.

    ``rate`` set => open loop (``-q`` + ``--latency-correction``); ``rate`` None
    => closed loop (as fast as ``conns`` connections allow). ``duration_s`` set
    => run for that long (``-z``, waiting for in-flight requests) instead of ``-n``.
    """
    args = ["docker", "exec", OHA_CONTAINER, "oha", "-c", str(conns)]
    if duration_s is not None:
        args += ["-z", f"{duration_s:g}s", "-w"]
    else:
        args += ["-n", str(n)]
    if rate is not None:
        args += ["-q", str(rate), "--latency-correction"]
    args += ["--no-tui", "--output-format", "json", "-m", case.method, "-t", "35s"]
    if case.body is not None:
        args += ["-d", case.body, "-T", "application/json"]
    elif case.body_file is not None:
        args += ["-D", f"/results/bodies/{case.body_file}.json", "-T", "application/json"]
    if token:
        args += ["-H", f"Authorization: Bearer {token}"]
    for key, value in case.headers.items():
        args += ["-H", f"{key}: {value}"]
    args.append(url)
    return args


def run_oha(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"oha failed ({proc.returncode}): {proc.stderr[-500:]}")
    return dict(json.loads(proc.stdout))


def oha(
    url: str, case: Case, rate: int, n: int, token: str | None, *, conns: int | None = None
) -> dict[str, Any]:
    """Open-loop run at ``rate`` rps for ``n`` requests (``conns`` scales with rate by default)."""
    if conns is None:
        conns = max(4, min(64, rate // 2))
    return run_oha(oha_args(url, case, token, n=n, conns=conns, rate=rate))


class OhaOutputError(RuntimeError):
    """oha's JSON lacks a field the summary needs (a schema change or a broken run)."""


def _ms(seconds: float | None) -> float | None:
    return seconds * 1000 if seconds is not None else None


def summarise(raw: dict[str, Any], expect_status: int) -> dict[str, Any]:
    """Latency (ms), throughput and the expected-status share of one oha run.

    Raises ``OhaOutputError`` rather than defaulting a missing field to 0: a
    zero would be indistinguishable from a real (very fast) measurement.
    """
    pct, summary = raw.get("latencyPercentiles"), raw.get("summary")
    if not isinstance(pct, dict) or not isinstance(summary, dict):
        raise OhaOutputError(f"oha JSON lacks summary/latencyPercentiles: keys {sorted(raw)}")
    missing = [k for k in ("average", "slowest", "requestsPerSec") if k not in summary]
    if missing:
        raise OhaOutputError(f"oha summary lacks {missing}: keys {sorted(summary)}")
    codes = {str(k): v for k, v in (raw.get("statusCodeDistribution") or {}).items()}
    errors = raw.get("errorDistribution") or {}
    total = sum(codes.values()) + sum(errors.values())
    ok = codes.get(str(expect_status), 0)
    return {
        "mean_ms": _ms(summary["average"]),
        "p50_ms": _ms(pct.get("p50")),
        "p90_ms": _ms(pct.get("p90")),
        "p99_ms": _ms(pct.get("p99")),
        "p999_ms": _ms(pct.get("p99.9")),
        "max_ms": _ms(summary["slowest"]),
        "rps": summary["requestsPerSec"],
        "ok_ratio": (ok / total) if total else 0.0,
        "status_codes": codes,
        "errors": errors,
    }


def check_ok(summary: dict[str, Any], min_ok: float, label: str) -> str | None:
    """Why ``summary`` is not a valid measurement, or ``None`` if it is."""
    if summary["ok_ratio"] >= min_ok:
        return None
    return (
        f"{label}: ok_ratio {summary['ok_ratio']:.3f} < {min_ok} "
        f"(status {summary['status_codes']}, errors {summary['errors']})"
    )


def delta(broker: dict[str, Any], direct: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key in ("mean_ms", "p50_ms", "p90_ms", "p99_ms"):
        b, d = broker.get(key), direct.get(key)
        out[key] = (b - d) if b is not None and d is not None else None
    return out


#: Broker state while a recreate is in flight: restored on exit like any variant.
UNKNOWN_VARIANT = "?"


def switch_variant(state: dict[str, str | None], name: str) -> bool:
    """Recreate the broker on variant ``name`` unless it is already on it.

    ``state["variant"]`` is the broker's variant (``None`` = never touched). It
    reads ``UNKNOWN_VARIANT`` until the recreate succeeds, so an interrupted
    recreate is still put back by ``restore_default``. Returns True on recreate.
    """
    if state["variant"] == name:
        return False
    state["variant"] = UNKNOWN_VARIANT
    state["variant"] = apply_variant(name, None)
    return True


def restore_default(state: dict[str, str | None]) -> None:
    """Put the broker back on the default variant unless it never left it."""
    if state["variant"] not in (None, "default"):
        state["variant"] = UNKNOWN_VARIANT
        state["variant"] = apply_variant("default", None)


def fmt_num(value: float | None, spec: str = ".2f") -> str:
    return "n/a" if value is None else format(value, spec)


def case_rates(case: Case, rates: tuple[int, ...], quick: bool) -> list[int]:
    chosen = case.rates or rates
    return sorted({min(r, 10) for r in chosen}) if quick else list(chosen)


def requests_for(case: Case, rate: int, args: argparse.Namespace) -> int:
    n = max(int(args.min_requests), int(rate * args.duration))
    return min(n, case.max_requests) if case.max_requests is not None else n


def estimate_s(plan: list[tuple[Case, int]], args: argparse.Namespace) -> float:
    """Rough run length: timed windows plus ~1 min per broker recreate."""
    repeat = int(args.repeat)
    timed = sum(
        repeat * (1 if case.no_direct else 2) * requests_for(case, rate, args) / rate
        for case, rate in plan
    )
    return timed + 60.0 * len({case.variant for case, _ in plan})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Broker overhead A/B bench (oha, in-network)")
    parser.add_argument("--rates", default="1,10,50", help="comma-separated rps set")
    parser.add_argument("--duration", type=float, default=20.0, help="seconds per run")
    parser.add_argument("--min-requests", type=int, default=50)
    parser.add_argument("--quick", action="store_true", help="10 rps, 10 s runs")
    parser.add_argument("--only", default="", help="comma-separated case names")
    parser.add_argument("--repeat", type=int, default=1, help="direct/broker pairs per case (odd)")
    parser.add_argument(
        "--min-ok",
        type=float,
        default=DEFAULT_MIN_OK,
        help="minimum expected-status share for a run to count (default %(default)s)",
    )
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.repeat % 2 == 0:
        parser.error("--repeat must be an odd number >= 1 (the headline is the median repeat)")

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
    plan = [(case, rate) for case in selected for rate in case_rates(case, rates, args.quick)]
    env.check_fresh(callers={case.caller for case, _ in plan}, run_s=estimate_s(plan, args))
    ensure_bodies()

    rows: list[dict[str, Any]] = []
    state: dict[str, str | None] = {"variant": None}
    with (
        exclusive_run("bench_overhead"),
        restore_on_exit(
            ("remove oha sidecar", stop_oha),
            ("broker back to default variant", lambda: restore_default(state)),
        ),
    ):
        start_oha()
        for case, rate in plan:
            # A freshly recreated broker has cold pools/caches/import paths:
            # warm it harder than between runs on a warm broker.
            warm_n = 200 if switch_variant(state, case.variant) else 20
            rows.append(run_case(env, case, rate, args, warm_n))
        path = write_result("overhead", {**run_metadata("overhead"), "rows": rows})
        print(f"wrote {path}")
    invalid = [row for row in rows if not row["valid"]]
    for row in invalid:
        print(f"INVALID {row['case']} @{row['rate']}: {'; '.join(row['invalid_reasons'])}")
    return 1 if invalid else 0


def median_sample(
    samples: list[dict[str, Any]], rank: Callable[[dict[str, Any]], float]
) -> dict[str, Any]:
    """The repeat whose ``rank`` is the median; needs an odd number of repeats.

    An even count has no single middle repeat: either middle one would bias
    the headline (the lower one is the minimum for two), and averaging two
    repeats' percentiles is not a percentile.
    """
    if len(samples) % 2 == 0:
        raise ValueError(f"need an odd number of repeats for a median repeat, got {len(samples)}")
    return sorted(samples, key=rank)[len(samples) // 2]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def run_case(
    env: PerfEnv, case: Case, rate: int, args: argparse.Namespace, warm_n: int
) -> dict[str, Any]:
    vendor = env.vendors[case.vendor]
    alias_base = upstream_base(vendor["alias"])
    token = env.caller(case.caller).token
    broker_url = f"{env.broker_url}/{alias_base}{case.path}"
    direct_url = f"{alias_base}{case.path}"
    n = requests_for(case, rate, args)
    if case.max_requests is not None:
        warm_n = min(warm_n, 20)
    plain = (
        None
        if case.no_direct
        else Case(
            case.name, method=case.method, path=case.path, body=case.body, body_file=case.body_file
        )
    )
    warm_rate = max(20, min(rate, 50))
    oha(broker_url, case, warm_rate, warm_n, token)
    if plain is not None:
        oha(direct_url, plain, warm_rate, 20, None)

    samples: list[dict[str, Any]] = []
    problems: list[str] = []
    for i in range(args.repeat):
        sample: dict[str, Any] = {}
        if plain is not None:
            # Direct right before broker, never shared across cases: host drift
            # between the two runs is bounded by one run's length.
            sample["direct_measured_at"] = _now()
            sample["direct"] = summarise(oha(direct_url, plain, rate, n, None), 200)
        sample["broker_measured_at"] = _now()
        sample["broker"] = summarise(oha(broker_url, case, rate, n, token), case.expect_status)
        direct = sample.get("direct")
        sample["overhead"] = delta(sample["broker"], direct) if direct else None
        for side in ("direct", "broker"):
            if sample.get(side) and (why := check_ok(sample[side], args.min_ok, f"{side}#{i}")):
                problems.append(why)
        samples.append(sample)

    def rank(s: dict[str, Any]) -> float:
        value = (s["overhead"] or {}).get("mean_ms") if plain else s["broker"]["mean_ms"]
        return float("inf") if value is None else float(value)

    rep = median_sample(samples, rank)
    means = [m for s in samples if (m := (s["overhead"] or {}).get("mean_ms")) is not None]
    spread = (
        {"min": min(means), "median": statistics.median(means), "max": max(means)}
        if means
        else None
    )
    row: dict[str, Any] = {
        "case": case.name,
        "variant": case.variant,
        "vendor": case.vendor,
        "credential_type": vendor["credential_type"],
        "caller": case.caller,
        "method": case.method,
        "rate": rate,
        "requests": n,
        "repeats": len(samples),
        "valid": not problems,
        "invalid_reasons": problems,
        "broker": rep["broker"],
        "direct": rep.get("direct"),
        "overhead": rep["overhead"],
        "direct_measured_at": rep.get("direct_measured_at"),
        "broker_measured_at": rep["broker_measured_at"],
        "overhead_mean_spread": spread,
    }
    if len(samples) > 1:
        row["samples"] = samples
    broker, ov = rep["broker"], rep["overhead"] or {}
    spread_s = (
        f" spread {fmt_num(spread['min'])}..{fmt_num(spread['max'])}ms"
        if spread and len(means) > 1
        else ""
    )
    print(
        f"  {case.name:<32} {rate:>4} rps  broker p50={fmt_num(broker['p50_ms'])}ms "
        f"p99={fmt_num(broker['p99_ms'])}ms ok={broker['ok_ratio']:.3f}  "
        + (
            f"overhead mean={fmt_num(ov.get('mean_ms'))}ms "
            f"p50={fmt_num(ov.get('p50_ms'))}ms{spread_s}"
            if plain
            else "(no direct baseline)"
        )
        + ("" if row["valid"] else "  INVALID"),
        flush=True,
    )
    return row


if __name__ == "__main__":
    sys.exit(main())
