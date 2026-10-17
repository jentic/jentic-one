"""Unit tests for the pure analysers behind ``make perf-agent-{sql,stages,resilience}``."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.perf import pyspy_profile
from tests.perf.agent import __main__ as agent_main
from tests.perf.agent import base, sqltrace, stages
from tests.perf.agent.base import PrereqError
from tests.perf.common import Caller, PerfEnv

HOOK_PATH = Path(sqltrace.__file__).parent / "hooks" / "sitecustomize.py"


def _ev(rid: str, ev: str, **kw: Any) -> dict[str, Any]:
    return {"rid": rid, "ev": ev, "plane": kw.pop("plane", "control"), "conn": 1, "t": 0.0, **kw}


def _request(rid: str) -> list[dict[str, Any]]:
    return [
        _ev(rid, "checkout"),
        _ev(rid, "ping"),
        _ev(rid, "begin"),
        _ev(
            rid,
            "sql",
            sql="SELECT * FROM t WHERE id IN ($1, $2, $3)",
            rows=0,
            ms=1.0,
            params=["secret-a", "b", "c"],
            site="src/jentic_one/x.py:1 f",
        ),
        _ev(rid, "commit"),
        _ev(rid, "checkin"),
    ]


def test_normalize_collapses_parameter_lists() -> None:
    assert sqltrace.normalize("SELECT 1 FROM t WHERE a IN ($1, $2::uuid,\n $3) AND b = $4") == (
        "SELECT 1 FROM t WHERE a IN ($n…) AND b = $n"
    )


def test_plane_table_counts_zero_row_selects_and_readonly_commits() -> None:
    reqs = sqltrace.by_request(_request("r1") + _request("r2"))
    (row,) = sqltrace.plane_table(reqs)
    assert row["plane"] == "control"
    assert row["sessions"] == row["pings"] == row["transactions"] == row["statements"] == 1
    assert row["zero_row_selects"] == 1
    assert row["readonly_commits"] == 1
    assert row["writes"] == 0


def test_artifact_views_never_carry_bind_parameters() -> None:
    evs = _request("r1")
    groups = sqltrace.statement_groups(sqltrace.by_request(evs))
    visible = [{k: v for k, v in g.items() if k != "_params"} for g in groups]
    assert "secret-a" not in json.dumps(visible)
    assert "secret-a" not in json.dumps(sqltrace.strip_params(evs))
    assert groups[0]["_params"] == ["secret-a", "b", "c"]


@pytest.mark.parametrize(
    ("sql", "params", "reason"),
    [
        ("SELECT 1 WHERE a = $1", ["x"], ""),
        ("WITH d AS (DELETE FROM t RETURNING id) SELECT * FROM d", [], "data-modifying CTE"),
        ("WITH c AS (SELECT 1) SELECT * FROM c", [], ""),
        ("SELECT 1 WHERE a = $1", [{"truncated": True}], "too long"),
        ("SELECT 1 WHERE a = $1", None, "not captured"),
        ("UPDATE t SET a = 1", [], "not a SELECT"),
    ],
)
def test_explain_skip_reason(sql: str, params: list[Any] | None, reason: str) -> None:
    ptypes = ["str"] if "$1" in sql else []
    group = {"sample": {"sql": sql, "ptypes": ptypes}, "_params": params}
    got = sqltrace.explain_skip_reason(group)
    assert (reason in got) if reason else got == ""


def test_literal_round_trips_awkward_values() -> None:
    assert sqltrace._literal("o'brien") == "'o''brien'"
    assert sqltrace._literal(["a,b", 'q"x', None]) == '\'{"a,b","q\\"x",NULL}\''
    assert sqltrace._literal({"bytes_hex": "00ff"}) == "'\\x00ff'::bytea"
    assert sqltrace._literal(True) == "true"
    assert sqltrace.pg_type("list[UUID]") == "uuid[]"
    assert sqltrace.pg_type("list") == "text[]"


def _secret_request(rid: str) -> list[dict[str, Any]]:
    """One request whose SELECT carries values a custom plan would print."""
    return [
        _ev(rid, "checkout"),
        _ev(
            rid,
            "sql",
            sql="SELECT * FROM t WHERE key_hash = $1 AND id = ANY($2)",
            ptypes=["str", "list[str]"],
            rows=1,
            ms=2.0,
            params=["hash-secret-1", ["cred-secret-2", "b"]],
            site="src/jentic_one/x.py:1 f",
        ),
        _ev(rid, "checkin"),
    ]


def test_sql_explain_never_writes_bind_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scripts: list[str] = []

    def fake_compose(*args: str, **_: Any) -> str:
        scripts.append(args[-1])
        # A plan that echoes the values (what a custom plan prints), to prove masking.
        return (
            "QUERY PLAN\n-----\n"
            "Index Scan using ix_key_hash on t (actual rows=1 loops=1)\n"
            "  Index Cond: (key_hash = 'hash-secret-1'::text)\n"
            "  Filter: (id = ANY ('{cred-secret-2,b}'::text[]))\n"
            "  Note: hash-secret-1\n"
            "(4 rows)\n"
        )

    events = _secret_request("p-1") + _secret_request("p-2")
    monkeypatch.setattr(sqltrace, "compose", fake_compose)
    monkeypatch.setattr(sqltrace, "broker_variant", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(sqltrace, "drive", lambda *a: ("p-", events, {"200": 2}))
    monkeypatch.setattr(sqltrace, "hook_manifest", lambda: {"patched": [], "failed": []})
    monkeypatch.setattr(sqltrace, "require_patched", lambda *a: None)
    monkeypatch.setattr(base, "AGENT_DIR", tmp_path)

    assert sqltrace.main(["-n", "2", "--explain", "1"]) == 0
    assert "SET LOCAL plan_cache_mode = force_generic_plan" in scripts[0]
    (artifact,) = tmp_path.glob("*-sql.json")
    written = artifact.read_text() + capsys.readouterr().out
    assert "Index Cond: (key_hash = '…'::text)" in written
    for secret in ("hash-secret-1", "cred-secret-2"):
        assert secret not in written


def test_mask_plan_masks_literals_and_captured_values_in_errors() -> None:
    err = "ERROR: invalid input syntax for type uuid: abcd-secret at character 9"
    assert sqltrace.mask_plan(err, [["abcd-secret"], 7, None]) == (
        "ERROR: invalid input syntax for type uuid: … at character 9"
    )
    assert sqltrace.mask_plan("x = 'o''brien' AND y = $1", []) == "x = '…' AND y = $1"


def test_stages_analyse_counts_per_request_and_ignores_lag_context() -> None:
    handler = stages.HANDLER
    events: list[dict[str, Any]] = [
        {"ev": "stage", "rid": "r1", "name": handler, "ms": 10.0},
        {"ev": "stage", "rid": "r1", "name": "x:discover", "ms": 2.0},
        {"ev": "stage", "rid": "r2", "name": handler, "ms": 20.0},
        {"ev": "stage", "rid": "orphan", "name": "x:discover", "ms": 9.0},
        {"ev": "lag", "rid": "", "samples_ms": [0.0, 1.0, 6.0]},
    ]
    out = stages.analyse(events)
    assert out["requests"] == 2
    assert out["handler_mean_ms"] == 15.0
    discover = next(s for s in out["stages"] if s["stage"] == "x:discover")
    assert discover["calls_per_req"] == 0.5
    assert out["loop_lag"]["samples"] == 3


def test_group_logs_keeps_only_whitelisted_fields() -> None:
    events = [
        {
            "level": "error",
            "event": "unhandled_exception",
            "request_id": "r1",
            "authorization": "Bearer jak_secret",
            "body": "payload",
            "exception": 'File "/app/src/jentic_one/broker/x.py", line 9, in f\nBoom',
        }
    ]
    (row,) = agent_main.group_logs(events)
    assert row["where"] == "src/jentic_one/broker/x.py:9 f"
    assert set(row["sample"]) == {"level", "event", "request_id", "exception"}
    assert "jak_secret" not in json.dumps(row)


def test_read_csv_handles_quoted_commas_and_escaped_quotes(tmp_path: Path) -> None:
    path = tmp_path / "failures.csv"
    path.write_text('Method,Name,Error,Occurrences\nPOST,/x,"bad, ""quoted"" thing",3\n')
    assert agent_main.read_csv(path) == [
        {"Method": "POST", "Name": "/x", "Error": 'bad, "quoted" thing', "Occurrences": "3"}
    ]


def test_cpu_analysers_survive_a_torn_frame_name_without_corrupting_the_others(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "profile.txt"
    ours = "handle (/app/src/jentic_one/broker/web/app.py:42)"
    raw.write_bytes(
        b"run (asyncio/runners.py:118);"
        + ours.encode()
        + b";execute (sqlalchemy/engine/base.py:7) 3\n"
        # The 0xff lands inside the leaf frame name, as a torn read would.
        + ours.encode()
        + b";gar\xffbled (x.py:1) 2\n"
    )
    summary = pyspy_profile.analyse(raw)
    assert summary["samples"] == 5
    assert summary["buckets"] == {"db: other sqlalchemy/asyncpg": 0.6, "other": 0.4}
    top = {r["frame"]: r["share"] for r in summary["top_self"]}
    assert top["execute (sqlalchemy/engine/base.py:7)"] == 0.6
    assert top["gar\ufffdbled (x.py:1)"] == 0.4
    hot = agent_main.line_hotspots(raw)
    assert hot["inclusive"] == [
        {"frame": "src/jentic_one/broker/web/app.py:42 handle", "pct": 100.0}
    ]


def test_broker_env_drift_reports_traced_or_variant_broker() -> None:
    expected = {"PYTHONPATH": "", "PERF_TRACE": "", "JENTIC__X": "true", "EMPTY": None}
    actual = {"PYTHONPATH": "/perf-hooks", "PERF_TRACE": "sql", "JENTIC__X": "true"}
    drift = agent_main.broker_env_drift(expected, actual)
    assert [d.split("=")[0] for d in drift] == ["PERF_TRACE", "PYTHONPATH"]
    assert agent_main.broker_env_drift(expected, {"JENTIC__X": "true"}) == []


@pytest.fixture()
def hook(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The trace hook, loaded inert (no PERF_TRACE, so it installs nothing)."""
    monkeypatch.delenv("PERF_TRACE", raising=False)
    spec = importlib.util.spec_from_file_location("perf_trace_hook_under_test", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_param_capture_is_faithful_or_marked(hook: ModuleType) -> None:
    uid = uuid.UUID(int=1)
    assert hook._param_value(uid) == str(uid)
    assert hook._param_value(["a,b", 1, None]) == ["a,b", 1, None]
    assert hook._param_value(b"\x00\xff") == {"bytes_hex": "00ff"}
    assert hook._param_value("x" * (hook.PARAM_MAX_CHARS + 1)) == {"truncated": True}
    assert hook._param_value(["x" * (hook.PARAM_MAX_CHARS + 1)]) == {"truncated": True}
    assert hook._param_type([uid, uid]) == "list[UUID]"
    assert hook._param_type([]) == "list"


def test_hook_listeners_never_raise(hook: ModuleType) -> None:
    def broken(*_: Any) -> None:
        raise AttributeError("invalidated connection")

    hook._guarded(broken)("conn")
    assert hook._stats["errors"] == 1


@pytest.fixture()
def failing_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A trace dir holding a trace file, and a broker whose clean restore fails."""
    monkeypatch.setattr(base, "TRACE_DIR", tmp_path)

    def recreate(overrides: dict[str, str]) -> None:
        (tmp_path / "1.jsonl").write_text('{"params": ["secret"]}\n')
        if not overrides.get("PERF_TRACE"):
            raise PrereqError("broker did not come up: clean", "make perf-up")

    monkeypatch.setattr(base, "recreate_broker", recreate)
    return tmp_path


def test_broker_variant_failed_restore_keeps_body_error_and_purges(failing_restore: Path) -> None:
    with pytest.raises(ValueError, match="probe failed"), base.broker_variant({}, trace="sql"):
        raise ValueError("probe failed")
    assert list(failing_restore.glob("*.jsonl")) == []


def test_broker_variant_failed_restore_after_success_is_a_prereq(failing_restore: Path) -> None:
    with (
        pytest.raises(PrereqError, match="restore failed: restore clean broker"),
        base.broker_variant({}, trace="sql"),
    ):
        pass
    assert list(failing_restore.glob("*.jsonl")) == []


def test_cmd_cpu_capture_runtime_error_is_a_prereq(monkeypatch: pytest.MonkeyPatch) -> None:
    def capture(**_: Any) -> None:
        raise RuntimeError("py-spy record failed: exit codes [1]")

    monkeypatch.setattr(pyspy_profile, "capture", capture)
    with pytest.raises(PrereqError, match="cpu: py-spy record failed"):
        agent_main.cmd_cpu([])


def _cpu_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str) -> list[Path]:
    """Fake a captured ``agent cpu`` profile under a temporary repo root."""
    out_dir = tmp_path / "pyspy"
    out_dir.mkdir()
    files = [out_dir / "run.txt", out_dir / "run.svg"]
    files[0].write_text(raw)
    files[1].write_text("<svg/>")
    monkeypatch.setattr(agent_main, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(pyspy_profile, "OUT_DIR", out_dir)
    monkeypatch.setattr(pyspy_profile, "capture", lambda **_: ("run", {"ok_ratio": 1.0}))
    return files


def _finish_raising(exc: BaseException) -> Any:
    def finish(self: base.Report) -> Path:
        raise exc

    return finish


@pytest.mark.parametrize(
    ("raw", "finish", "error"),
    [
        ("a;b 3\ntruncat", lambda self: Path("unused"), ValueError),
        ("a;b 3", _finish_raising(OSError("disk full")), OSError),
        ("a;b 3", _finish_raising(KeyboardInterrupt()), KeyboardInterrupt),
    ],
    ids=["analysis-failed", "artifact-write-failed", "ctrl-c-during-write"],
)
def test_cmd_cpu_deletes_the_profile_when_it_fails_before_the_artifact_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    finish: Any,
    error: type[BaseException],
) -> None:
    files = _cpu_profile(tmp_path, monkeypatch, raw)
    monkeypatch.setattr(base.Report, "finish", finish)
    with pytest.raises(error):
        agent_main.cmd_cpu([])
    assert not any(f.exists() for f in files)


def test_cmd_cpu_keeps_the_profile_once_its_artifact_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _cpu_profile(tmp_path, monkeypatch, "a;b 3")
    written: list[dict[str, Any]] = []

    def finish(self: base.Report) -> Path:
        written.append(self.data)
        return tmp_path / "cpu.json"

    monkeypatch.setattr(base.Report, "finish", finish)
    assert agent_main.cmd_cpu([]) == 0
    assert all(f.exists() for f in files)
    assert written[0]["flamegraph"] == "pyspy/run.svg"


def test_cmd_cpu_says_lower_duration_when_the_run_outlasts_the_token_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Drives the real capture() up to its check_fresh, so the message is the one
    # agent cpu users see: it must name only knobs agent cpu has.
    now = time.time()
    env = PerfEnv(
        app_url="a",
        broker_url="b",
        upstream_url="u",
        agent_id="ag",
        callers={"jak": Caller("jak", "jak_x", "ag", expires_at=now + 60, issued_at=now)},
        vendors={"perf-noauth": {"alias": "noauth"}},
    )
    monkeypatch.setattr(PerfEnv, "load", classmethod(lambda cls, path=None: env))
    with pytest.raises(PrereqError) as exc:
        agent_main.cmd_cpu([])
    assert "--duration" in str(exc.value)
    assert not {"--only", "--repeat"} & set(str(exc.value).split())
    assert "DURATION" in exc.value.fix
    assert "doctor" not in exc.value.fix
