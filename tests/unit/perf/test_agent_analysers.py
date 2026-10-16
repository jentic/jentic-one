"""Unit tests for the pure analysers behind ``make perf-agent-{sql,stages,resilience}``."""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.perf.agent import __main__ as agent_main
from tests.perf.agent import sqltrace, stages

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
