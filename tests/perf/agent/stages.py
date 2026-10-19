"""``make perf-agent-stages``: where does the broker's time go, per stage?

Runs closed-loop oha load (``-c C`` for ``D`` seconds) through a broker traced
with ``PERF_TRACE=stages`` and reports, per wrapped function: calls per
request, p50 / p99 / mean wall ms, and mean share of ``_handle`` (the request
handler). Stages nest (``authorize_execution`` contains
``CredentialResolver.resolve``), so shares don't sum to 100 %. Context-manager
stages split into ``.enter`` (e.g. upstream header wait) and ``.body``
(streaming the body after the handler returned).

Also reports event-loop lag (a 10 ms sleeper's overshoot): high p99 lag means
something blocks the loop — correlate with ``make perf-agent-cpu``.

Every request must return 200 (otherwise exit 2), and the hook must have
patched ``_handle``; targets it could not patch are listed under "Hook targets".

Drill down with ``FUNCS=module:Qual.name,...`` (module relative to
``jentic_one``), e.g. ``FUNCS=broker.services.credentials.orchestrator:inject``.
"""

from __future__ import annotations

import argparse
import os
import statistics
from collections import defaultdict
from typing import Any

from tests.perf.agent.base import (
    Report,
    TraceTail,
    broker_variant,
    env_or_fail,
    hook_manifest,
    hook_stats,
    internal_url,
    md_table,
    pct,
    require_fresh,
    require_ok,
    require_patched,
    wait_quiet,
)
from tests.perf.bench_overhead import Case, oha_args, run_oha, start_oha, stop_oha, summarise

HANDLER = "broker.web.routers.execute:_handle"


