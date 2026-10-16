"""Unit tests for ``tests/perf/agent/compare.py`` — the regression gate agents rely on."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from tests.perf import common
from tests.perf.agent import base, compare
from tests.perf.agent.base import EXIT_REGRESSION, PrereqError
from tests.perf.agent.compare import Metric, m_dbcost, m_stages
from tests.perf.common import Caller, PerfEnv


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        # dbcost per-call counts: pg_stat_statements rounding is absorbed, +1/call is not.
        (Metric("stmts/call", 13, 14, abs_tol=0.1), "regressed"),
        (Metric("stmts/call", 13, 13.05, abs_tol=0.1), "unchanged"),
        (Metric("stmts/call", 13, 12, abs_tol=0.1), "improved"),
        # Sequential counts are exact.
        (Metric("stmts/req", 13, 14, exact=True), "regressed"),
        (Metric("stmts/req", 13, 13, exact=True), "unchanged"),
        # Closed-loop calls/request: one truncated request is noise.
        (Metric("calls/req", 1.0, 1.01, abs_tol=0.05), "unchanged"),
        (Metric("calls/req", 1.0, 2.0, abs_tol=0.05), "regressed"),
        # Relative tolerance with an absolute floor.
        (Metric("mean ms", 10.0, 10.9), "unchanged"),
        (Metric("mean ms", 10.0, 11.5), "regressed"),
        (Metric("mean ms", 1.0, 1.4, floor=0.5), "unchanged"),
        (Metric("rps", 900, 2500, better="higher", floor=5), "improved"),
        # Single-run tails never gate.
        (Metric("p99 ms", 10.0, 50.0, gate=False), "info"),
    ],
)
def test_metric_verdict(metric: Metric, expected: str) -> None:
    assert metric.verdict(0.10) == expected


def test_dbcost_one_extra_statement_per_call_regresses() -> None:
    row = {"variant": "warm", "caller": "jak", "total_per_call": {}}
    b = {"rows": [{**row, "total_per_call": {"statements": 13, "transactions": 4, "writes": 1}}]}
    h = {"rows": [{**row, "total_per_call": {"statements": 14, "transactions": 4, "writes": 1}}]}
    verdicts = {m.key: m.verdict(0.10) for m in m_dbcost(b, h)}
    assert verdicts["warm jak statements/call"] == "regressed"
    assert verdicts["warm jak transactions/call"] == "unchanged"


def _stages(stages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "data": {
            "handler_mean_ms": 5.0,
            "load": {"rps": 500.0},
            "loop_lag": {"p99_ms": 1.0},
            "stages": stages,
        }
    }


def _stage(name: str, calls: float = 1.0) -> dict[str, Any]:
    return {"stage": name, "calls_per_req": calls, "mean_ms": 1.0, "p99_ms": 2.0}


def test_stages_lost_or_added_stage_regresses() -> None:
    b = _stages([_stage("a"), _stage("gone")])
    h = _stages([_stage("a"), _stage("new")])
    verdicts = {m.key: m.verdict(0.10) for m in m_stages(b, h)}
    assert verdicts["gone present"] == "regressed"
    assert verdicts["new calls/req"] == "regressed"
    assert verdicts["a calls/req"] == "unchanged"
    assert verdicts["a p99 ms"] == "info"
    assert verdicts["loop lag p99 ms"] == "info"


@pytest.fixture()
def artifact_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point compare's artifact discovery and Report output at ``tmp_path``."""
    monkeypatch.setattr(compare, "AGENT_DIR", tmp_path)
    monkeypatch.setattr(compare, "RESULTS_DIR", tmp_path / "classic")
    monkeypatch.setattr(base, "AGENT_DIR", tmp_path / "out")
    return tmp_path


def _sql_artifact(
    directory: Path,
    stamp: str,
    *,
    statements: float = 13,
    args: dict[str, Any] | None = None,
    statuses: dict[str, int] | None = None,
) -> Path:
    payload = {
        "cmd": "sql",
        "timestamp": f"2026-10-16T{stamp}+00:00",
        "broker_mode": "traced (PERF_TRACE=sql)",
        "variant": {"PERF_RESOLVE_TTL": "300"},
        "data": {
            "args": args or {"n": 20, "caller": "jak", "cache": "warm", "explain": 5},
            "statuses": statuses or {"200": 20},
            "totals_per_req": {"statements": statements, "sessions": 4},
            "planes": [{"plane": "control", "sessions": 2, "statements": statements}],
        },
    }
    path = directory / f"20261016T{stamp.replace(':', '')}Z-sql.json"
    path.write_text(json.dumps(payload))
    return path


