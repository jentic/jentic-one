"""Free-text `?q=` filter on GET /traces and GET /jobs.

Powers the Monitor's search input. Substring match (case-insensitive via
SQLite default LIKE collation) across the columns the row renders, so
typing `github` matches whether it landed in `api_id`, `operation_id`,
`workflow_id`, or `agent_id` (traces) / `slug_or_id`, `agent_id`,
`toolkit_id`, `upstream_job_url` (jobs).

Whitespace-only inputs are treated as not set so the no-filter plan
stays cheap — the UI sends `q=` whenever the user clears the search.
"""

import sqlite3
import uuid

import pytest
from src.db import DB_PATH
from src.routers.traces import write_trace


@pytest.fixture
def cleanup_q_rows():
    yield
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("DELETE FROM jobs WHERE id LIKE 'job_q_%'")
        cx.execute("DELETE FROM executions WHERE id LIKE 'exec_q_%'")
        cx.commit()


@pytest.mark.asyncio
async def test_traces_q_matches_api_id_substring(admin_client, cleanup_q_rows):  # noqa: ARG001
    target = f"exec_q_{uuid.uuid4().hex[:8]}"
    other = f"exec_q_{uuid.uuid4().hex[:8]}"

    await write_trace(
        trace_id=target,
        toolkit_id="default",
        operation_id="GET/api.github.com/repos/{owner}/{repo}",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=10,
        error=None,
        api_id="github.com",
    )
    await write_trace(
        trace_id=other,
        toolkit_id="default",
        operation_id="GET/api.stripe.com/v1/charges",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=10,
        error=None,
        api_id="stripe.com",
    )

    resp = admin_client.get("/traces?q=github&limit=500")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["traces"]}
    assert target in ids
    assert other not in ids


@pytest.mark.asyncio
async def test_traces_q_matches_workflow_id(admin_client, cleanup_q_rows):  # noqa: ARG001
    """Workflow trace where the substring lives only in workflow_id."""
    wf = f"exec_q_{uuid.uuid4().hex[:8]}"
    await write_trace(
        trace_id=wf,
        toolkit_id="default",
        operation_id=None,
        workflow_id="wf_review_pr",
        spec_path="github.com/review.arazzo.json",
        status="success",
        http_status=None,
        duration_ms=900,
        error=None,
    )

    resp = admin_client.get("/traces?q=review_pr&limit=500")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["traces"]}
    assert wf in ids


@pytest.mark.asyncio
async def test_traces_q_is_case_insensitive(admin_client, cleanup_q_rows):  # noqa: ARG001
    """SQLite LIKE is case-insensitive for ASCII by default — verify."""
    target = f"exec_q_{uuid.uuid4().hex[:8]}"
    await write_trace(
        trace_id=target,
        toolkit_id="default",
        operation_id="GET/api.github.com/repos/{owner}/{repo}",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=10,
        error=None,
        api_id="github.com",
    )

    resp = admin_client.get("/traces?q=GitHub&limit=500")
    ids = {r["id"] for r in resp.json()["traces"]}
    assert target in ids


@pytest.mark.asyncio
async def test_traces_q_blank_string_is_ignored(admin_client, cleanup_q_rows):  # noqa: ARG001
    """Whitespace-only `q` short-circuits — same plan as no `q` at all.

    The UI sends `q=` (or `q=   `) on clear; we don't want that to silently
    filter to zero rows on a string the SQL would happily LIKE against.
    """
    target = f"exec_q_{uuid.uuid4().hex[:8]}"
    await write_trace(
        trace_id=target,
        toolkit_id="default",
        operation_id="GET/api.github.com/zen",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=10,
        error=None,
    )

    # `q=%20%20` (two spaces) — pydantic min_length=1 lets it through; the
    # router's .strip() then treats it as unset.
    resp = admin_client.get("/traces?q=%20%20&limit=500")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["traces"]}
    assert target in ids


