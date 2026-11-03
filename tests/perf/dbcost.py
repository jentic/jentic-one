"""Per-call database cost of a brokered request, attributed per plane.

Each plane connects as its own Postgres role (registry_user / control_user /
admin_user), so ``pg_stat_statements`` grouped by role tells us exactly how
many statements — and how many transactions (``BEGIN``) — one broker call
costs on each database, plus the total DB time.

Method: reset stats, idle for the window to measure background noise (worker
polling etc.), reset, drive ``N`` sequential calls (one connection) at a low
fixed rate, read stats, subtract the idle baseline, divide by ``N``. The run is
aborted unless all ``N`` calls returned 200: a failed call costs fewer
statements, so a partial run would understate the per-call cost. Sequential +
low rate keeps cache behaviour deterministic: ``warm`` pins every broker TTL to
300 s so the window never straddles an expiry (100% cache hits); ``cold`` sets
every TTL to 0.

The ``app`` container (control/admin/registry surfaces + background scanners and
the job worker) is stopped for the duration — the standalone broker never calls
it on a synchronous ``POST /execute``, and its polling would otherwise leak into
the control/admin numbers. It is restarted on exit, whatever else fails.

Counting: ``statements`` is real SQL only; ``BEGIN``/``COMMIT``/``ROLLBACK`` are
reported separately as ``tx_control``. ``transactions`` counts ``BEGIN`` — when
``pool_pre_ping`` is enabled (the default) a checkout whose connection has sat
idle for at least ``pool_pre_ping_idle_s`` seconds (or every checkout when
``pool_pre_ping_idle_s`` is ``0``) runs its ping in its own ``BEGIN``/``ROLLBACK``;
a read-only session opened with ``DatabaseSession.read_session`` runs in
autocommit and adds no ``BEGIN`` of its own.
``writes`` counts ``INSERT``/``UPDATE``/``DELETE``/``MERGE`` statements,
including a data-modifying ``WITH … INSERT/UPDATE/DELETE`` (a ``SELECT … FOR
UPDATE`` is a read and is not counted).

Usage::

    uv run python -m tests.perf.dbcost                       # warm, jak_ caller
    uv run python -m tests.perf.dbcost --variant cold --caller at
    uv run python -m tests.perf.dbcost --all                 # warm/cold x jak/at/jwt
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from tests.perf.bench_overhead import (
    VARIANTS,
    Case,
    oha,
    restore_default,
    start_oha,
    stop_oha,
    summarise,
    switch_variant,
)
from tests.perf.common import (
    PerfEnv,
    compose,
    exclusive_run,
    restore_on_exit,
    run_metadata,
    upstream_base,
    write_result,
)

ROLES = ("registry_user", "control_user", "admin_user")
METRICS = ("statements", "transactions", "tx_control", "writes", "ms")
DB_VARIANTS = {"warm": "warm-cache", "cold": "cold-cache"}

#: Data-modifying statements: plain DML, or a ``WITH`` whose body modifies data
#: (``SELECT … FOR UPDATE`` doesn't match: no ``SET`` follows its ``UPDATE``).
WRITE_PREDICATE = (
    "(s.query ~* '^\\s*(INSERT|UPDATE|DELETE|MERGE)\\M'"
    " OR (s.query ~* '^\\s*WITH\\M' AND s.query ~* "
    "'\\m(INSERT\\s+INTO|DELETE\\s+FROM|UPDATE\\s+\\S+(\\s+(AS\\s+)?\\S+)?\\s+SET)\\M'))"
)

STATS_SQL = f"""
SELECT r.rolname,
       COALESCE(SUM(s.calls) FILTER (WHERE s.query !~* '^\\s*(BEGIN|COMMIT|ROLLBACK)'), 0)::bigint,
       COALESCE(SUM(s.calls) FILTER (WHERE s.query ~* '^\\s*BEGIN'), 0)::bigint,
       COALESCE(SUM(s.calls) FILTER (WHERE s.query ~* '^\\s*(BEGIN|COMMIT|ROLLBACK)'), 0)::bigint,
       COALESCE(SUM(s.calls) FILTER (WHERE {WRITE_PREDICATE}), 0)::bigint,
       COALESCE(SUM(s.total_exec_time), 0)::float
FROM pg_roles r
LEFT JOIN pg_stat_statements s ON s.userid = r.oid
WHERE r.rolname IN ('registry_user', 'control_user', 'admin_user')
GROUP BY r.rolname ORDER BY r.rolname;
"""

TOP_SQL = """
SELECT r.rolname, s.calls, round(s.mean_exec_time::numeric, 3),
       left(regexp_replace(s.query, '\\s+', ' ', 'g'), 160)
FROM pg_stat_statements s JOIN pg_roles r ON s.userid = r.oid
WHERE r.rolname IN ('registry_user', 'control_user', 'admin_user')
  AND s.query NOT ILIKE 'BEGIN%' AND s.query NOT ILIKE 'COMMIT%' AND s.query NOT ILIKE 'ROLLBACK%'