def test_compare_flags_regression(artifact_dir: Path) -> None:
    b = _sql_artifact(artifact_dir, "10:00:00")
    h = _sql_artifact(artifact_dir, "11:00:00", statements=14)
    assert compare.main(["--base", str(b), "--head", str(h)]) == EXIT_REGRESSION


def test_compare_default_head_is_newer_than_base(artifact_dir: Path) -> None:
    older = _sql_artifact(artifact_dir, "09:00:00", statements=99)
    b = _sql_artifact(artifact_dir, "10:00:00")
    newer = _sql_artifact(artifact_dir, "11:00:00")
    _, base_payload = compare.load(b)
    assert compare.newest("sql", b, base_payload) == newer
    _, newest_payload = compare.load(newer)
    with pytest.raises(PrereqError, match="newer than"):
        compare.newest("sql", newer, newest_payload)
    assert older.exists()


@pytest.mark.parametrize(
    ("head_kwargs", "match"),
    [
        ({"args": {"n": 20, "caller": "at", "cache": "warm", "explain": 5}}, "args.caller"),
        ({"statuses": {"200": 10, "401": 10}}, "HEAD failed: statuses"),
    ],
)
def test_compare_refuses_non_comparable_runs(
    artifact_dir: Path, head_kwargs: dict[str, Any], match: str
) -> None:
    b = _sql_artifact(artifact_dir, "10:00:00")
    h = _sql_artifact(artifact_dir, "11:00:00", statements=9, **head_kwargs)
    with pytest.raises(PrereqError, match=match):
        compare.main(["--base", str(b), "--head", str(h)])
    # --force compares anyway (and records why it was forced).
    assert compare.main(["--base", str(b), "--head", str(h), "--force"]) == 0


def test_compare_refuses_head_older_than_base(artifact_dir: Path) -> None:
    b = _sql_artifact(artifact_dir, "11:00:00")
    h = _sql_artifact(artifact_dir, "10:00:00")
    with pytest.raises(PrereqError, match="older than BASE"):
        compare.main(["--base", str(b), "--head", str(h)])


def test_compare_malformed_or_mismatched_artifacts_exit_prereq(artifact_dir: Path) -> None:
    b = _sql_artifact(artifact_dir, "10:00:00")
    broken = artifact_dir / "20261016T110000Z-sql-broken.json"
    broken.write_text(json.dumps({"cmd": "sql", "timestamp": "2026-10-16T11:00:00+00:00"}))
    with pytest.raises(PrereqError, match="malformed"):
        compare.main(["--base", str(b), "--head", str(broken), "--force"])
    other = artifact_dir / "20261016T120000Z-stages.json"
    other.write_text(json.dumps({"cmd": "stages", "timestamp": "2026-10-16T12:00:00+00:00"}))
    with pytest.raises(PrereqError, match="kind mismatch"):
        compare.main(["--base", str(b), "--head", str(other)])
    garbage = artifact_dir / "garbage-sql.json"
    garbage.write_text("{not json")
    with pytest.raises(PrereqError, match="cannot read"):
        compare.main(["--base", str(garbage)])


def _cpu_artifact(directory: Path, stamp: str, *, concurrency: int = 16) -> Path:
    """What ``make perf-agent-cpu`` writes: knobs under data.args, clean broker."""
    payload = {
        "cmd": "cpu",
        "timestamp": f"2026-10-16T{stamp}+00:00",
        "broker_mode": "clean",
        "variant": {},
        "data": {
            "args": {
                "concurrency": concurrency,
                "duration": 30,
                "caller": "jak",
                "vendor": "perf-noauth",
            },
            "load": {"rps": 400.0, "ok_ratio": 1.0},
            "profile": {"buckets": {"db": 0.4, "http": 0.3}},
        },
    }
    path = directory / f"20261016T{stamp.replace(':', '')}Z-cpu.json"
    path.write_text(json.dumps(payload))
    return path


