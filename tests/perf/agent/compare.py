"""``make perf-agent-compare BASE=<artifact> [HEAD=<artifact>]``: did a change help?

Diffs the key metrics of two runs of the same kind and labels each
``improved`` / ``regressed`` / ``unchanged``. Exits 3 if anything regressed
beyond its noise threshold, so an agent can gate its own fix on it.

Understands agent artifacts (``results/agent/*-{sql,stages,cpu}.json``) and the
classic results (``results/*-{overhead,capacity,dbcost,profile}.json``).
``HEAD`` defaults to the newest artifact of the same kind as ``BASE``.

Thresholds: counts (statements, sessions, …) are exact (any increase
regresses); latency / CPU / rps use a relative tolerance (``--tolerance``,
default 10 %) plus a small absolute floor so sub-ms jitter never trips it.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.perf.agent.base import AGENT_DIR, EXIT_REGRESSION, Report, md_table
from tests.perf.common import REPO_ROOT, RESULTS_DIR


@dataclass
class Metric:
    key: str
    base: float
    head: float
    #: "lower" = smaller is better, "higher" = bigger is better.
    better: str = "lower"
    #: exact = any worsening regresses (counts).
    exact: bool = False
    floor: float = 0.0

    def verdict(self, tolerance: float) -> str:
        delta = self.head - self.base
        worse = delta > 0 if self.better == "lower" else delta < 0
        if self.exact:
            if abs(delta) < 1e-9:
                return "unchanged"
            return "regressed" if worse else "improved"
        limit = max(abs(self.base) * tolerance, self.floor)
        if abs(delta) <= limit:
            return "unchanged"
        return "regressed" if worse else "improved"


def kind_of(path: Path, payload: dict[str, Any]) -> str:
    return str(payload.get("cmd") or payload.get("scenario") or path.stem.split("-", 1)[-1])


def load(path: Path) -> tuple[str, dict[str, Any]]:
    payload = json.loads(path.read_text())
    return kind_of(path, payload), payload


def newest(kind: str, exclude: Path) -> Path:
    candidates = sorted(AGENT_DIR.glob(f"*-{kind}.json")) + sorted(
        RESULTS_DIR.glob(f"*-{kind}.json")
    )
    candidates = [p for p in candidates if p.resolve() != exclude.resolve()]
    if not candidates:
        raise SystemExit(f"no other {kind} artifact to compare against; run the probe again")
    return max(candidates, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------- extractors


def m_sql(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    out: list[Metric] = []
    bt, ht = b["data"]["totals_per_req"], h["data"]["totals_per_req"]
    for k in bt:
        out.append(Metric(f"total.{k}/req", bt[k], ht.get(k, 0), exact=True))
    bp = {p["plane"]: p for p in b["data"]["planes"]}
    hp = {p["plane"]: p for p in h["data"]["planes"]}
    for plane in sorted(set(bp) | set(hp)):
        for k in ("sessions", "transactions", "statements", "writes", "zero_row_selects"):
            out.append(
                Metric(
                    f"{plane}.{k}/req",
                    bp.get(plane, {}).get(k, 0),
                    hp.get(plane, {}).get(k, 0),
                    exact=True,
                )
            )
    return out


def m_stages(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    out = [
        Metric(
            "handler mean ms", b["data"]["handler_mean_ms"], h["data"]["handler_mean_ms"], floor=0.5
        ),
        Metric(
            "load rps", b["data"]["load"]["rps"], h["data"]["load"]["rps"], better="higher", floor=5
        ),
        Metric(
            "loop lag p99 ms",
            b["data"]["loop_lag"]["p99_ms"],
            h["data"]["loop_lag"]["p99_ms"],
            floor=2.0,
        ),
    ]
    bs = {s["stage"]: s for s in b["data"]["stages"]}
    hs = {s["stage"]: s for s in h["data"]["stages"]}
    for name in sorted(set(bs) & set(hs)):
        out.append(Metric(f"{name} mean ms", bs[name]["mean_ms"], hs[name]["mean_ms"], floor=0.3))
        out.append(
            Metric(
                f"{name} calls/req",
                bs[name]["calls_per_req"],
                hs[name]["calls_per_req"],
                exact=True,
            )
        )
    return out


def _buckets(p: dict[str, Any]) -> dict[str, float]:
    prof = p.get("profile") or p.get("data", {}).get("profile", {})
    return {k: 100 * float(v) for k, v in prof.get("buckets", {}).items()}


def m_cpu(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    bb, hb = _buckets(b), _buckets(h)
    out = [
        Metric(f"cpu% {k}", bb.get(k, 0), hb.get(k, 0), floor=1.0)
        for k in sorted(set(bb) | set(hb))
    ]
    bl = (b.get("load") or b.get("data", {}).get("load", {})).get("rps")
    hl = (h.get("load") or h.get("data", {}).get("load", {})).get("rps")
    if bl and hl:
        out.append(Metric("load rps (saturated)", bl, hl, better="higher", floor=5))
    return out


def m_overhead(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    def idx(p: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {f"{r['case']}@{r['rate']}": r for r in p["rows"] if r.get("overhead")}

    bi, hi = idx(b), idx(h)
    return [
        Metric(
            f"{k} overhead mean ms",
            bi[k]["overhead"]["mean_ms"],
            hi[k]["overhead"]["mean_ms"],
            floor=1.0,
        )
        for k in sorted(set(bi) & set(hi))
    ]


def m_capacity(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    def idx(p: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {f"{r['config']} c={r['concurrency']}": r for r in p["rows"]}

    bi, hi = idx(b), idx(h)
    out: list[Metric] = []
    for k in sorted(set(bi) & set(hi)):
        out.append(Metric(f"{k} rps", bi[k]["rps"], hi[k]["rps"], better="higher", floor=5))
        out.append(Metric(f"{k} p99 ms", bi[k]["p99_ms"], hi[k]["p99_ms"], floor=5))
    return out


def m_dbcost(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    def idx(p: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {f"{r['variant']} {r['caller']}": r["total_per_call"] for r in p["rows"]}

    bi, hi = idx(b), idx(h)
    out: list[Metric] = []
    for k in sorted(set(bi) & set(hi)):
        for m in ("statements", "transactions", "writes"):
            # dbcost divides pg_stat_statements deltas: allow 0.1/call noise.
            out.append(Metric(f"{k} {m}/call", bi[k][m], hi[k][m], floor=0.1))
    return out


EXTRACTORS = {
    "sql": m_sql,
    "stages": m_stages,
    "cpu": m_cpu,
    "profile": m_cpu,
    "overhead": m_overhead,
    "capacity": m_capacity,
    "dbcost": m_dbcost,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, default=None)
    parser.add_argument("--tolerance", type=float, default=0.10)
    args = parser.parse_args(argv)

    base_path = args.base if args.base.is_absolute() else REPO_ROOT / args.base
    kind, base = load(base_path)
    head_path = args.head or newest(kind, base_path)
    head_path = head_path if head_path.is_absolute() else REPO_ROOT / head_path
    head_kind, head = load(head_path)
    if head_kind != kind and {head_kind, kind} != {"cpu", "profile"}:
        raise SystemExit(f"kind mismatch: BASE is {kind}, HEAD is {head_kind}")
    extractor = EXTRACTORS.get(kind)
    if extractor is None:
        raise SystemExit(f"don't know how to compare {kind} artifacts")
    metrics = extractor(base, head)
    rows = []
    counts = {"improved": 0, "regressed": 0, "unchanged": 0}
    for m in metrics:
        v = m.verdict(args.tolerance)
        counts[v] += 1
        delta = m.head - m.base
        rel = f"{100 * delta / m.base:+.1f}%" if m.base else "n/a"
        rows.append([v.upper() if v != "unchanged" else v, m.key, m.base, m.head, delta, rel])
    order = {"REGRESSED": 0, "IMPROVED": 1, "unchanged": 2}
    rows.sort(key=lambda r: (order[str(r[0])], str(r[1])))

    rep = Report("compare", f"Compare {kind}: {base_path.name} -> {head_path.name}")
    rep.data = {
        "kind": kind,
        "base": str(base_path.relative_to(REPO_ROOT)),
        "head": str(head_path.relative_to(REPO_ROOT)),
        "tolerance": args.tolerance,
        "counts": counts,
        "metrics": [
            {"verdict": r[0], "metric": r[1], "base": r[2], "head": r[3], "delta": r[4]}
            for r in rows
        ],
    }
    rep.highlights.append(
        f"{counts['regressed']} regressed, {counts['improved']} improved, "
        f"{counts['unchanged']} unchanged (tolerance {args.tolerance:.0%}; counts exact)"
    )
    for r in rows:
        if r[0] in ("REGRESSED", "IMPROVED"):
            rep.highlights.append(f"{r[0]}: {r[1]} {r[2]:.3g} -> {r[3]:.3g} ({r[5]})")
    rep.section(
        "Metrics", md_table(["verdict", "metric", "base", "head", "delta", "rel"], rows, digits=3)
    )
    # make rewrites a failing recipe's status to 2, so also say it in-band.
    rep.result = f"{'REGRESSED' if counts['regressed'] else 'OK'} (exit 3 = regressed)"
    rep.finish()
    return EXIT_REGRESSION if counts["regressed"] else 0