ORDER BY s.calls DESC LIMIT 25;
"""


def psql(sql: str) -> list[list[str]]:
    out = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "postgres",
        "-d",
        "jentic",
        "-At",
        "-F",
        "\t",
        "-c",
        sql,
        capture=True,
    )
    return [line.split("\t") for line in out.strip().splitlines() if line]


def reset() -> None:
    psql("SELECT pg_stat_statements_reset();")


def snapshot() -> dict[str, dict[str, float]]:
    rows = psql(STATS_SQL)
    return {
        role: {
            "statements": float(calls),
            "transactions": float(tx),
            "tx_control": float(txc),
            "writes": float(w),
            "ms": float(ms),
        }
        for role, calls, tx, txc, w, ms in rows
    }


class IncompleteRunError(RuntimeError):
    """Not every measured call succeeded, so per-call counts would be wrong."""


def measure(env: PerfEnv, variant: str, caller: str, n: int, rate: int) -> dict[str, Any]:
    window_s = n / rate
    case = Case(f"dbcost-{variant}-{caller}", caller=caller)
    url = f"{env.broker_url}/{upstream_base(env.vendors['perf-noauth']['alias'])}{case.path}"
    token = env.caller(caller).token
    # Warm (or, for cold, at least load modules/pools) before measuring.
    oha(url, case, 20, 50, token)
    time.sleep(1)

    reset()
    time.sleep(window_s)
    idle = snapshot()

    reset()
    # One connection: calls are strictly sequential, so cache state is too.
    result = summarise(oha(url, case, rate, n, token, conns=1), 200)
    ok = result["status_codes"].get("200", 0)
    if ok != n:
        raise IncompleteRunError(
            f"dbcost {variant}/{caller}: {ok}/{n} calls returned 200 "
            f"(status {result['status_codes']}, errors {result['errors']})"
        )
    # Let post-response background persistence (stream passthrough) land.
    time.sleep(1.5)
    busy = snapshot()

    per_call: dict[str, dict[str, float]] = {}
    for role in ROLES:
        b, i = busy.get(role, {}), idle.get(role, {})
        per_call[role] = {k: max(0.0, b.get(k, 0.0) - i.get(k, 0.0)) / ok for k in METRICS}
    total = {k: sum(v[k] for v in per_call.values()) for k in METRICS}
    top = psql(TOP_SQL)
    return {
        "variant": variant,
        "caller": caller,
        "requests": n,
        "rate": rate,
        "ok": ok,
        "per_call": per_call,
        "total_per_call": total,
        "idle_window": idle,
        "top_statements": [
            {"role": r, "calls": int(c), "mean_ms": float(m), "query": q} for r, c, m, q in top
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-call DB cost per plane (pg_stat_statements)")
    parser.add_argument("--variant", choices=sorted(DB_VARIANTS), default="warm")
    parser.add_argument("--caller", choices=("jak", "at", "jwt"), default="jak")
    parser.add_argument("--all", action="store_true", help="warm/cold x jak/at/jwt")
    parser.add_argument("-n", type=int, default=200, help="calls per measurement")
    parser.add_argument("--rate", type=int, default=10)
    args = parser.parse_args(argv)
    assert set(DB_VARIANTS.values()) <= set(VARIANTS)

    env = PerfEnv.load()
    combos = (
        [(v, c) for v in DB_VARIANTS for c in ("jak", "at", "jwt")]
        if args.all
        else [(args.variant, args.caller)]
    )
    # Idle window + measured window per combo, plus a broker recreate each.
    env.check_fresh(
        callers={c for _, c in combos},
        run_s=len(combos) * (2 * args.n / args.rate + 60),
        shorten="lower -n, raise --rate, or drop --all",
    )
    rows: list[dict[str, Any]] = []
    state: dict[str, str | None] = {"variant": None}
    with (
        exclusive_run("dbcost"),
        restore_on_exit(
            ("remove oha sidecar", stop_oha),
            ("broker back to default variant", lambda: restore_default(state)),
            ("restart app", lambda: compose("start", "app")),
        ),
    ):
        start_oha()
        compose("stop", "app")
        for variant, caller in combos:
            switch_variant(state, DB_VARIANTS[variant])
            row = measure(env, variant, caller, args.n, args.rate)
            rows.append(row)
            parts = "  ".join(
                f"{role.split('_')[0]}: {v['statements']:.1f} stmt / {v['transactions']:.2f} tx"
                for role, v in row["per_call"].items()
            )
            t = row["total_per_call"]
            print(
                f"  {variant:<5} {caller:<4} total {t['statements']:.1f} stmt, "
                f"{t['transactions']:.2f} tx, {t['writes']:.2f} writes, "
                f"{t['ms']:.2f} ms DB  |  {parts}",
                flush=True,
            )
        path = write_result("dbcost", {**run_metadata("dbcost"), "rows": rows})
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