def _profile_artifact(directory: Path, stamp: str, *, concurrency: int = 16) -> Path:
    """What classic ``make perf-profile`` writes: knobs at top level, no broker_mode."""
    payload = {
        "scenario": "profile",
        "timestamp": f"2026-10-16T{stamp}+00:00",
        "variant": {},
        "vendor": "perf-noauth",
        "caller": "jak",
        "concurrency": concurrency,
        "load": {"rps": 410.0, "ok_ratio": 1.0},
        "profile": {"buckets": {"db": 0.41, "http": 0.3}},
    }
    path = directory / "classic" / f"20261016T{stamp.replace(':', '')}Z-profile.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def test_compare_cpu_with_classic_profile(artifact_dir: Path) -> None:
    cpu = _cpu_artifact(artifact_dir, "10:00:00")
    prof = _profile_artifact(artifact_dir, "11:00:00")
    assert compare.differences(compare.load(cpu)[1], compare.load(prof)[1]) == []
    assert compare.main(["--base", str(cpu), "--head", str(prof)]) == 0
    other = _profile_artifact(artifact_dir, "12:00:00", concurrency=32)
    with pytest.raises(PrereqError, match=r"args\.concurrency: 16 -> 32"):
        compare.main(["--base", str(cpu), "--head", str(other)])


def test_failures_detects_classic_ok_ratio() -> None:
    capacity = {"rows": [{"config": "1x2", "ok_ratio": 0.5}, {"config": "2x4", "ok_ratio": 1.0}]}
    assert compare.failures(capacity) == ["1x2: ok_ratio 0.500"]
    assert compare.failures({"data": {"load": {"ok_ratio": 1.0}}}) == []


def test_failures_honours_classic_valid_flag_and_direct_side() -> None:
    overhead = {
        "rows": [
            {
                "case": "noauth",
                "valid": False,
                "invalid_reasons": ["broker#1: ok_ratio 0.400 < 0.99"],
                "broker": {"ok_ratio": 1.0},
                "direct": {"ok_ratio": 0.9},
            },
            {"case": "bearer", "valid": True, "broker": {"ok_ratio": 1.0}},
        ]
    }
    assert compare.failures(overhead) == [
        "noauth: marked invalid (broker#1: ok_ratio 0.400 < 0.99)",
        "noauth: direct ok_ratio 0.900",
    ]


def test_overhead_rows_without_a_mean_are_not_compared() -> None:
    def run(mean: float | None) -> dict[str, Any]:
        return {"rows": [{"case": "c", "rate": 10, "overhead": {"mean_ms": mean}}]}

    assert compare.m_overhead(run(None), run(2.0)) == []
    assert [m.key for m in compare.m_overhead(run(1.0), run(2.0))] == ["c@10 overhead mean ms"]


# --------------------------------------------------------------------------- suite guards


def test_require_fresh_turns_stale_tokens_into_a_prereq() -> None:
    env = PerfEnv(app_url="a", broker_url="b", upstream_url="u", agent_id="ag")
    env.callers = {"jak": Caller("jak", "jak_x", "ag", expires_at=1.0)}
    with pytest.raises(PrereqError, match="jak caller") as exc:
        base.require_fresh(env, callers=["jak"], run_s=60)
    assert exc.value.fix == "make perf-provision"


def test_require_fresh_says_shorten_a_probe_longer_than_the_token_lifetime() -> None:
    now = time.time()
    env = PerfEnv(app_url="a", broker_url="b", upstream_url="u", agent_id="ag")
    env.callers = {"jak": Caller("jak", "jak_x", "ag", expires_at=now + 600, issued_at=now)}
    with pytest.raises(PrereqError, match="longer than the token lifetime") as exc:
        base.require_fresh(env, callers=["jak"], run_s=3600)
    assert "perf-provision" not in exc.value.fix
    assert "DURATION" in exc.value.fix


def test_run_lock_refuses_while_another_perf_tool_holds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(common, "LOCK_FILE", tmp_path / ".perf.lock")
    with (
        common.exclusive_run("bench_overhead"),
        pytest.raises(PrereqError, match="bench_overhead"),
        base.run_lock("agent-sql"),
    ):
        pytest.fail("ran under someone else's lock")
    with base.run_lock("agent-sql"):
        pass
