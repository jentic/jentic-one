"""Shared pieces for the admin monitoring load lane: HTTP client, seeder, DB counters.

Split out from ``bench_monitoring`` so the driver stays readable and so the
aggregate invariants can be imported by other tools.

Everything here is stdlib only and reaches Postgres through ``psql``, like
:mod:`tests.perf.dbcost` does — but through an injectable target, so the lane
runs against a throwaway container (``--pg-container``) as well as the compose
stack's ``postgres`` service. An admin read needs the ``app`` surface up, so
this lane never stops it.

Cache accounting: the monitoring caches are in-process and the local perf
config exports no metrics, so a hit/miss counter is not readable over HTTP.
Instead the lane counts **scans of ``admin.execution_records``** in
``pg_stat_user_tables`` before and after a batch: a cache hit touches no table,
a miss scans it. That works on a bare Postgres, with no ``pg_stat_statements``
preload and no exporter.
"""

from __future__ import annotations

import json
import math
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, TypeGuard

from tests.perf.common import compose

#: Columns the seeder writes. ``id`` is left to the KSUID server default.
_SEED_COLUMNS = (
    "trace_id, started_at, duration_ms, status, operation_id, operation_method, "
    "operation_path, api_vendor, api_name, api_version, http_status, actor_id, "
    "actor_type, created_by, toolkit_id"
)

#: Duration ladder, in ms. Deliberately chosen so ``percentile_cont`` (which
#: interpolates) and ``percentile_disc`` (which does not) disagree: with 4
#: distinct values per cycle the continuous p50 lands between the middle two.
#: One slot is NULL so the "avg ignores NULLs while total counts them"
#: behaviour stays exercised at every scale.
_DURATIONS = ("10", "20", "30", "1000", "NULL")


class ProbeError(RuntimeError):
    """A probe could not be completed; never raised for a mere invariant violation."""


