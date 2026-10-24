"""Shared-golden contract tests: the mount's held poll payload vs the CLI's.

The frozen cases under ``cli/tests/golden/testdata/held_poll`` hold a held
execution as the admin service sees it (``view``), the control-plane read the
CLI receives for it (``detail``, plus the ``/result`` document once the call
finished) and the ``get_execution_result`` payload both surfaces answer. A case
may also cap the result size (``max_result_bytes``) or fail the ``/result``
read (``result_fetch_error``; the payload's ``result_error`` is then the prefix
of the message). The Go side replays ``detail``/``result`` through its MCP tool
(``cli/internal/cli/api/mcp_held_golden_test.go``); this replays ``view``
through the held-executions route and the mount's own payload assembly
(``held_result_wanted`` deciding the ``/result`` read, ``held_poll_payload``
attaching its outcome) — one source of truth, so a drift on either side fails
against the identical file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from jentic_one.admin.services.schemas.held_executions import HeldExecutionDetailView
from jentic_one.admin.services.schemas.jobs import JobResultView
from jentic_one.admin.web.routers.held_executions import _detail_response
from jentic_one.mcp import execute as ex
from jentic_one.mcp.tools import held_poll_payload, held_result_wanted

_GOLDEN_DIR = (
    Path(__file__).resolve().parents[3] / "cli" / "tests" / "golden" / "testdata" / "held_poll"
)
_BASE_URL = "https://jentic.example"
_CASES = sorted(_GOLDEN_DIR.glob("*.json"))


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("jentic.example", 443),
            "path": "/",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", b"jentic.example")],
        }
    )


def _load(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return doc


def test_goldens_exist() -> None:
    assert _CASES, f"no held-poll goldens under {_GOLDEN_DIR}"


@pytest.mark.parametrize("path", _CASES, ids=lambda p: p.stem)
def test_detail_route_answers_the_golden_read(path: Path) -> None:
    golden = _load(path)
    view = HeldExecutionDetailView.model_validate(golden["view"])
    detail = _detail_response(view, _request()).model_dump(mode="json", by_alias=True)
    assert detail == golden["detail"]


def _result_view(view: HeldExecutionDetailView, result: dict[str, Any]) -> JobResultView:
    """The ``/result`` document of a golden as the admin service reads it."""
    raw = result["raw"].encode() if "raw" in result else json.dumps(result["body"]).encode()
    return JobResultView(
        id="jres_1",
        job_id=view.job_id,
        kind="execution",
        body=result.get("body", {}),
        content_type=result["content_type"],
        raw_body=raw,
        created_at=datetime(2026, 10, 6, 12, 6, tzinfo=UTC),
    )


@pytest.mark.parametrize("path", _CASES, ids=lambda p: p.stem)
def test_poll_payload_matches_the_golden(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    golden = _load(path)
    if "max_result_bytes" in golden:
        monkeypatch.setattr(ex, "MAX_RESULT_BYTES", golden["max_result_bytes"])
    view = HeldExecutionDetailView.model_validate(golden["view"])
    result = golden["result"]
    fetch_error = bool(golden.get("result_fetch_error"))

    wanted = held_result_wanted(view)
    assert wanted == (result is not None or fetch_error), "the /result read the golden implies"
    fetched: JobResultView | Exception | None = None
    if wanted:
        fetched = RuntimeError("boom") if fetch_error else _result_view(view, result)
    payload = held_poll_payload(view, _BASE_URL, fetched)

    want = dict(golden["payload"])
    if fetch_error:
        assert payload.pop("result_error").startswith(want.pop("result_error"))
    assert payload == want
