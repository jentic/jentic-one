"""Pure logic of the local perf suite (``tests/perf``): no Docker, no stack.

The suite itself only runs on demand against a compose stack; these pin the
parts that decide whether a measurement is written at all — success gating,
token freshness, interleaved A/B, restore-on-exit and the single-run lock —
since a regression there silently produces plausible but wrong numbers.
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

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
    assert row["overhead"]["mean_ms"] == pytest.approx(spread["median"])


def _sample(overhead_ms: float) -> dict[str, Any]:
    return {"overhead": {"mean_ms": overhead_ms}}


def _rank(s: dict[str, Any]) -> float:
    return float(s["overhead"]["mean_ms"])


@pytest.mark.parametrize(
    ("overheads", "median"), [([7.0], 7.0), ([9.0, 1.0, 4.0], 4.0), ([5, 1, 9, 3, 7], 5.0)]
)
def test_median_sample_is_the_middle_repeat(overheads: list[float], median: float) -> None:
    rep = bench_overhead.median_sample([_sample(o) for o in overheads], _rank)
    assert rep["overhead"]["mean_ms"] == median


def test_median_sample_rejects_an_even_number_of_repeats() -> None:
    # With two repeats the lower middle is the minimum: a best-of-two headline.
    with pytest.raises(ValueError, match="odd"):
        bench_overhead.median_sample([_sample(10.0), _sample(12.0)], _rank)


@pytest.mark.parametrize("repeat", ["0", "2", "4"])
def test_main_rejects_an_even_or_zero_repeat(
    repeat: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        bench_overhead.main(["--repeat", repeat])
    assert "odd" in capsys.readouterr().err


# ── token freshness ───────────────────────────────────────────────────────────


def test_check_fresh_refuses_a_caller_that_expires_mid_run() -> None:
    now = 1_000_000.0
    env = _env(at=Caller("at", "at_x", "ag", expires_at=now + 1800))
    with pytest.raises(SystemExit, match="at caller"):
        env.check_fresh(callers=["at"], run_s=1700, now=now)


def test_check_fresh_says_split_the_run_when_it_outlasts_the_token_lifetime() -> None:
    now = 1_000_000.0
    env = _env(at=Caller("at", "at_x", "ag", expires_at=now + 3600, issued_at=now))
    with pytest.raises(common.TokenLifetimeTooShortError) as exc:
        env.check_fresh(callers=["at"], run_s=2 * 3600, now=now)
    assert isinstance(exc.value, SystemExit)
    assert "make perf-provision" not in str(exc.value)
    assert "--only" in str(exc.value)


def test_check_fresh_says_reprovision_when_a_fresh_token_would_fit() -> None:
    now = 1_000_000.0
    issued = now - 3000
    env = _env(at=Caller("at", "at_x", "ag", expires_at=issued + 3600, issued_at=issued))
    with pytest.raises(SystemExit, match="make perf-provision") as exc:
        env.check_fresh(callers=["at"], run_s=1800, now=now)
    assert not isinstance(exc.value, common.TokenLifetimeTooShortError)


def test_perf_config_tokens_outlive_the_full_overhead_matrix_at_repeat_3() -> None:
    env = _env()
    env.vendors["perf-noauth"]["api"] = {
        "vendor": "v", "name": "n", "version": "1", "revision_id": "abc",
    }  # fmt: skip
    rates = (1, 10, 50)  # the CLI default set
    plan = [
        (case, rate)
        for case in bench_overhead.cases(env)
        for rate in bench_overhead.case_rates(case, rates, quick=False)
    ]
    run_s = bench_overhead.estimate_s(plan, _args(duration=20.0, repeat=3))
    needed = run_s + common.TOKEN_MARGIN_S
    config = yaml.safe_load((common.PERF_DIR / "config.perf.yaml").read_text())
    assert config["auth"]["access_ttl_seconds"] > needed
    admin_auth = config["admin"]["auth"]
    assert admin_auth["jwt_ttl_seconds"] > needed
    assert admin_auth["session_ttl_seconds"] >= admin_auth["jwt_ttl_seconds"]


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
    env.admin_issued_at = 100.0
    env.callers["at"] = Caller("at", "at_x", "ag", expires_at=50.0, issued_at=20.0)
    path = tmp_path / "env.json"
    env.save(path)
    loaded = PerfEnv.load(path)
    assert (loaded.admin_issued_at, loaded.admin_expires_at) == (100.0, 123.0)
    assert loaded.callers["at"].issued_at == 20.0


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


def test_dbcost_stats_sql_interpolates_the_cte_aware_write_predicate() -> None:
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


def test_a_rejected_load_deletes_its_flamegraph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pyspy_profile, "OUT_DIR", tmp_path)
    files = [tmp_path / "run.txt", tmp_path / "run.svg"]
    for f in files:
        f.write_text("x")
    keep = tmp_path / "other.svg"
    keep.write_text("x")
    with pytest.raises(pyspy_profile.LoadError):
        pyspy_profile.check_load_or_discard("run", {}, [], alive=False, min_ok=0.99)
    assert not any(f.exists() for f in files)
    assert keep.exists()


def test_a_valid_load_keeps_its_flamegraph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pyspy_profile, "OUT_DIR", tmp_path)
    svg = tmp_path / "run.svg"
    svg.write_text("x")
    good = bench_overhead.summarise(_oha_json({"200": 100}), 200)
    pyspy_profile.check_load_or_discard("run", good, [], alive=False, min_ok=0.99)
    assert svg.exists()


class _FakeRecord:
    """A ``py-spy record`` that writes its output file, then ``wait``s as told."""

    def __init__(self, out_dir: Path, args: str, wait: Any, raw: str) -> None:
        out = Path(args.rsplit("--output ", 1)[1].replace("/out", str(out_dir), 1))
        out.write_text(raw if out.suffix == ".txt" else "<svg/>")
        self._wait = wait

    def wait(self, timeout: float) -> int:
        result = self._wait()
        assert isinstance(result, int)
        return result


def _run_pyspy_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, wait: Any, raw: str = "a;b 3"
) -> list[Path]:
    """Drive ``pyspy_profile.main`` with Docker, oha and py-spy faked out.

    Returns the files left in the (temporary) ``results/pyspy`` directory.
    """
    out_dir = tmp_path / "pyspy"
    out_dir.mkdir()
    env = _env()
    monkeypatch.setattr(pyspy_profile, "OUT_DIR", out_dir)
    monkeypatch.setattr(PerfEnv, "load", classmethod(lambda cls, path=None: env))
    monkeypatch.setattr(pyspy_profile, "exclusive_run", lambda tool: contextlib.nullcontext())
    for name in ("stop_oha", "stop_pyspy", "start_oha", "start_pyspy"):
        monkeypatch.setattr(pyspy_profile, name, lambda *a: None)
    monkeypatch.setattr(pyspy_profile, "broker_container", lambda: "cid")
    monkeypatch.setattr(pyspy_profile, "run_oha", lambda a: _oha_json({"200": 100}))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(pyspy_profile, "run_metadata", lambda scenario: {})
    monkeypatch.setattr(
        pyspy_profile, "write_result", lambda scenario, payload: tmp_path / "profile.json"
    )
    monkeypatch.setattr(pyspy_profile, "record", lambda args: _FakeRecord(out_dir, args, wait, raw))
    try:
        pyspy_profile.main(["--duration", "1"])
    finally:
        left = sorted(out_dir.iterdir())
    return left


def _raise(exc: BaseException) -> Any:
    def wait() -> int:
        raise exc

    return wait


@pytest.mark.parametrize(
    ("wait", "raw", "error"),
    [
        (_raise(subprocess.TimeoutExpired("py-spy", 61)), "a;b 3", subprocess.TimeoutExpired),
        (_raise(KeyboardInterrupt()), "a;b 3", KeyboardInterrupt),
        (lambda: 1, "a;b 3", RuntimeError),
        (lambda: 0, "a;b 3\ntruncat", ValueError),
    ],
    ids=["timeout", "ctrl-c", "record-failed", "analyse-failed"],
)
def test_every_failed_profile_run_deletes_its_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wait: Any,
    raw: str,
    error: type[BaseException],
) -> None:
    out_dir = tmp_path / "pyspy"
    with pytest.raises(error):
        _run_pyspy_main(tmp_path, monkeypatch, wait=wait, raw=raw)
    assert list(out_dir.iterdir()) == []


def test_a_completed_profile_run_keeps_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    left = _run_pyspy_main(tmp_path, monkeypatch, wait=lambda: 0)
    assert [p.suffix for p in left] == [".svg", ".txt"]


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