@dataclass(frozen=True)
class Psql:
    """A way to run SQL. ``container`` bypasses compose; otherwise the compose service."""

    container: str | None = None
    database: str = "jentic"
    user: str = "postgres"

    def _flags(self, sql: str) -> list[str]:
        return ["-U", self.user, "-d", self.database, "-At", "-F", "\t", "-c", sql]

    def rows(self, sql: str) -> list[list[str]]:
        """Run ``sql`` and return tab-split rows, via ``docker exec`` or compose."""
        if self.container:
            argv = ["docker", "exec", "-i", self.container, "psql", *self._flags(sql)]
            proc = subprocess.run(argv, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise ProbeError(f"psql failed: {proc.stderr.strip()[:400]}")
            out = proc.stdout
        else:
            out = compose("exec", "-T", "postgres", "psql", *self._flags(sql), capture=True)
        return [line.split("\t") for line in out.strip().splitlines() if line]

    def scalar(self, sql: str) -> str:
        rows = self.rows(sql)
        if not rows or not rows[0]:
            raise ProbeError(f"expected one value from: {sql[:120]}")
        return rows[0][0]


def seed_executions(
    psql: Psql,
    count: int,
    *,
    spread_s: int,
    actors: int,
    apis: int,
    analyze: bool = True,
    id_offset: int = 0,
) -> int:
    """Insert ``count`` execution records set-based, spread back over ``spread_s`` seconds.

    Deterministic by construction — every value derives from the ``generate_series``
    index, so a reseed at the same parameters produces the same distribution and a
    comparison between runs stays meaningful. Statuses alternate over the real
    ``ExecutionStatus`` values (``completed``/``failed``) in a 4:1 ratio.

    ``id_offset`` shifts the series the ``trace_id`` derives from. A repeated call
    at offset 0 writes the same trace ids each time, which these aggregates do not
    care about but a trace-id-filtered measurement would, so the write loop in the
    consistency phase advances it per batch to keep them distinct.

    Seam for the larger ladder: this covers one table at the volumes a laptop can
    hold. The xs/s/m/l multi-table seeder (``make perf-seed SCALE=…``) lands
    separately; when it does, call it instead for 10^6+ and keep this for the
    self-contained case.
    """
    status = "CASE WHEN g % 5 = 0 THEN 'failed' ELSE 'completed' END"
    duration = "(ARRAY[" + ",".join(_DURATIONS) + "])[(g % 5) + 1]"
    sql = f"""
    INSERT INTO admin.execution_records ({_SEED_COLUMNS})
    SELECT md5((g + {id_offset})::text),
           now() - ((g % {spread_s}) * interval '1 second'),
           {duration},
           {status},
           'op_' || (g % 7),
           'GET',
           '/seed/' || (g % 7),
           'vendor' || (g % {apis}),
           'api' || (g % {apis}),
           '1',
           CASE WHEN g % 5 = 0 THEN 500 ELSE 200 END,
           'actor_' || (g % {actors}),
           'user',
           'actor_' || (g % {actors}),
           'default'
    FROM generate_series(1, {count}) g;
    """
    psql.rows(sql)
    if not analyze:
        # The write loop in the consistency phase calls this in a tight loop;
        # ANALYZE and a full count(*) there would cost more than the insert.
        return -1
    psql.rows("ANALYZE admin.execution_records;")
    return int(psql.scalar("SELECT count(*) FROM admin.execution_records;"))


def execution_scans(psql: Psql) -> int:
    """Total scans of ``admin.execution_records`` so far (seq + index).

    The cache-miss signal: a served-from-cache monitoring read adds nothing here.
    """
    val = psql.scalar(
        "SELECT COALESCE(seq_scan,0) + COALESCE(idx_scan,0) FROM pg_stat_user_tables "
        "WHERE schemaname='admin' AND relname='execution_records';"
    )
    return int(val)


@dataclass
class Call:
    """One completed HTTP call."""

    label: str
    status: int
    ms: float
    body: dict[str, Any] | None


class MonitoringClient:
    """Bearer-authenticated GETs against the admin surface (stdlib urllib).

    With ``credentials`` the client re-mints its token once on a 401 and retries.
    A run long enough to matter outlives a short access-token TTL, and a local app
    started in debug mode rotates its signing key on every reload — either turns
    the rest of the run into fast 401s that no latency number would reveal.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 30.0,
        credentials: tuple[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.credentials = credentials
        self.relogins = 0
        self._lock = threading.Lock()

    def _relogin(self) -> bool:
        """Mint a fresh token; False when no credentials were supplied."""
        if self.credentials is None:
            return False
        email, password = self.credentials
        payload = json.dumps({"email": email, "password": password}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/auth/login",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return False
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            return False
        with self._lock:
            self.token = token
            self.relogins += 1
        return True

    def get(self, path: str, *, label: str | None = None) -> Call:
        call = self._get_once(path, label=label)
        if call.status == 401 and self._relogin():
            return self._get_once(path, label=label)
        return call

    def _get_once(self, path: str, *, label: str | None = None) -> Call:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            method="GET",
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                status = int(resp.status)
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), int(exc.code)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProbeError(f"GET {path}: {exc}") from exc
        ms = (time.perf_counter() - start) * 1000.0
        body: dict[str, Any] | None = None
        if raw:
            try:
                parsed = json.loads(raw)
                body = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                body = None
        return Call(label or path, status, ms, body)


def percentiles(values: list[float]) -> dict[str, float]:
    """p50/p95/p99 by nearest-rank on a sorted copy (empty -> zeros).

    ``n`` rides along because nearest-rank resolution is set by the sample size:
    a quantile is only as meaningful as the number of samples behind it, and at
    n=40 a p99 is just the second-highest value. Every caller records it so a
    reader can tell a real tail from one sample.
    """
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0, "n": 0.0}
    ordered = sorted(values)

    def at(q: float) -> float:
        idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
        return ordered[idx]

    return {
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
        "n": float(len(ordered)),
    }


def _finite(value: Any) -> TypeGuard[float]:
    """True for a real, finite number — and narrows it, so ``float(x)`` typechecks."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def usage_violations(body: dict[str, Any]) -> list[str]:
    """Internal-consistency checks on a ``GET /monitoring/usage`` body.

    These hold regardless of what rows exist, so a violation is a real defect
    rather than a seeding artefact. The bucket-sum check is the sharp one under
    concurrent writes: ``overall_stats`` and ``time_buckets`` are separate
    statements, so a READ COMMITTED session re-snapshots between them and an
    insert landing in the gap makes the two disagree.
    """
    out: list[str] = []
    stats = body.get("stats") or {}
    since, until = body.get("since"), body.get("until")
    bucket_s = body.get("bucket_seconds")

    for key in ("total", "success", "failed", "pending", "avg_ms", "active_now"):
        if not _finite(stats.get(key)):
            out.append(f"stats.{key} is not a finite number: {stats.get(key)!r}")
        elif float(stats[key]) < 0:
            out.append(f"stats.{key} is negative: {stats[key]!r}")

    total = stats.get("total")
    if _finite(total):
        if (
            _finite(stats.get("success"))
            and _finite(stats.get("failed"))
            and float(stats["success"]) + float(stats["failed"]) > float(total)
        ):
            out.append(
                f"success+failed ({stats['success']}+{stats['failed']}) exceeds total {total}"
            )
        buckets = body.get("buckets") or []
        summed = sum(float(b.get("total", 0)) for b in buckets if _finite(b.get("total")))
        if buckets and summed != float(total):
            out.append(f"bucket totals sum to {summed:g} but stats.total is {total}")
        top_sum = sum(float(r.get("total", 0)) for r in (body.get("top") or []))
        if top_sum > float(total):
            out.append(f"top row totals sum to {top_sum:g}, above stats.total {total}")

    p50, p95 = stats.get("p50_ms"), stats.get("p95_ms")
    if _finite(p50) and _finite(p95) and float(p50) > float(p95):
        out.append(f"p50_ms {p50} exceeds p95_ms {p95}")

    if isinstance(until, int) and until % 60 != 0:
        out.append(f"until {until} is not ceiled to a minute boundary")
    if isinstance(since, int) and isinstance(until, int) and since >= until:
        out.append(f"since {since} is not before until {until}")

    for b in body.get("buckets") or []:
        ts = b.get("ts")
        if (
            isinstance(ts, int)
            and isinstance(since, int)
            and isinstance(bucket_s, int)
            and bucket_s
            and (ts - since) % bucket_s != 0
        ):
            out.append(f"bucket ts {ts} is off the grid anchored at since={since}")
        if (
            _finite(b.get("success"))
            and _finite(b.get("failed"))
            and _finite(b.get("total"))
            and float(b["success"]) + float(b["failed"]) > float(b["total"])
        ):
            out.append(f"bucket {ts}: success+failed exceeds total")
    return out


def executions_violations(body: dict[str, Any]) -> list[str]:
    """Cross-statement consistency checks on ``GET /monitoring/executions``.

    The route is served by two statements in one ``read committed`` session —
    ``daily_buckets`` then ``top_operations`` — so a commit landing between them
    leaves the two describing different snapshots. That span is the only place a
    body can contradict itself, so it is the only thing worth checking.

    Comparisons within one statement carry no information and are deliberately
    absent: ``total_executions`` and ``success_rate_percent`` are both summed in
    Python from the ``daily_buckets`` rows the response already carries, so
    re-summing those rows can never disagree and the rate cannot leave 0..100;
    and per-bucket ``success``/``failed`` are disjoint ``FILTER`` counts inside a
    single ``GROUP BY`` row, so neither can exceed that row's own ``total``.

    ``top_operations`` reads a subset of the rows ``daily_buckets`` counts (it
    requires a non-null operation and api, and keeps only the top ten), so its
    totals sum to at most ``total_executions`` whenever both statements share a
    snapshot. Exceeding it means they did not. The check is sound for any data;
    it is *sharp* for :func:`seed_executions`, whose seven operations all fit
    under that limit and are never null, making the sum exactly equal and any
    interleaved commit visible.
    """
    out: list[str] = []
    total = body.get("total_executions")
    if not _finite(total) or float(total) < 0:
        # A shape guard, not an arithmetic one: this catches a serialisation
        # defect, not a snapshot disagreement.
        out.append(f"total_executions is not a non-negative number: {total!r}")
    top = body.get("top_operations") or []
    summed = sum(float(r["total"]) for r in top if _finite(r.get("total")))
    if top and _finite(total) and summed > float(total):
        out.append(f"top_operations totals sum to {summed:g}, above total_executions {total}")
    return out
