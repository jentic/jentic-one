"""``make perf-agent-sql``: what SQL does one broker call run, and from which line?

Drives ``N`` sequential calls (fresh ``X-Request-Id`` each) through a broker
traced with ``PERF_TRACE=sql`` and prints, per request:

* **per plane**: sessions (pool checkouts), pre-pings, transactions, commits /
  rollbacks, statements, writes, zero-row SELECTs, read-only sessions that
  COMMIT, DB ms;
* **statements grouped by (plane, normalized SQL, callsite)** with calls per
  request, average rows, zero-row %, ms;
* **one representative request's timeline** (session → ping → BEGIN → SQL →
  COMMIT/ROLLBACK) with ``file:line`` for every step;
* ``EXPLAIN (ANALYZE, BUFFERS)`` for the costliest SELECTs, executed in-container
  as the plane's own role with the captured parameters inside a rolled-back
  transaction. Statements with a truncated parameter, and CTEs that write, are
  skipped with a note rather than planned with the wrong values.

Bind parameter values are captured only when ``EXPLAIN`` runs, used in memory,
and never written to the artifact; the raw trace files are deleted when the
traced broker stops. Any non-200 response aborts the run (exit 2).

Timings come from a traced broker: use them for ranking, not as absolutes.
"""

from __future__ import annotations

import argparse
import re
import statistics
import time
import uuid
from collections import Counter, defaultdict
from typing import Any

import httpx

from tests.perf.agent.base import (
    PrereqError,
    Report,
    TraceTail,
    broker_variant,
    env_or_fail,
    hook_manifest,
    hook_stats,
    md_table,
    proxy_url,
    require_fresh,
    require_patched,
    wait_quiet,
)
from tests.perf.common import compose

CACHE_VARIANTS = {
    "warm": {"PERF_RESOLVE_TTL": "300", "PERF_TOOLKIT_TTL": "300", "PERF_RULE_TTL": "300"},
    "cold": {"PERF_RESOLVE_TTL": "0", "PERF_TOOLKIT_TTL": "0", "PERF_RULE_TTL": "0"},
}
WRITE_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE)", re.I)
SELECT_RE = re.compile(r"^\s*(SELECT|WITH)", re.I)
#: A data-modifying CTE: ``EXPLAIN ANALYZE`` would execute the write.
CTE_WRITE_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE)\b", re.I)
#: Hook targets the SQL accounting cannot do without.
REQUIRED_TARGETS = ("shared.db.session:DatabaseSession",)
PG_TYPES = {
    "str": "text",
    "int": "bigint",
    "float": "double precision",
    "bool": "boolean",
    "datetime": "timestamptz",
    "date": "date",
    "UUID": "uuid",
    "Decimal": "numeric",
    "list": "text[]",
    "tuple": "text[]",
    "dict": "jsonb",
    "bytes": "bytea",
    "NoneType": "text",
}


def pg_type(ptype: str) -> str:
    """Postgres type for a hook ``ptype`` (``list[UUID]`` -> ``uuid[]``)."""
    head, sep, inner = ptype.partition("[")
    if sep and head in ("list", "tuple"):
        return PG_TYPES.get(inner.rstrip("]"), "text") + "[]"
    return PG_TYPES.get(ptype, "text")


def normalize(sql: str) -> str:
    s = re.sub(r"\s+", " ", sql).strip()
    s = re.sub(r"\$\d+(?:::\w+)?(?:\s*,\s*\$\d+(?:::\w+)?)+", "$n…", s)
    return re.sub(r"\$\d+", "$n", s)


def short_sql(sql: str, width: int = 110) -> str:
    s = normalize(sql)
    return s if len(s) <= width else s[: width - 1] + "…"


def top_site(site: str) -> str:
    return site.split(" <- ")[0] if site else "?"


# --------------------------------------------------------------------------- drive