def analyse(events: list[dict[str, Any]]) -> dict[str, Any]:
    per_req: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    lag: list[float] = []
    for e in events:
        if e.get("ev") == "stage" and e.get("rid"):
            per_req[e["rid"]][e["name"]].append(float(e["ms"]))
        elif e.get("ev") == "lag":
            lag += [float(x) for x in e.get("samples_ms", [])]
    reqs = {rid: st for rid, st in per_req.items() if HANDLER in st}
    n = max(1, len(reqs))
    handler_mean = statistics.fmean([sum(st[HANDLER]) for st in reqs.values()] or [0.0])
    names = sorted({name for st in reqs.values() for name in st})
    stages: list[dict[str, Any]] = []
    for name in names:
        totals = [sum(st[name]) for st in reqs.values() if name in st]
        calls = sum(len(st[name]) for st in reqs.values() if name in st)
        mean = statistics.fmean(totals) if totals else 0.0
        stages.append(
            {
                "stage": name,
                "calls_per_req": round(calls / n, 2),
                "p50_ms": round(pct(totals, 0.5), 3),
                "p99_ms": round(pct(totals, 0.99), 3),
                "mean_ms": round(mean, 3),
                "share_of_handler_pct": round(100 * mean / handler_mean, 1) if handler_mean else 0,
            }
        )
    stages.sort(key=lambda s: -float(s["mean_ms"]))
    return {
        "requests": len(reqs),
        "handler_mean_ms": round(handler_mean, 3),
        "stages": stages,
        "loop_lag": {
            "samples": len(lag),
            "p50_ms": round(pct(lag, 0.5), 2),
            "p99_ms": round(pct(lag, 0.99), 2),
            "max_ms": round(max(lag, default=0.0), 2),
            "over_5ms_pct": round(100 * sum(1 for x in lag if x > 5) / len(lag), 2) if lag else 0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--caller", choices=("jak", "at", "jwt"), default="jak")
    parser.add_argument("--vendor", default="perf-noauth")
    parser.add_argument("--funcs", default=os.environ.get("FUNCS", ""))
    args = parser.parse_args(argv)

    env = env_or_fail()
    # Warm-up + the timed window, plus the traced-broker recreate and restore.
    require_fresh(env, callers=[args.caller], run_s=args.duration + 180)
    url = internal_url(env, args.vendor)
    token = env.caller(args.caller).token
    overrides = {"PERF_TRACE_FUNCS": args.funcs} if args.funcs else {}
    start_oha()
    try:
        with broker_variant(overrides, trace="stages"):
            run_oha(oha_args(url, Case("warm"), token, n=50, conns=4, rate=None))
            wait_quiet()
            # After warm-up so lazily imported targets have loaded.
            manifest = hook_manifest()
            require_patched(manifest, (HANDLER,))
            tail = TraceTail()
            raw = run_oha(
                oha_args(
                    url,
                    Case("stages"),
                    token,
                    n=None,
                    conns=args.concurrency,
                    rate=None,
                    duration_s=args.duration,
                )
            )
            wait_quiet(2.0)
            events = tail.read()
            result = analyse(events)
    finally:
        stop_oha()
    load = summarise(raw, 200)
    require_ok(load, "stages load")

    rep = Report(
        "stages",
        f"Broker stage latency — c={args.concurrency}, {args.duration:g}s, {args.caller}, "
        f"{args.vendor}",
        mode="traced (PERF_TRACE=stages)",
        variant=overrides,
    )
    stats = hook_stats(events)
    rep.data = {"args": vars(args), "load": load, "hook": {**manifest, **stats}, **result}
    lag = result["loop_lag"]
    rep.highlights.append(
        f"{load.get('rps', 0):.0f} rps, client p50 {load.get('p50_ms', 0):.1f} ms; "
        f"handler mean {result['handler_mean_ms']} ms over {result['requests']} requests"
    )
    for s in [s for s in result["stages"] if s["stage"] != HANDLER][:4]:
        rep.highlights.append(
            f"`{s['stage']}`: mean {s['mean_ms']} ms (p99 {s['p99_ms']}), "
            f"{s['share_of_handler_pct']}% of handler, {s['calls_per_req']}x/req"
        )
    rep.highlights.append(
        f"Event-loop lag p99 {lag['p99_ms']} ms, max {lag['max_ms']} ms, "
        f"{lag['over_5ms_pct']}% of 10 ms ticks late by >5 ms"
    )
    if manifest["missing"]:
        rep.highlights.append(
            f"WARNING: {len(manifest['missing'])} stage target(s) not patched "
            "(renamed, or module never imported); see Hook targets"
        )
    if stats["errors"] or stats["dropped"]:
        rep.highlights.append(
            f"WARNING: the hook swallowed {stats['errors']} errors and dropped "
            f"{stats['dropped']} events; stage counts may be low"
        )
    rep.section(
        "Stages",
        md_table(
            ["stage", "calls/req", "p50 ms", "p99 ms", "mean ms", "% of handler"],
            [
                [
                    s["stage"],
                    s["calls_per_req"],
                    s["p50_ms"],
                    s["p99_ms"],
                    s["mean_ms"],
                    s["share_of_handler_pct"],
                ]
                for s in result["stages"]
            ],
            digits=3,
        ),
        note="wall time under load, traced broker; stages nest; `.body` runs after the "
        "handler returns (streaming), so it is not part of the handler share",
    )
    rep.section(
        "Hook targets",
        md_table(
            ["target", "patched"],
            [[t, "yes" if t in manifest["patched"] else "NO"] for t in manifest["targets"]],
        ),
        note="NO = renamed or never imported: that stage has no row above",
    )
    rep.section(
        "Event-loop lag",
        md_table(
            ["samples", "p50 ms", "p99 ms", "max ms", "% > 5 ms"],
            [
                [
                    lag["samples"],
                    lag["p50_ms"],
                    lag["p99_ms"],
                    lag["max_ms"],
                    lag["over_5ms_pct"],
                ]
            ],
        ),
    )
    rep.next_steps += [
        "make perf-agent-stages FUNCS=broker.services.credentials.orchestrator:inject",
        "make perf-agent-cpu   # which lines burn the CPU behind a slow stage",
        "make perf-agent-compare BASE=<this ARTIFACT>",
    ]
    rep.finish()
    return 0
