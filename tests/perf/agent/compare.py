"""``make perf-agent-compare BASE=<artifact> [HEAD=<artifact>]``: did a change help?

Diffs the key metrics of two runs of the same kind and labels each
``improved`` / ``regressed`` / ``unchanged`` (or ``info`` for metrics too noisy
to gate on). Exits 3 if a gated metric regressed beyond its threshold, so an
agent can gate its own fix on it, and 2 if the two runs are not comparable.

Understands agent artifacts (``results/agent/*-{sql,stages,cpu}.json``) and the
classic results (``results/*-{overhead,capacity,dbcost,profile}.json``).
``HEAD`` defaults to the newest artifact of the same kind written after ``BASE``.

Comparability: both runs must have succeeded (every request answered 200) and
share their arguments, ``PERF_*`` variant and broker mode. Otherwise compare
refuses with exit 2 and lists the differences; ``--force`` compares anyway.

Thresholds:

* sequential counts (``sql`` statements, sessions, …) are exact — any increase
  regresses;
* averaged counts get an absolute tolerance: ``dbcost`` per-call counts
  ``0.1`` (``pg_stat_statements`` delta rounding; ``+1``/call still regresses)
  and ``stages`` calls/request ``0.05`` (a closed-loop run ends mid-flight and
  caches refill on their TTL);
* means / rps use a relative tolerance (``--tolerance``, default 10 %) plus a
  small absolute floor so sub-ms jitter never trips them;
* tail latencies (p99, event-loop lag p99) come from one run each and move more
  than 10 % between identical runs, so they are reported as ``info`` only.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.perf.agent.base import (
    AGENT_DIR,
    EXIT_REGRESSION,
    PrereqError,
    Report,
    md_table,
)
from tests.perf.common import REPO_ROOT, RESULTS_DIR

VERDICTS = ("regressed", "improved", "unchanged", "info")


@dataclass
class Metric:
    key: str
    base: float
    head: float
    #: "lower" = smaller is better, "higher" = bigger is better.
    better: str = "lower"
    #: exact = any worsening regresses (counts).
    exact: bool = False
    #: Absolute tolerance; when set it replaces the relative tolerance + floor.
    abs_tol: float | None = None
    floor: float = 0.0
    #: False = informational only (reported, never regresses).
    gate: bool = True

    def verdict(self, tolerance: float) -> str:
        if not self.gate:
            return "info"
        delta = self.head - self.base
        worse = delta > 0 if self.better == "lower" else delta < 0
        if self.exact:
            limit = 1e-9
        elif self.abs_tol is not None:
            limit = self.abs_tol
        else:
            limit = max(abs(self.base) * tolerance, self.floor)
        if abs(delta) <= limit:
            return "unchanged"
        return "regressed" if worse else "improved"


def kind_of(path: Path, payload: dict[str, Any]) -> str:
    return str(payload.get("cmd") or payload.get("scenario") or path.stem.split("-", 1)[-1])


def load(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PrereqError(f"cannot read artifact {path}: {exc}", "pass an ARTIFACT path") from exc
    if not isinstance(payload, dict):
        raise PrereqError(f"{path} is not a perf artifact", "pass an ARTIFACT path")
    return kind_of(path, payload), payload


def written_at(path: Path, payload: dict[str, Any]) -> float:
    """When a run was written: its recorded timestamp, else the file mtime."""
    stamp = payload.get("timestamp")
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp).timestamp()
        except ValueError:
            pass
    return path.stat().st_mtime


def newest(kind: str, base_path: Path, base: dict[str, Any]) -> Path:
    """The newest artifact of ``kind`` written after ``BASE`` (keeps base -> head forward)."""
    after = written_at(base_path, base)
    candidates = []
    for p in [*AGENT_DIR.glob(f"*-{kind}.json"), *RESULTS_DIR.glob(f"*-{kind}.json")]:
        if p.resolve() == base_path.resolve():
            continue
        try:
            when = written_at(p, json.loads(p.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
        if when > after:
            candidates.append((when, p))
    if not candidates:
        raise PrereqError(
            f"no {kind} artifact newer than {base_path.name}",
            f"run the {kind} probe again after your change, or pass HEAD=<artifact>",
        )
    return max(candidates)[1]


# --------------------------------------------------------------------------- comparability


def _data(p: dict[str, Any]) -> dict[str, Any]:
    data = p.get("data")
    return data if isinstance(data, dict) else {}


#: Probe arguments that change what is printed, not what is measured.
NON_IDENTITY_ARGS = frozenset({"explain"})


def _without(args: Any, keys: frozenset[str]) -> Any:
    return {k: v for k, v in args.items() if k not in keys} if isinstance(args, dict) else args


def run_identity(p: dict[str, Any]) -> dict[str, Any]:
    """What must match for two runs to be comparable (absent keys are ignored)."""
    ident: dict[str, Any] = {
        "variant": p.get("variant", {}),
        "broker_mode": p.get("broker_mode"),
        "args": _without(_data(p).get("args"), NON_IDENTITY_ARGS),
    }
    # Classic profile artifacts record their knobs at top level.
    for key in ("vendor", "caller", "concurrency"):
        if key in p:
            ident[key] = p[key]
    return {k: v for k, v in ident.items() if v is not None}


def differences(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    b, h = run_identity(base), run_identity(head)
    out: list[str] = []
    for key in sorted(set(b) | set(h)):
        bv, hv = b.get(key), h.get(key)
        if isinstance(bv, dict) and isinstance(hv, dict):
            for sub in sorted(set(bv) | set(hv)):
                if bv.get(sub) != hv.get(sub):
                    out.append(f"{key}.{sub}: {bv.get(sub)!r} -> {hv.get(sub)!r}")
        elif bv != hv:
            out.append(f"{key}: {bv!r} -> {hv!r}")
    return out


def _ratio_below_one(value: Any) -> bool:
    return isinstance(value, (int, float)) and value < 1.0


def failures(p: dict[str, Any]) -> list[str]:
    """Signs the run itself failed (non-200 responses), so its numbers mean nothing."""
    out: list[str] = []
    data = _data(p)
    statuses = data.get("statuses")
    if isinstance(statuses, dict) and set(statuses) - {"200", 200}:
        out.append(f"statuses {statuses}")
    load_ = data.get("load") or p.get("load") or {}
    if isinstance(load_, dict) and _ratio_below_one(load_.get("ok_ratio")):
        out.append(f"load ok_ratio {load_['ok_ratio']:.3f}")
    for row in p.get("rows") or []:
        if not isinstance(row, dict):
            continue
        label = row.get("config") or row.get("case") or row.get("variant") or "row"
        if _ratio_below_one(row.get("ok_ratio")):
            out.append(f"{label}: ok_ratio {row['ok_ratio']:.3f}")
        broker = row.get("broker")
        if isinstance(broker, dict) and _ratio_below_one(broker.get("ok_ratio")):
            out.append(f"{label}: broker ok_ratio {broker['ok_ratio']:.3f}")
        ok, requests = row.get("ok"), row.get("requests")
        if isinstance(ok, int) and isinstance(requests, int) and ok != requests:
            out.append(f"{label}: {ok}/{requests} ok")
    return out


# --------------------------------------------------------------------------- extractors


def m_sql(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    out: list[Metric] = []
    bt, ht = b["data"]["totals_per_req"], h["data"]["totals_per_req"]
    for k in sorted(set(bt) | set(ht)):
        out.append(Metric(f"total.{k}/req", bt.get(k, 0), ht.get(k, 0), exact=True))
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
            gate=False,
        ),
    ]
    bs = {s["stage"]: s for s in b["data"]["stages"]}
    hs = {s["stage"]: s for s in h["data"]["stages"]}
    for name in sorted(set(bs) | set(hs)):
        if name not in hs:
            # The probe lost coverage (renamed target, or the path no longer runs).
            out.append(Metric(f"{name} present", 1, 0, better="higher", exact=True))
            continue
        base_calls = bs[name]["calls_per_req"] if name in bs else 0.0
        out.append(Metric(f"{name} calls/req", base_calls, hs[name]["calls_per_req"], abs_tol=0.05))
        if name in bs:
            out.append(
                Metric(f"{name} mean ms", bs[name]["mean_ms"], hs[name]["mean_ms"], floor=0.3)
            )
            out.append(Metric(f"{name} p99 ms", bs[name]["p99_ms"], hs[name]["p99_ms"], gate=False))
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
        out.append(Metric(f"{k} p99 ms", bi[k]["p99_ms"], hi[k]["p99_ms"], gate=False))
    return out


def m_dbcost(b: dict[str, Any], h: dict[str, Any]) -> list[Metric]:
    def idx(p: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {f"{r['variant']} {r['caller']}": r["total_per_call"] for r in p["rows"]}

    bi, hi = idx(b), idx(h)
    out: list[Metric] = []
    for k in sorted(set(bi) & set(hi)):
        for m in ("statements", "transactions", "writes"):
            out.append(Metric(f"{k} {m}/call", bi[k][m], hi[k][m], abs_tol=0.1))
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


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--head", type=Path, default=None)
    parser.add_argument("--tolerance", type=float, default=0.10)
    parser.add_argument(
        "--force", action="store_true", help="compare runs that differ or failed (prints why)"
    )
    args = parser.parse_args(argv)

    base_path = _resolve(args.base)
    kind, base = load(base_path)
    head_path = _resolve(args.head) if args.head else newest(kind, base_path, base)
    head_kind, head = load(head_path)
    if head_kind != kind and {head_kind, kind} != {"cpu", "profile"}:
        raise PrereqError(
            f"kind mismatch: BASE is {kind}, HEAD is {head_kind}", "pass two artifacts of one kind"
        )
    extractor = EXTRACTORS.get(kind)
    if extractor is None:
        raise PrereqError(
            f"don't know how to compare {kind} artifacts", ", ".join(sorted(EXTRACTORS))
        )

    problems = [f"differs: {d}" for d in differences(base, head)]
    problems += [f"BASE failed: {f}" for f in failures(base)]
    problems += [f"HEAD failed: {f}" for f in failures(head)]
    if written_at(head_path, head) < written_at(base_path, base):
        problems.append("HEAD is older than BASE: improved/regressed read backwards")
    if problems and not args.force:
        listing = "\n".join(f"  - {p}" for p in problems)
        raise PrereqError(
            f"runs are not comparable:\n{listing}",
            "re-run both with the same knobs on a healthy stack (make perf-agent-doctor), "
            "or add FORCE=1 to compare anyway",
        )

    try:
        metrics = extractor(base, head)
    except (KeyError, TypeError) as exc:
        raise PrereqError(
            f"malformed {kind} artifact (missing {exc})", "re-run the probe to regenerate it"
        ) from exc
    rows = []
    counts = dict.fromkeys(VERDICTS, 0)
    for m in metrics:
        v = m.verdict(args.tolerance)
        counts[v] += 1
        delta = m.head - m.base
        rel = f"{100 * delta / m.base:+.1f}%" if m.base else "n/a"
        rows.append(
            [v.upper() if v in ("regressed", "improved") else v, m.key, m.base, m.head, delta, rel]
        )
    order = {"REGRESSED": 0, "IMPROVED": 1, "unchanged": 2, "info": 3}
    rows.sort(key=lambda r: (order[str(r[0])], str(r[1])))

    rep = Report("compare", f"Compare {kind}: {base_path.name} -> {head_path.name}")
    rep.data = {
        "kind": kind,
        "base": _display(base_path),
        "head": _display(head_path),
        "tolerance": args.tolerance,
        "forced": bool(problems),
        "problems": problems,
        "counts": counts,
        "metrics": [
            {"verdict": r[0], "metric": r[1], "base": r[2], "head": r[3], "delta": r[4]}
            for r in rows
        ],
    }
    for p in problems:
        rep.highlights.append(f"FORCED despite: {p}")
    rep.highlights.append(
        f"{counts['regressed']} regressed, {counts['improved']} improved, "
        f"{counts['unchanged']} unchanged, {counts['info']} info-only "
        f"(tolerance {args.tolerance:.0%}; see the module docstring for count tolerances)"
    )
    for r in rows:
        if r[0] in ("REGRESSED", "IMPROVED"):
            rep.highlights.append(f"{r[0]}: {r[1]} {r[2]:.3g} -> {r[3]:.3g} ({r[5]})")
    rep.section(
        "Metrics",
        md_table(["verdict", "metric", "base", "head", "delta", "rel"], rows, digits=3),
        note="info = single-run tail latency, too noisy to gate on; reported for context",
    )
    # make rewrites a failing recipe's status to 2, so also say it in-band.
    rep.result = f"{'REGRESSED' if counts['regressed'] else 'OK'} (exit 3 = regressed)"
    rep.finish()
    return EXIT_REGRESSION if counts["regressed"] else 0