def drive(
    n: int, warmup: int, caller: str, vendor: str
) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    env = env_or_fail()
    # Sequential calls (~0.05 s apart) plus the traced-broker recreate.
    require_fresh(env, callers=[caller], run_s=(warmup + n) * 0.5 + 120)
    url = proxy_url(env, vendor)
    token = env.caller(caller).token
    prefix = f"perf-sql-{uuid.uuid4().hex[:6]}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30) as client:
        for i in range(warmup):
            client.post(url, headers={**headers, "X-Request-Id": f"warm-{i}"}, content=b"{}")
        wait_quiet()
        tail = TraceTail()
        statuses: Counter[int] = Counter()
        for i in range(n):
            resp = client.post(
                url,
                headers={**headers, "X-Request-Id": f"{prefix}-{i}"},
                content=b'{"probe":true}',
            )
            statuses[resp.status_code] += 1
            time.sleep(0.05)
        wait_quiet(2.0)
    events = tail.read()
    by_status = {str(k): v for k, v in sorted(statuses.items())}
    if statuses.get(200, 0) != n:
        raise PrereqError(
            f"non-200 responses {by_status}: the run measured a failing path",
            "make perf-agent-doctor   # stale tokens, or a broken broker",
        )
    return prefix, events, by_status


# --------------------------------------------------------------------------- analyse


