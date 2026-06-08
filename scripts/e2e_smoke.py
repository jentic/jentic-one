#!/usr/bin/env python3
"""
End-to-end smoke against a running jentic-mini stack.

Unlike ``seed_monitor_data.py`` (which writes synthetic rows directly into
SQLite), this script exercises the *real* code paths: it logs in as admin,
mints a toolkit API key, calls the broker, and polls async jobs. Every row
it produces in ``executions`` and ``jobs`` was written by the same code that
processes production traffic.

Target: ``httpbin.org`` — chosen because it requires no auth, has stable
public endpoints, and exists in zero catalog setup. The broker's policy gate
falls through to anonymous forwarding for hosts with no credential
configured, so we don't need to register an OAS spec or vault entry.

What it covers:
1. Sync broker calls   → executions row, http_status set, agent_id linked
2. Async broker calls  → jobs row + executions row with cross-link
                         (job.trace_id == trace.id == execution_id)
3. Mixed success/error → at least one 4xx response so the Monitor's
                         "failed" bucket has live data
4. Workflow execution  → registers a tiny inline OpenAPI + Arazzo for
                         httpbin, runs the workflow, and asserts each
                         child broker trace carries parent_trace_id
                         pointing at the workflow's own trace_id (proves
                         the X-Jentic-Parent-Trace loopback header is
                         honoured end-to-end)

What it does NOT cover (deliberately):
- Credential injection (httpbin needs no auth)
- Authenticated upstream APIs (would require secrets in CI)

Idempotency: re-running the script against the same backend is safe — it
reuses the existing ``e2e-smoke`` toolkit key if one is already labelled,
otherwise mints a fresh one. Existing executions/jobs are left in place;
each run simply appends new rows.

Usage:
    python3 scripts/e2e_smoke.py                       # default :8900
    JENTIC_BASE_URL=http://localhost:8900 python3 …    # explicit base URL
    JENTIC_ADMIN_PASSWORD=… python3 scripts/e2e_smoke.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Any


DEFAULT_BASE_URL = os.environ.get("JENTIC_BASE_URL", "http://localhost:8900")
ADMIN_USERNAME = os.environ.get("JENTIC_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("JENTIC_ADMIN_PASSWORD", "adminadmin")
TOOLKIT_KEY_LABEL = "e2e-smoke"
NO_AUTH_CRED_LABEL = "e2e-smoke httpbin (no-auth)"
WORKFLOW_SLUG = "e2e-smoke-httpbin"
SYNC_PROBES = [
    ("GET", "/get", 200),
    ("GET", "/status/200", 200),
    ("GET", "/status/404", 404),
    ("GET", "/status/500", 500),
    ("GET", "/headers", 200),
    ("GET", "/uuid", 200),
]
ASYNC_PROBES = [
    ("GET", "/get", 200),
    ("GET", "/delay/1", 200),
    ("GET", "/status/200", 200),
]
HTTPBIN_HOST = "httpbin.org"


class HTTPClient:
    """Tiny urllib wrapper that holds an admin session cookie + agent key."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.cookies = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.opener = opener
        self.agent_key: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        use_agent_key: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        url = self.base_url + path
        body_bytes: bytes | None = None
        hdrs = {"Accept": "application/json"}
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        if use_agent_key:
            if not self.agent_key:
                raise RuntimeError("agent_key not set — call mint_agent_key() first")
            hdrs["X-Jentic-API-Key"] = self.agent_key
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=body_bytes, method=method, headers=hdrs)
        # Broker calls (`use_agent_key=True`) authenticate as an *agent*, not as
        # an admin browser session. Sending the admin `jentic_session` cookie
        # along would (a) be the wrong identity and (b) currently gets forwarded
        # to upstream by the broker (no Cookie scrubbing) — meaning the admin
        # JWT would land in upstream logs and in the broker's stored job result.
        # Use a one-shot opener with no cookie jar for those requests.
        opener = urllib.request.build_opener() if use_agent_key else self.opener
        try:
            resp = opener.open(req, timeout=30)
            status = resp.status
            resp_headers = dict(resp.headers.items())
            payload = resp.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers = dict(exc.headers.items()) if exc.headers else {}
            payload = exc.read() if hasattr(exc, "read") else b""
        except (urllib.error.URLError, OSError) as exc:
            # Transport-level failure (connection reset/refused/timeout) — e.g. a
            # flaky upstream dropping the socket mid-forward. Surface it as a
            # synthetic 0 status so the probe is recorded as a failure rather
            # than crashing the whole smoke run.
            status = 0
            resp_headers = {}
            payload = str(exc).encode()
        return status, resp_headers, payload

    def json_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        use_agent_key: bool = False,
    ) -> tuple[int, dict[str, str], Any]:
        status, hdrs, raw = self.request(
            method, path, json_body=json_body, headers=headers, use_agent_key=use_agent_key
        )
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode(errors="replace")
        return status, hdrs, body


