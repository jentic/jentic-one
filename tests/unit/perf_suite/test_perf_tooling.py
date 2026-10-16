"""Pure logic of the local perf suite (``tests/perf``): no Docker, no stack.

The suite itself only runs on demand against a compose stack; these pin the
parts that decide whether a measurement is written at all — success gating,
token freshness, interleaved A/B, restore-on-exit and the single-run lock —
since a regression there silently produces plausible but wrong numbers.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import pytest

from tests.perf import bench_overhead, common, dbcost, provision, pyspy_profile, report
from tests.perf.bench_overhead import Case
from tests.perf.common import Caller, PerfEnv, Vendor


def _oha_json(codes: dict[str, int], *, mean_s: float = 0.01) -> dict[str, Any]:
    return {
        "summary": {"average": mean_s, "slowest": mean_s * 2, "requestsPerSec": 10.0},
        "latencyPercentiles": {"p50": mean_s, "p90": mean_s, "p99": mean_s, "p99.9": mean_s},
        "statusCodeDistribution": codes,
        "errorDistribution": {},
    }


def _env(**callers: Caller) -> PerfEnv:
    env = PerfEnv(app_url="a", broker_url="http://broker:8000", upstream_url="u", agent_id="ag")
    env.callers = {"jak": Caller("jak", "jak_x", "ag"), **callers}
    env.vendors = {"perf-noauth": {"alias": "smoke-noauth", "credential_type": "no_auth"}}
    return env


# ── summarise / check_ok ──────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["summary", "latencyPercentiles"])
def test_summarise_raises_when_a_section_is_missing(missing: str) -> None:
    raw = _oha_json({"200": 5})
    del raw[missing]
    with pytest.raises(bench_overhead.OhaOutputError):
        bench_overhead.summarise(raw, 200)


def test_summarise_raises_when_a_summary_field_is_missing() -> None:
    raw = _oha_json({"200": 5})
    del raw["summary"]["average"]
    with pytest.raises(bench_overhead.OhaOutputError, match="average"):
        bench_overhead.summarise(raw, 200)


def test_check_ok_rejects_a_mostly_401_run() -> None:
    summary = bench_overhead.summarise(_oha_json({"200": 10, "401": 90}), 200)
    why = bench_overhead.check_ok(summary, 0.99, "broker")
    assert why is not None
    assert "401" in why


def test_check_ok_accepts_an_all_success_run() -> None:
    summary = bench_overhead.summarise(_oha_json({"202": 50}), 202)
    assert bench_overhead.check_ok(summary, 0.99, "broker") is None


# ── run_case: interleaving, gating, spread ────────────────────────────────────


def _args(**over: Any) -> argparse.Namespace:
    base = {"min_requests": 50, "duration": 1.0, "repeat": 1, "min_ok": 0.99}
    return argparse.Namespace(**{**base, **over})


def test_run_case_marks_a_failing_broker_run_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_oha(url: str, case: Case, rate: int, n: int, token: str | None, **_: Any) -> Any:
        return _oha_json({"401": n} if url.startswith("http://broker") else {"200": n})

    monkeypatch.setattr(bench_overhead, "oha", fake_oha)
    row = bench_overhead.run_case(_env(), Case("c"), 10, _args(), warm_n=20)
    assert row["valid"] is False
    assert any(r.startswith("broker#0") for r in row["invalid_reasons"])


def test_run_case_measures_direct_right_before_broker_each_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    broker_means = iter([0.030, 0.010, 0.020])

    def fake_oha(url: str, case: Case, rate: int, n: int, token: str | None, **_: Any) -> Any:
        side = "broker" if url.startswith("http://broker") else "direct"
        if n == 20 or rate != 10:  # warm-up runs
            return _oha_json({"200": n})
        calls.append(side)
        mean = next(broker_means) if side == "broker" else 0.005
        return _oha_json({"200": n}, mean_s=mean)

    monkeypatch.setattr(bench_overhead, "oha", fake_oha)
    row = bench_overhead.run_case(_env(), Case("c"), 10, _args(repeat=3), warm_n=20)

    assert calls == ["direct", "broker"] * 3
    assert row["valid"] is True
    assert row["repeats"] == 3
    assert row["direct_measured_at"] <= row["broker_measured_at"]
    spread = row["overhead_mean_spread"]
    assert spread["min"] == pytest.approx(5.0)
    assert spread["median"] == pytest.approx(15.0)
    assert spread["max"] == pytest.approx(25.0)
    # Headline numbers come from the median repeat, not the first or last.
    assert row["overhead"]["mean_ms"] == pytest.approx(15.0)


# ── token freshness ───────────────────────────────────────────────────────────


def test_check_fresh_refuses_a_caller_that_expires_mid_run() -> None:
    now = 1_000_000.0
    env = _env(at=Caller("at", "at_x", "ag", expires_at=now + 1800))
    with pytest.raises(SystemExit, match="at caller"):
        env.check_fresh(callers=["at"], run_s=1700, now=now)


def test_check_fresh_ignores_callers_the_run_does_not_use() -> None:
    now = 1_000_000.0
    env = _env(at=Caller("at", "at_x", "ag", expires_at=now + 10))
    env.check_fresh(callers=["jak"], run_s=3600, now=now)


def test_check_fresh_treats_an_unknown_admin_expiry_as_stale() -> None:
    env = _env()
    with pytest.raises(SystemExit, match="admin token"):
        env.check_fresh(admin=True, now=time.time())


def test_perf_env_round_trips_admin_expiry(tmp_path: Path) -> None:
    env = _env()
    env.admin_expires_at = 123.0
    path = tmp_path / "env.json"
    env.save(path)
    assert PerfEnv.load(path).admin_expires_at == 123.0


# ── restore-on-exit and the variant state ─────────────────────────────────────


def test_restore_runs_every_step_and_keeps_the_body_exception() -> None:
    ran: list[str] = []

    def boom() -> None:
        ran.append("boom")
        raise RuntimeError("restore step failed")

    with (
        pytest.raises(KeyboardInterrupt),
        common.restore_on_exit(("a", boom), ("b", lambda: ran.append("b"))),
    ):
        raise KeyboardInterrupt
    assert ran == ["boom", "b"]


def test_restore_failure_after_a_clean_body_is_raised() -> None:
    def boom() -> None:
        raise RuntimeError("nope")

    with (
        pytest.raises(RuntimeError, match="restore failed: a"),
        common.restore_on_exit(("a", boom)),
    ):
        pass


def test_interrupted_variant_switch_is_still_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[str] = []

    def fake_apply(name: str, current: str | None) -> str:
        applied.append(name)
        if name == "buffered":
            raise KeyboardInterrupt
        return name

    monkeypatch.setattr(bench_overhead, "apply_variant", fake_apply)
    state: dict[str, str | None] = {"variant": "default"}
    with pytest.raises(KeyboardInterrupt):
        bench_overhead.switch_variant(state, "buffered")
    assert state["variant"] == bench_overhead.UNKNOWN_VARIANT
    bench_overhead.restore_default(state)
    assert applied == ["buffered", "default"]
    assert state["variant"] == "default"


def test_restore_default_is_a_no_op_when_the_broker_never_left_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bench_overhead, "apply_variant", lambda *_: pytest.fail("recreated"))
    for variant in (None, "default"):
        bench_overhead.restore_default({"variant": variant})


# ── single-run lock ───────────────────────────────────────────────────────────


def test_exclusive_run_refuses_a_second_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(common, "LOCK_FILE", tmp_path / ".perf.lock")
    with (
        common.exclusive_run("first"),
        pytest.raises(SystemExit, match="first"),
        common.exclusive_run("second"),
    ):
        pass
    with common.exclusive_run("third"):
        pass


# ── provision.verify injection checks ─────────────────────────────────────────


def test_injection_error_flags_a_missing_bearer_credential() -> None:
    vendor = Vendor("perf-bearer", "smoke-bearer", "bearer_token")
    assert provision.injection_error(vendor, {"headers": {}}) is not None
    echo = {"headers": {"Authorization": "Bearer perf-bearer-abc"}}
    assert provision.injection_error(vendor, echo) is None


def test_injection_error_checks_query_api_keys() -> None:
    vendor = Vendor("perf-apikey-qry", "smoke-apikey-qry", "api_key", "query")
    assert provision.injection_error(vendor, {"query_params": {}}) is not None
    echo = {"query_params": {"api_key": "perf-key-1"}}  # pragma: allowlist secret
    assert provision.injection_error(vendor, echo) is None


# ── dbcost: per-call counts need every call to succeed ────────────────────────


def test_dbcost_measure_aborts_unless_every_call_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dbcost, "oha", lambda *a, **k: _oha_json({"200": 190, "429": 10}))
    monkeypatch.setattr(dbcost, "reset", lambda: None)
    monkeypatch.setattr(dbcost, "snapshot", lambda: {})
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(dbcost.IncompleteRunError, match="190/200"):
        dbcost.measure(_env(), "warm", "jak", n=200, rate=10)


def test_dbcost_write_predicate_counts_cte_writes() -> None:
    assert "WITH" in dbcost.WRITE_PREDICATE
    assert "{WRITE_PREDICATE}" not in dbcost.STATS_SQL


# ── profile: the load must have run for the whole window ──────────────────────


def test_check_load_rejects_a_failed_or_unfinished_or_failing_load() -> None:
    good = bench_overhead.summarise(_oha_json({"200": 100}), 200)
    bad = bench_overhead.summarise(_oha_json({"200": 50, "503": 50}), 200)
    with pytest.raises(pyspy_profile.LoadError, match="failed"):
        pyspy_profile.check_load({}, [RuntimeError("oha died")], alive=False, min_ok=0.99)
    with pytest.raises(pyspy_profile.LoadError, match="still running"):
        pyspy_profile.check_load(good, [], alive=True, min_ok=0.99)
    with pytest.raises(pyspy_profile.LoadError, match="no result"):
        pyspy_profile.check_load({}, [], alive=False, min_ok=0.99)
    with pytest.raises(pyspy_profile.LoadError, match="ok_ratio"):
        pyspy_profile.check_load(bad, [], alive=False, min_ok=0.99)
    pyspy_profile.check_load(good, [], alive=False, min_ok=0.99)


# ── report ────────────────────────────────────────────────────────────────────


def test_report_skips_invalid_rows_and_tolerates_a_missing_rps() -> None:
    run = {"rows": [{"case": "a", "valid": False}, {"case": "b"}, {"case": "c", "valid": True}]}
    assert [r["case"] for r in report.valid_rows(run)] == ["b", "c"]
    assert report._rps({"load": {"rps": None}}) == "n/a"
    assert report._rps({}) == "n/a"


def test_report_pins_plotly_with_subresource_integrity() -> None:
    page = report.render([], "")
    assert f'integrity="{report.PLOTLY_SRI}"' in page
    assert 'crossorigin="anonymous"' in page