def by_request(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("ev") in {"sql", "ping", "checkout", "checkin", "begin", "commit", "rollback"}:
            out[e["rid"]].append(e)
    return out


def plane_table(reqs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    n = max(1, len(reqs))
    totals: dict[str, Counter[str]] = defaultdict(Counter)
    ms: dict[str, float] = defaultdict(float)
    for evs in reqs.values():
        sessions: dict[tuple[str, int], dict[str, bool]] = {}
        for e in evs:
            plane = e.get("plane", "?")
            t = totals[plane]
            kind = e["ev"]
            key = (plane, int(e.get("conn", 0)))
            if kind == "checkout":
                t["sessions"] += 1
                sessions[key] = {"write": False, "commit": False}
            elif kind == "ping":
                t["pings"] += 1
            elif kind == "begin":
                t["transactions"] += 1
            elif kind == "commit":
                t["commits"] += 1
                sessions.setdefault(key, {"write": False, "commit": False})["commit"] = True
            elif kind == "rollback":
                t["rollbacks"] += 1
            elif kind == "sql":
                t["statements"] += 1
                ms[plane] += float(e.get("ms", 0.0))
                if WRITE_RE.match(e["sql"]):
                    t["writes"] += 1
                    sessions.setdefault(key, {"write": False, "commit": False})["write"] = True
                elif SELECT_RE.match(e["sql"]) and int(e.get("rows", -1)) == 0:
                    t["zero_row_selects"] += 1
            elif kind == "checkin":
                s = sessions.pop(key, None)
                if s and s["commit"] and not s["write"]:
                    t["readonly_commits"] += 1
    rows = []
    for plane in sorted(totals):
        t = totals[plane]
        rows.append(
            {
                "plane": plane,
                **{
                    k: round(t[k] / n, 2)
                    for k in (
                        "sessions",
                        "pings",
                        "transactions",
                        "commits",
                        "rollbacks",
                        "statements",
                        "writes",
                        "zero_row_selects",
                        "readonly_commits",
                    )
                },
                "db_ms": round(ms[plane] / n, 3),
            }
        )
    return rows


def statement_groups(reqs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    n = max(1, len(reqs))
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for evs in reqs.values():
        for e in evs:
            if e["ev"] == "sql":
                groups[(e["plane"], normalize(e["sql"]), top_site(e.get("site", "")))].append(e)
    out = []
    for (plane, sql, site), evs in groups.items():
        rows = [int(e.get("rows", -1)) for e in evs]
        out.append(
            {
                "plane": plane,
                "calls_per_req": round(len(evs) / n, 2),
                "rows_avg": round(statistics.fmean(rows), 2),
                "zero_row_pct": round(100 * sum(1 for r in rows if r == 0) / len(rows), 1),
                "ms_avg": round(statistics.fmean(float(e.get("ms", 0)) for e in evs), 3),
                "ms_per_req": round(sum(float(e.get("ms", 0)) for e in evs) / n, 3),
                "site": site,
                "chain": evs[0].get("site", ""),
                "sql": sql,
                "sample": {"sql": evs[0]["sql"], "ptypes": evs[0].get("ptypes", [])},
                "_params": evs[0].get("params"),
            }
        )
    out.sort(key=lambda g: (-g["ms_per_req"], -g["calls_per_req"]))
    return out


def strip_params(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events without bind parameter values (fit for an artifact)."""
    return [{k: v for k, v in e.items() if k != "params"} for e in evs]


def timeline(evs: list[dict[str, Any]]) -> list[list[Any]]:
    t0 = evs[0]["t"] if evs else 0.0
    rows = []
    for e in evs:
        what = e["ev"].upper() if e["ev"] != "sql" else short_sql(e["sql"], 80)
        rows.append(
            [
                f"{(e['t'] - t0) * 1000:.1f}",
                e.get("plane", ""),
                what,
                e.get("rows", "") if e["ev"] == "sql" else "",
                e.get("ms", ""),
                top_site(e.get("site", "")),
            ]
        )
    return rows


# --------------------------------------------------------------------------- explain


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _array_item(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value).lower() if isinstance(value, bool) else str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _literal(value: Any) -> str:
    """A SQL literal for one captured bind value (see the hook's ``_param_value``)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return _quote("{" + ",".join(_array_item(v) for v in value) + "}")
    if isinstance(value, dict) and "bytes_hex" in value:
        return f"'\\x{value['bytes_hex']}'::bytea"
    return _quote(str(value))


def explain_skip_reason(group: dict[str, Any]) -> str:
    """Why EXPLAIN would mislead (or write) for this group; empty = safe to run."""
    sql = group["sample"]["sql"]
    if not SELECT_RE.match(sql):
        return "not a SELECT"
    if sql.lstrip()[:4].upper() == "WITH" and CTE_WRITE_RE.search(sql):
        return "data-modifying CTE (EXPLAIN ANALYZE would execute the write)"
    params = group["_params"]
    if group["sample"]["ptypes"] and params is None:
        return "parameters not captured"
    if any(isinstance(p, dict) and p.get("truncated") for p in params or []):
        return "a parameter was too long to capture; the plan would use a different value"
    return ""


def explain(group: dict[str, Any]) -> str:
    skip = explain_skip_reason(group)
    if skip:
        return f"(EXPLAIN skipped: {skip})"
    sql = group["sample"]["sql"]
    ptypes: list[str] = group["sample"]["ptypes"]
    params: list[Any] = group["_params"] or []
    plane = group["plane"]
    types = ", ".join(pg_type(t) for t in ptypes)
    stmt = f"PREPARE perf_q({types}) AS {sql}" if ptypes else f"PREPARE perf_q AS {sql}"
    args = ", ".join(_literal(v) for v in params)
    execute = f"EXECUTE perf_q({args})" if ptypes else "EXECUTE perf_q"
    script = (
        f"BEGIN; SET LOCAL search_path TO {plane}, public; {stmt}; "
        f"EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) {execute}; ROLLBACK;"
    )
    try:
        out = compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            f"{plane}_user",
            "-d",
            "jentic",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            script,
            capture=True,
        )
    except RuntimeError as exc:
        return f"(EXPLAIN failed: {str(exc)[-300:]})"
    lines = [ln for ln in out.splitlines() if ln.strip() and "QUERY PLAN" not in ln]
    lines = [ln for ln in lines if not set(ln.strip()) <= {"-"} and not ln.startswith("(")]
    return "\n".join(lines[:25])


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--caller", choices=("jak", "at", "jwt"), default="jak")
    parser.add_argument("--cache", choices=sorted(CACHE_VARIANTS), default="warm")
    parser.add_argument("--vendor", default="perf-noauth")
    parser.add_argument("--explain", type=int, default=5, help="EXPLAIN the top N SELECTs")
    args = parser.parse_args(argv)

    overrides = CACHE_VARIANTS[args.cache]
    with broker_variant(overrides, trace="sql", trace_params=args.explain > 0):
        prefix, all_events, statuses = drive(args.n, args.warmup, args.caller, args.vendor)
        manifest = hook_manifest()
        require_patched(manifest, REQUIRED_TARGETS)
        events = [e for e in all_events if str(e.get("rid", "")).startswith(prefix)]
        reqs = by_request(events)
        planes = plane_table(reqs)
        groups = statement_groups(reqs)
        explains = {}
        for g in [g for g in groups if SELECT_RE.match(g["sql"])][: args.explain]:
            explains[f"{g['plane']} {g['site']}"] = explain(g)

    rep = Report(
        "sql",
        f"Broker SQL per call — {args.caller}, {args.cache} cache, {args.vendor}, "
        f"{len(reqs)} requests",
        mode="traced (PERF_TRACE=sql)",
        variant=overrides,
    )
    total = {
        k: round(sum(p[k] for p in planes), 2)
        for k in ("sessions", "pings", "transactions", "statements", "writes", "zero_row_selects")
    }
    rep.data = {
        "args": vars(args),
        "statuses": statuses,
        "hook": {**manifest, **hook_stats(all_events)},
        "requests": len(reqs),
        "totals_per_req": total,
        "planes": planes,
        "statements": [{k: v for k, v in g.items() if k != "_params"} for g in groups],
        "explain": explains,
        "timeline_rid": "",
    }
    rep.highlights.append(
        f"Per call: {total['statements']} statements, {total['sessions']} sessions, "
        f"{total['pings']} pre-pings, {total['transactions']} transactions, "
        f"{total['writes']} writes, {total['zero_row_selects']} zero-row SELECTs"
    )
    stats = rep.data["hook"]
    if stats["errors"] or stats["dropped"]:
        rep.highlights.append(
            f"WARNING: the hook swallowed {stats['errors']} listener errors and dropped "
            f"{stats['dropped']} events; counts may be low"
        )
    for g in groups[:3]:
        rep.highlights.append(
            f"Costliest: {g['plane']} {g['calls_per_req']}x/req {g['ms_per_req']} ms/req "
            f"at `{g['site']}` — `{short_sql(g['sql'], 70)}`"
        )
    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in groups:
        by_site[g["site"]].append(g)
    for site, gs in sorted(by_site.items(), key=lambda kv: -sum(g["calls_per_req"] for g in kv[1])):
        empty = [g for g in gs if g["zero_row_pct"] == 100.0]
        if len(gs) > 1:
            calls = round(sum(g["calls_per_req"] for g in gs), 2)
            rep.highlights.append(
                f"`{site}` issues {len(gs)} distinct statements ({calls}/req), "
                f"{len(empty)} always return 0 rows"
            )
    for p in planes:
        if p["sessions"] > 1 or p["readonly_commits"] > 0:
            rep.highlights.append(
                f"{p['plane']}: {p['sessions']} sessions/req, {p['pings']} pings/req, "
                f"{p['readonly_commits']} read-only sessions that COMMIT/req"
            )

    rep.section(
        "Per plane (per request)",
        md_table(
            [
                "plane",
                "sessions",
                "pings",
                "tx",
                "commits",
                "rollbacks",
                "stmts",
                "writes",
                "0-row SELECTs",
                "RO commits",
                "db ms",
            ],
            [
                [
                    p["plane"],
                    p["sessions"],
                    p["pings"],
                    p["transactions"],
                    p["commits"],
                    p["rollbacks"],
                    p["statements"],
                    p["writes"],
                    p["zero_row_selects"],
                    p["readonly_commits"],
                    p["db_ms"],
                ]
                for p in planes
            ],
        ),
        note="sessions = pool checkouts; pings = pool_pre_ping round trips; RO commits = "
        "sessions with no writes that still COMMIT",
    )
    rep.section(
        "Statements by (plane, SQL, callsite)",
        md_table(
            ["plane", "calls/req", "rows avg", "0-row %", "ms avg", "ms/req", "callsite", "sql"],
            [
                [
                    g["plane"],
                    g["calls_per_req"],
                    g["rows_avg"],
                    g["zero_row_pct"],
                    g["ms_avg"],
                    g["ms_per_req"],
                    g["site"],
                    short_sql(g["sql"], 90),
                ]
                for g in groups
            ],
            digits=3,
        ),
        note="full SQL, param types and call chains are in the artifact (data.statements)",
    )
    if reqs:
        counts = sorted(reqs.items(), key=lambda kv: len(kv[1]))
        rid, evs = counts[len(counts) // 2]
        rep.data["timeline_rid"] = rid
        rep.data["timeline"] = strip_params(evs)
        rep.section(
            f"Timeline of a median request ({rid})",
            md_table(["+ms", "plane", "event / SQL", "rows", "ms", "callsite"], timeline(evs)),
        )
    for key, plan in explains.items():
        rep.section(f"EXPLAIN (ANALYZE, BUFFERS) — {key}", f"```\n{plan}\n```")
    rep.next_steps += [
        "make perf-agent-sql CACHE=cold   # cache-miss path",
        "make perf-agent-sql CALLER=at    # opaque-token caller",
        "make perf-agent-compare BASE=<this ARTIFACT>   # after a fix",
    ]
    rep.finish()
    return 0