def login_admin(http: HTTPClient) -> None:
    """Authenticate as admin. Required for toolkit/key management endpoints."""
    status, _, body = http.json_request(
        "POST",
        "/user/login",
        json_body={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    if status != 200:
        raise SystemExit(
            f"Admin login failed (HTTP {status}): {body!r}\n"
            f"  base_url={http.base_url}  user={ADMIN_USERNAME}\n"
            f"  hint: set JENTIC_ADMIN_PASSWORD if the admin password is not 'adminadmin'."
        )


def mint_agent_key(http: HTTPClient) -> str:
    """Reuse an existing e2e-smoke key if present, otherwise mint a new one.

    The full key value is only returned at creation time. If a key with our
    label already exists, we cannot retrieve its value — we always mint a
    fresh one. To keep re-runs from accumulating key rows indefinitely, we
    revoke any prior e2e-smoke keys before issuing the new one.
    """
    status, _, body = http.json_request("GET", "/toolkits/default/keys")
    if status == 200 and isinstance(body, dict):
        for key in body.get("keys", []):
            if key.get("label") == TOOLKIT_KEY_LABEL:
                key_id = key.get("id")
                if key_id:
                    http.json_request("DELETE", f"/toolkits/default/keys/{key_id}")
    status, _, body = http.json_request(
        "POST",
        "/toolkits/default/keys",
        json_body={"label": TOOLKIT_KEY_LABEL},
    )
    if status not in (200, 201) or not isinstance(body, dict) or not body.get("key"):
        raise SystemExit(f"Could not mint agent key (HTTP {status}): {body!r}")
    return body["key"]


def ensure_no_auth_credential(http: HTTPClient) -> None:
    """Register an auth_type=none credential for httpbin.org so the broker
    forwards calls instead of fail-closing.

    jentic-mini uses a route-based credential model: the broker denies any
    authenticated call (403 policy_denied) to a host with no matching
    credential_routes row — there is no anonymous fall-through for toolkit
    callers (see tests/test_no_auth_api.py and src/routers/broker.py). A
    "no-auth" upstream like httpbin is therefore modelled as an
    auth_type=none credential: POST /credentials derives the route from
    api_id (host = "httpbin.org") and injects no auth header. The agent key
    is on the default toolkit, which the broker resolves by host without a
    toolkit_credentials binding, so this single call is enough.

    Idempotent: deletes any prior e2e-smoke no-auth credential first so the
    derived id and route stay stable across re-runs.
    """
    status, _, body = http.json_request("GET", "/credentials?api_id=httpbin.org")
    # GET /credentials returns a bare JSON array, not an envelope object. Delete
    # every prior e2e-smoke no-auth credential so a re-run doesn't leave two
    # credentials matching the same host (the broker then 409s CREDENTIAL_AMBIGUOUS).
    if status == 200 and isinstance(body, list):
        for cred in body:
            if cred.get("label") == NO_AUTH_CRED_LABEL and cred.get("id"):
                http.json_request("DELETE", f"/credentials/{cred['id']}")
    status, _, body = http.json_request(
        "POST",
        "/credentials",
        json_body={
            "label": NO_AUTH_CRED_LABEL,
            "api_id": HTTPBIN_HOST,
            "auth_type": "none",
            "value": "",
        },
    )
    if status not in (200, 201) or not isinstance(body, dict):
        raise SystemExit(
            f"Could not register no-auth credential for {HTTPBIN_HOST} (HTTP {status}): {body!r}\n"
            f"  Without it the broker fail-closes every probe with 403 policy_denied."
        )


def fire_sync(http: HTTPClient, method: str, path: str, expected: int) -> dict[str, Any]:
    target = f"/{HTTPBIN_HOST}{path}"
    t0 = time.monotonic()
    status, hdrs, _ = http.request(method, target, use_agent_key=True)
    dt_ms = int((time.monotonic() - t0) * 1000)
    return {
        "target": target,
        "expected": expected,
        "status": status,
        "ok": status == expected,
        "execution_id": hdrs.get("x-jentic-execution-id"),
        "duration_ms": dt_ms,
    }


def fire_async(http: HTTPClient, method: str, path: str) -> dict[str, Any]:
    """Fire with Prefer: respond-async, wait=0 — backend always returns 202.

    The job_id and execution_id come back in response headers; the body is
    a small JSON envelope with a poll URL.
    """
    target = f"/{HTTPBIN_HOST}{path}"
    status, hdrs, _ = http.request(
        method,
        target,
        headers={"Prefer": "respond-async, wait=0"},
        use_agent_key=True,
    )
    return {
        "target": target,
        "status": status,
        "job_id": hdrs.get("x-jentic-job-id"),
        "execution_id": hdrs.get("x-jentic-execution-id"),
    }


def poll_job(http: HTTPClient, job_id: str, timeout_s: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, _, body = http.json_request("GET", f"/jobs/{job_id}")
        if status != 200 or not isinstance(body, dict):
            return {"job_id": job_id, "status": "lookup_failed", "http": status, "body": body}
        last = body
        if body.get("status") in ("complete", "failed", "cancelled"):
            return body
        time.sleep(0.5)
    return {**last, "status": last.get("status", "timeout"), "_timed_out": True}


def assert_trace_link(http: HTTPClient, trace_id: str, expected_job_id: str) -> dict[str, Any]:
    """Confirm the executions row carries the cross-link to its parent job."""
    status, _, body = http.json_request("GET", f"/traces/{trace_id}")
    return {
        "http": status,
        "trace_id": body.get("id") if isinstance(body, dict) else None,
        "job_id": body.get("job_id") if isinstance(body, dict) else None,
        "expected_job_id": expected_job_id,
        "matches": isinstance(body, dict) and body.get("job_id") == expected_job_id,
    }


# ── Workflow coverage ────────────────────────────────────────────────────────
#
# We register a tiny inline OpenAPI for httpbin.org plus a two-step Arazzo
# workflow that calls it. Running the workflow proves the full
# parent_trace_id chain end-to-end:
#
#     POST /workflows/{slug}              ← workflow_trace_id minted here
#       └─ subprocess (arazzo-runner)
#            session.headers["X-Jentic-Parent-Trace"] = workflow_trace_id
#            ├─ broker GET /httpbin.org/get      → child_trace_1
#            └─ broker GET /httpbin.org/uuid     → child_trace_2
#
# Both child traces should land in `executions` with
#     parent_trace_id == workflow_trace_id
# which the Monitor surfaces as "part of workflow X". The header is loopback-
# only on the broker (see `tests/test_traces_xlinks.py`), so spoofing from
# the public API surface is impossible — we exercise the trusted path here.


_HTTPBIN_OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "httpbin.org (e2e-smoke)", "version": "1.0.0"},
    "servers": [{"url": "https://httpbin.org"}],
    "paths": {
        "/get": {
            "get": {
                "operationId": "httpbinGet",
                "summary": "Echo request",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/uuid": {
            "get": {
                "operationId": "httpbinUuid",
                "summary": "Random UUID",
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


def _build_arazzo(spec_path: str) -> dict[str, Any]:
    """Two-step workflow against the just-imported httpbin OpenAPI.

    `sourceDescriptions[0].url` MUST be the on-disk path returned by the
    OpenAPI import (the broker preprocessor reads it to rewrite servers
    onto loopback). The container path is what matters here, not the host
    path — the import response gives us exactly that.
    """
    return {
        "arazzo": "1.0.0",
        "info": {
            "title": "e2e-smoke httpbin workflow",
            "version": "1.0.0",
            "description": "Two-step httpbin workflow used by scripts/e2e_smoke.py",
        },
        "sourceDescriptions": [
            {"name": "httpbin", "type": "openapi", "url": spec_path},
        ],
        "workflows": [
            {
                "workflowId": WORKFLOW_SLUG,
                "summary": "GET /get then GET /uuid against httpbin",
                "description": "Drives parent_trace_id end-to-end.",
                "inputs": {"type": "object", "properties": {}},
                "steps": [
                    {
                        "stepId": "echo",
                        "operationId": "httpbinGet",
                        "outputs": {"echoed": "$response.body"},
                    },
                    {
                        "stepId": "uuid",
                        "operationId": "httpbinUuid",
                        "outputs": {"uuid": "$response.body.uuid"},
                    },
                ],
                "outputs": {
                    "echoed": "$steps.echo.outputs.echoed",
                    "uuid": "$steps.uuid.outputs.uuid",
                },
            }
        ],
    }


def register_workflow_artifacts(http: HTTPClient) -> str:
    """Import the httpbin OpenAPI + the Arazzo. Returns the workflow slug.

    Idempotent: POST /import uses INSERT OR REPLACE for both apis and
    workflows tables, so re-runs simply overwrite the previous registration.
    """
    status, _, body = http.json_request(
        "POST",
        "/import",
        json_body={
            "sources": [
                {
                    "type": "inline",
                    "filename": "e2e-smoke-httpbin.json",
                    "content": json.dumps(_HTTPBIN_OPENAPI),
                }
            ]
        },
    )
    if status != 200 or not isinstance(body, dict) or body.get("status") not in ("ok", "partial"):
        raise SystemExit(f"OpenAPI import failed (HTTP {status}): {body!r}")
    api_result = body["results"][0]
    if api_result.get("status") != "success":
        raise SystemExit(f"OpenAPI import returned failure: {api_result!r}")
    spec_path = api_result.get("spec_path")
    if not spec_path:
        raise SystemExit(f"OpenAPI import missing spec_path: {api_result!r}")

    arazzo = _build_arazzo(spec_path)
    status, _, body = http.json_request(
        "POST",
        "/import",
        json_body={
            "sources": [
                {
                    "type": "inline",
                    "filename": "e2e-smoke-httpbin.arazzo.json",
                    "content": json.dumps(arazzo),
                }
            ]
        },
    )
    if status != 200 or not isinstance(body, dict) or body.get("status") not in ("ok", "partial"):
        raise SystemExit(f"Arazzo import failed (HTTP {status}): {body!r}")
    wf_result = body["results"][0]
    if wf_result.get("status") != "success":
        raise SystemExit(f"Arazzo import returned failure: {wf_result!r}")
    slug = wf_result.get("slug")
    if not slug:
        raise SystemExit(f"Arazzo import missing slug: {wf_result!r}")
    return slug


def run_workflow(http: HTTPClient, slug: str) -> dict[str, Any]:
    """Execute the workflow synchronously via POST /workflows/{slug}.

    Auth uses the agent toolkit key (X-Jentic-API-Key), which the runner
    subprocess re-uses on every child broker call. The workflow's own
    trace_id comes back in the response body as `trace_id` — that is what
    we expect each child trace's parent_trace_id to point at.
    """
    status, hdrs, body = http.json_request(
        "POST",
        f"/workflows/{slug}",
        json_body={},
        use_agent_key=True,
    )
    workflow_trace_id = None
    if isinstance(body, dict):
        workflow_trace_id = body.get("trace_id")
    if not workflow_trace_id:
        # Fall back to header for older builds; harmless on current code.
        workflow_trace_id = hdrs.get("x-jentic-execution-id")
    return {
        "http": status,
        "workflow_trace_id": workflow_trace_id,
        "body": body,
    }


def assert_workflow_links(http: HTTPClient, workflow_trace_id: str) -> dict[str, Any]:
    """Find child traces of the workflow and confirm parent_trace_id is set.

    Strategy: list recent /traces, then keep only rows with
    parent_trace_id == workflow_trace_id. We expect at least two (one per
    Arazzo step). Anything less is a regression.

    We deliberately do NOT filter by api_id here: httpbin needs no auth, so
    no credential matches and the broker stores api_id=NULL on these child
    traces (api_id is sourced from the matched credential record). An
    api_id=httpbin.org filter would exact-match-exclude every child row and
    the assertion would always find zero. parent_trace_id is the real link.
    """
    status, _, body = http.json_request(
        "GET",
        "/traces?limit=50",
    )
    if status != 200 or not isinstance(body, dict):
        return {"ok": False, "http": status, "reason": "lookup_failed", "body": body}
    items = body.get("traces") or []
    children = [t for t in items if t.get("parent_trace_id") == workflow_trace_id]
    return {
        "ok": len(children) >= 2,
        "found": len(children),
        "expected_min": 2,
        "scanned": len(items),
        "child_trace_ids": [t.get("id") for t in children],
        "child_paths": [t.get("operation_id") for t in children],
    }


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    parser = argparse.ArgumentParser(description="jentic-mini e2e smoke")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument(
        "--skip-async", action="store_true", help="Skip the async broker calls + job polling"
    )
    args = parser.parse_args()

    http = HTTPClient(args.base_url)

    section("Setup")
    login_admin(http)
    print(f"  logged in as {ADMIN_USERNAME} on {http.base_url}")
    http.agent_key = mint_agent_key(http)
    print(f"  minted agent key  prefix={http.agent_key[:6]}…  label={TOOLKIT_KEY_LABEL}")
    ensure_no_auth_credential(http)
    print(f"  registered no-auth credential for {HTTPBIN_HOST} (route-based passthrough)")

    section("Sync broker calls")
    sync_results = []
    for method, path, expected in SYNC_PROBES:
        result = fire_sync(http, method, path, expected)
        sync_results.append(result)
        marker = "ok " if result["ok"] else "ERR"
        print(
            f"  [{marker}] {method:4s} {result['target']:38s} "
            f"got={result['status']} (expected {expected})  "
            f"trace={result['execution_id'] or '(missing)'}  {result['duration_ms']}ms"
        )

    sync_failures = [r for r in sync_results if not r["ok"]]
    if sync_failures:
        print(f"  WARN: {len(sync_failures)} sync probe(s) returned unexpected status")

    async_results = []
    if not args.skip_async:
        section("Async broker calls (Prefer: respond-async, wait=0)")
        for method, path, _expected in ASYNC_PROBES:
            r = fire_async(http, method, path)
            async_results.append(r)
            print(
                f"  dispatched {method} {r['target']:30s} "
                f"job={r['job_id'] or '(missing)'}  trace={r['execution_id'] or '(missing)'}"
            )

        section("Polling jobs to terminal state")
        terminal_results = []
        for r in async_results:
            if not r["job_id"]:
                continue
            terminal = poll_job(http, r["job_id"])
            terminal_results.append({"dispatch": r, "terminal": terminal})
            print(
                f"  job {r['job_id']:24s} → status={terminal.get('status')} "
                f"trace_id={terminal.get('trace_id') or '(missing)'}"
            )

        section("Verifying execution↔job cross-links")
        link_failures = 0
        for entry in terminal_results:
            r = entry["dispatch"]
            if not (r["execution_id"] and r["job_id"]):
                link_failures += 1
                print(f"  MISSING trace or job id from dispatch: {r}")
                continue
            check = assert_trace_link(http, r["execution_id"], r["job_id"])
            if check["matches"]:
                print(f"  ok  trace {r['execution_id']} → job_id={r['job_id']}")
            else:
                link_failures += 1
                print(f"  ERR trace {r['execution_id']}: {check}")
        if link_failures:
            print(f"  FAIL: {link_failures} cross-link check(s) failed")

    section("Workflow execution (parent_trace_id end-to-end)")
    wf_failures = 0
    try:
        slug = register_workflow_artifacts(http)
        print(f"  registered  workflow={slug}  (httpbin OpenAPI + 2-step Arazzo)")
        wf_run = run_workflow(http, slug)
        wf_trace = wf_run["workflow_trace_id"]
        if wf_run["http"] != 200 or not wf_trace:
            wf_failures += 1
            print(f"  ERR  workflow run failed: http={wf_run['http']} body={wf_run['body']!r}")
        else:
            wf_status = (
                wf_run["body"].get("status") if isinstance(wf_run["body"], dict) else "unknown"
            )
            print(f"  ran         workflow_trace={wf_trace}  status={wf_status}")
            link_check = assert_workflow_links(http, wf_trace)
            if link_check["ok"]:
                print(
                    f"  ok  found {link_check['found']} child trace(s) "
                    f"linked via parent_trace_id={wf_trace}"
                )
                for tid, path in zip(link_check["child_trace_ids"], link_check["child_paths"]):
                    print(f"        child trace={tid}  path={path}")
            else:
                wf_failures += 1
                print(f"  ERR parent_trace_id linkage: {link_check}")
    except SystemExit as exc:
        wf_failures += 1
        print(f"  ERR workflow setup: {exc}")

    section("Summary")
    print(f"  base_url       : {http.base_url}")
    print(f"  sync probes    : {len(sync_results)} ({len(sync_failures)} unexpected)")
    print(f"  async probes   : {len(async_results)}")
    print(f"  workflow       : {wf_failures} failure(s)")
    monitor_url = http.base_url.replace(":8900", ":5173") + "/monitor"
    print(f"  monitor page   : {monitor_url}")
    print(f"  agents page    : {monitor_url.rsplit('/', 1)[0]}/agents")

    if (
        sync_failures
        or wf_failures
        or (not args.skip_async and any(not r.get("job_id") for r in async_results))
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