@pytest.mark.asyncio
async def test_jobs_q_matches_slug_or_upstream_url(admin_client, cleanup_q_rows):  # noqa: ARG001
    target_slug = f"job_q_{uuid.uuid4().hex[:8]}"
    target_upstream = f"job_q_{uuid.uuid4().hex[:8]}"
    other = f"job_q_{uuid.uuid4().hex[:8]}"

    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status, created_at)
               VALUES (?, 'workflow', 'wf_github_review', 'default', 'complete',
                       strftime('%s','now'))""",
            (target_slug,),
        )
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status,
                                 upstream_job_url, created_at)
               VALUES (?, 'broker', 'POST /api.example.com/render', 'default',
                       'upstream_async',
                       'https://example.com/jobs/zzz-github-99', strftime('%s','now'))""",
            (target_upstream,),
        )
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status, created_at)
               VALUES (?, 'broker', 'POST /api.stripe.com/charges', 'default', 'complete',
                       strftime('%s','now'))""",
            (other,),
        )
        cx.commit()

    resp = admin_client.get("/jobs?q=github&limit=100")
    assert resp.status_code == 200
    ids = {row["job_id"] for row in resp.json()["data"]}
    assert target_slug in ids
    assert target_upstream in ids
    assert other not in ids


@pytest.mark.asyncio
async def test_jobs_q_matches_toolkit_and_agent(admin_client, cleanup_q_rows):  # noqa: ARG001
    """The OR list also covers `toolkit_id` and `agent_id` — exercise both."""
    by_toolkit = f"job_q_{uuid.uuid4().hex[:8]}"
    by_agent = f"job_q_{uuid.uuid4().hex[:8]}"

    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status, created_at)
               VALUES (?, 'broker', 'POST /api.example.com/x', 'acme-payments', 'complete',
                       strftime('%s','now'))""",
            (by_toolkit,),
        )
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, agent_id, status, created_at)
               VALUES (?, 'broker', 'POST /api.example.com/y', 'default', 'agnt_zeta42',
                       'complete', strftime('%s','now'))""",
            (by_agent,),
        )
        cx.commit()

    by_tk = {r["job_id"] for r in admin_client.get("/jobs?q=acme-pay&limit=100").json()["data"]}
    assert by_toolkit in by_tk
    assert by_agent not in by_tk

    by_ag = {r["job_id"] for r in admin_client.get("/jobs?q=zeta42&limit=100").json()["data"]}
    assert by_agent in by_ag
    assert by_toolkit not in by_ag


@pytest.mark.asyncio
async def test_q_escapes_like_wildcards(admin_client, cleanup_q_rows):  # noqa: ARG001
    """`%` / `_` in `q` are matched literally, not as LIKE wildcards.

    Regression guard: before escaping, `q=%` matched every row (the filter
    silently degraded to no-filter) and a literal `%` was unsearchable.
    """
    literal = f"exec_q_{uuid.uuid4().hex[:8]}"
    plain = f"exec_q_{uuid.uuid4().hex[:8]}"

    await write_trace(
        trace_id=literal,
        toolkit_id="default",
        operation_id="GET/api.example.com/discount/100%off",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=10,
        error=None,
        api_id="example.com",
    )
    await write_trace(
        trace_id=plain,
        toolkit_id="default",
        operation_id="GET/api.example.com/charges",
        workflow_id=None,
        spec_path=None,
        status="success",
        http_status=200,
        duration_ms=10,
        error=None,
        api_id="example.com",
    )

    # A bare `%` must NOT act as "match everything": it should only match the
    # row that literally contains a percent sign.
    pct = {r["id"] for r in admin_client.get("/traces?q=%25&limit=500").json()["traces"]}
    assert literal in pct
    assert plain not in pct

    # `_` is a single-char wildcard in raw LIKE; escaped, "100%o" matches only
    # the literal row, proving both metachars are neutralised.
    combo = {r["id"] for r in admin_client.get("/traces?q=100%25off&limit=500").json()["traces"]}
    assert literal in combo
    assert plain not in combo


@pytest.mark.asyncio
async def test_jobs_response_uses_capability_field(admin_client, cleanup_q_rows):  # noqa: ARG001
    """`GET /jobs` serialises the slug_or_id column as `capability` on the wire."""
    jid = f"job_q_{uuid.uuid4().hex[:8]}"
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            """INSERT INTO jobs (id, kind, slug_or_id, toolkit_id, status, created_at)
               VALUES (?, 'workflow', 'wf_capability_probe', 'default', 'complete',
                       strftime('%s','now'))""",
            (jid,),
        )
        cx.commit()

    rows = admin_client.get("/jobs?q=capability_probe&limit=100").json()["data"]
    row = next(r for r in rows if r["job_id"] == jid)
    assert row["capability"] == "wf_capability_probe"
    assert "slug_or_id" not in row
