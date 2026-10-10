"""Real end-to-end scenarios for the require-approval (ask tier) hold.

Run against the stack ``stack.sh up`` starts::

    tests/e2e_manual/ask_tier/stack.sh up
    PYTHONPATH=. uv run python -m tests.e2e_manual.ask_tier.scenarios          # all
    PYTHONPATH=. uv run python -m tests.e2e_manual.ask_tier.scenarios s01 s05  # some
    tests/e2e_manual/ask_tier/stack.sh down

Every agent call goes through a real lane — the ``jentic`` CLI built from this
checkout, the Go ``jentic mcp`` stdio daemon, the Python HTTP ``/mcp`` mount,
or plain HTTP to the standalone broker with an agent token — and every human
step goes through the admin/control API with a signed-in user's token. The
database is touched only to read state, to fast-forward an approval's
``expires_at`` (s03), and to hand an agent to a non-admin owner (OSS has no
claim-token minter, so a member cannot otherwise own a self-registered agent).

Each scenario prints PASS / FAIL, plus GAP lines for behaviour that is out of
the current phase's scope (2D fresh-rule re-authorisation). The results land
in ``$E2E_DIR/results.json``.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tests.e2e_manual.ask_tier.lanes import (
    CliLane,
    Env,
    GoMcpLane,
    HttpMcpLane,
    Human,
    StepError,
    bootstrap_admin,
    expect,
    make_user,
    mcp_payload,
    psql,
    reset_upstream,
    upstream_calls,
)

API = {"vendor": "ask-e2e-test", "name": "ask-tier-e2e", "version": "1.0.0"}
#: The same spec imported under a second vendor (s16's second connect request).
API_TWO = {"vendor": "ask-e2e-two", "name": "ask-tier-e2e", "version": "1.0.0"}
ROOT = Path(__file__).resolve().parents[3]

#: The ask rules every lane's binding carries: POST /orders, GET /items and
#: POST /slow are held for review; everything else is allowed.
ASK_RULES: list[dict[str, Any]] = [
    {"effect": "require-approval", "methods": ["POST"], "path": "^/orders$", "match_mode": "regex"},
    {"effect": "require-approval", "methods": ["GET"], "path": "^/items$", "match_mode": "regex"},
    {"effect": "require-approval", "methods": ["POST"], "path": "^/slow$", "match_mode": "regex"},
    {"effect": "allow", "path": ".*", "match_mode": "regex"},
]


@dataclass
class Outcome:
    name: str
    status: str = "pass"
    notes: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.notes.append(msg)
        print(f"    . {msg}")

    def gap(self, msg: str) -> None:
        self.gaps.append(msg)
        print(f"    GAP {msg}")


@dataclass
class World:
    env: Env
    admin: Human
    owner: Human
    other: Human
    cli: CliLane
    gocli: CliLane
    http: HttpMcpLane
    creds: dict[str, str]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def _wait_job(admin: Human, job_id: str, statuses: set[str], timeout: float = 60) -> dict[str, Any]:
    deadline = time.time() + timeout
    job: dict[str, Any] = {}
    while time.time() < deadline:
        r = admin.get(f"/jobs/{job_id}")
        expect(r.status_code == 200, f"GET /jobs/{job_id} -> {r.status_code}", r.text)
        job = r.json()
        if job["status"] in statuses:
            return job
        time.sleep(0.5)
    raise StepError(f"job {job_id} never reached {statuses}; last {job.get('status')}", job)


def _import_api(admin: Human, env: Env, api: dict[str, str] = API, spec: str = "ask.json") -> None:
    """Import the upstream's ``spec`` as ``api`` (its vendor) and promote it, once."""
    base = f"/apis/{api['vendor']}/{api['name']}/{api['version']}"
    r = admin.get(base)
    if r.status_code == 200 and r.json().get("current_revision_id"):
        return
    source = {"type": "url", "url": f"{env.upstream}/specs/{spec}", "vendor": api["vendor"]}
    r = admin.post("/apis", {"sources": [source]})
    expect(r.status_code == 202, "import spec", r.text)
    job = _wait_job(admin, r.json()["job_id"], {"completed", "failed"}, timeout=120)
    expect(job["status"] == "completed", "import job", job)
    revision = admin.get(f"/jobs/{r.json()['job_id']}/result").json()["revisions"][0]
    expect(revision["api"] == api, "imported under the expected identity", revision)
    p = admin.post(f"{base}/revisions/{revision['revision_id']}:promote")
    expect(p.status_code == 200, "promote revision", p.text)


def _bind(admin: Human, agent_id: str, label: str, rules: list[dict[str, Any]]) -> str:
    """The agent's ``ask-<label>`` credential binding (created once), with ``rules``."""
    bound = admin.get(f"/agents/{agent_id}/credentials").json().get("data", [])
    for row in bound:
        if row.get("name") == f"ask-{label}":
            cid = str(row["credential_id"])
            set_rules(admin, cid, agent_id, rules)
            return cid
    r = admin.post(
        "/credentials",
        {
            "type": "bearer_token",
            "name": f"ask-{label}",
            "api": API,
            "provider": "static",
            "token": f"upstream-secret-{label}",
        },
    )
    expect(r.status_code == 201, f"create credential {label}", r.text)
    cid = str(r.json()["credential"]["credential_id"])
    r = admin.post(f"/agents/{agent_id}/credentials", {"credential_id": cid})
    expect(r.status_code == 201, f"bind {label}", r.text)
    set_rules(admin, cid, agent_id, rules)
    return cid


def set_rules(admin: Human, cid: str, agent_id: str, rules: list[dict[str, Any]]) -> None:
    r = admin.put(f"/credentials/{cid}/agents/{agent_id}/permissions", rules)
    expect(r.status_code == 200, "set binding rules", r.text)


def setup(env: Env) -> World:
    admin = bootstrap_admin(env)
    _import_api(admin, env)
    owner = make_user(
        env,
        admin,
        "owner@ask-e2e.test",
        ["agents:read", "credentials:read", "jobs:read", "jobs:write", "apis:read"],
    )
    other = make_user(env, admin, "other@ask-e2e.test", ["jobs:read", "jobs:write"])
    cli = CliLane(env, admin, "ask-cli")
    gocli = CliLane(env, admin, "ask-gomcp")
    http = HttpMcpLane(env, admin, "ask-httpmcp")
    # The HTTP lane's agent belongs to the member `owner` (see the module doc).
    psql(env, f"UPDATE admin.agents SET owner_id = '{owner.user_id}' WHERE id = '{http.agent_id}'")
    creds = {
        "cli": _bind(admin, cli.agent_id, "cli", ASK_RULES),
        "gomcp": _bind(admin, gocli.agent_id, "gomcp", ASK_RULES),
        "http": _bind(admin, http.agent_id, "http", ASK_RULES),
    }
    return World(env, admin, owner, other, cli, gocli, http, creds)


# ---------------------------------------------------------------------------
# Agent-side helpers
# ---------------------------------------------------------------------------


def cli_execute(lane: CliLane, target: str, *args: str) -> tuple[int, dict[str, Any], str]:
    p = lane.run("execute", target, *args, "--json")
    out = lane.parse(p.stdout) if p.stdout.strip() else {}
    return p.returncode, out if isinstance(out, dict) else {"raw": out}, p.stderr


def cli_hold(lane: CliLane, target: str, *args: str) -> dict[str, Any]:
    rc, out, err = cli_execute(lane, target, *args)
    expect(
        out.get("status") == 202,
        f"expected a 202 held envelope (rc {rc})",
        {"out": out, "err": err},
    )
    body = out["body"]
    expect(body.get("status") == "held", "envelope status held", body)
    return dict(body)


def broker_raw(
    world: World,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Call the standalone broker as the HTTP lane's agent, bytes exactly as given."""
    url = f"{world.env.broker}/{world.env.upstream}{path}"
    h = {"authorization": f"Bearer {world.http.token}", **(headers or {})}
    return httpx.request(method, url, content=body, headers=h, timeout=60)


def agent_api(world: World, method: str, path: str, body: Any = None) -> httpx.Response:
    """The HTTP lane's agent calling the admin API with its own token."""
    return httpx.request(
        method,
        f"{world.env.app}{path}",
        json=body,
        headers={"authorization": f"Bearer {world.http.token}"},
        timeout=60,
    )


def calls_to(env: Env, path: str) -> list[dict[str, Any]]:
    return [c for c in upstream_calls(env) if c["path"] == path]


def withdraw_all_pending(world: World) -> None:
    for a in world.admin.approvals(state="pending", limit=100):
        world.admin.decide(a["id"], "deny", "e2e cleanup")


def result_of(world: World, job_id: str) -> dict[str, Any]:
    r = world.admin.get(f"/jobs/{job_id}/result")
    expect(r.status_code == 200, f"GET /jobs/{job_id}/result -> {r.status_code}", r.text)
    return dict(r.json())


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def s01_hold_approve_result(w: World, o: Outcome) -> None:
    """Ask rule -> 202 held -> approve -> the result reaches the agent (CLI lane)."""
    reset_upstream(w.env)
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=10")
    approval = held["approval"]
    expect(
        approval["review_url"].endswith(f"/app/agents/approvals/{approval['id']}"),
        "review_url",
        held,
    )
    expect(calls_to(w.env, "/items") == [], "nothing reached the upstream before a decision")
    job = w.admin.get(f"/jobs/{held['job_id']}").json()
    expect(job["status"] == "held", "job is held", job)
    detail = w.admin.approval(approval["id"]).json()
    expect(
        detail["request"]["url"].endswith("/items?limit=10"), "reviewer sees the exact URL", detail
    )
    r = w.admin.decide(approval["id"], "approve", "looks fine")
    expect(r.status_code == 200 and r.json()["state"] == "approved", "approve", r.text)
    job = _wait_job(w.admin, held["job_id"], {"completed", "failed"})
    expect(job["status"] == "completed", "approved job completed", job)
    # The agent reads its own result through the CLI (`jentic api`).
    p = w.cli.run("api", "GET", f"/jobs/{held['job_id']}/result")
    res = w.cli.parse(p.stdout)
    expect(p.returncode == 0 and res.get("http_status") == 200, "agent reads the result", p.stdout)
    calls = calls_to(w.env, "/items")
    expect(
        len(calls) == 1 and calls[0]["query"] == "limit=10", "ran exactly once, query kept", calls
    )
    expect(
        calls[0]["headers"].get("authorization") == "Bearer upstream-secret-cli",
        "the bound credential was injected at run time",
        calls[0]["headers"],
    )
    o.note(f"approval {approval['id']} -> job completed, upstream hit once")


def s02_deny_with_reason(w: World, o: Outcome) -> None:
    """Deny with a reason -> the agent reads a 403 problem carrying it."""
    reset_upstream(w.env)
    held = cli_hold(w.cli, f"POST:{w.env.upstream}/orders", "-d", '{"n": 2}')
    r = w.admin.decide(held["approval"]["id"], "deny", "not today")
    expect(r.status_code == 200 and r.json()["state"] == "denied", "deny", r.text)
    job = _wait_job(w.admin, held["job_id"], {"failed"})
    res = result_of(w, job["job_id"])
    expect(res.get("type") == "approval_denied" and res.get("status") == 403, "denied problem", res)
    expect("not today" in res.get("detail", ""), "reason reaches the agent", res)
    expect(calls_to(w.env, "/orders") == [], "a denied call never runs")
    again = w.admin.decide(held["approval"]["id"], "approve")
    expect(again.status_code == 409, "a second decision is refused (409)", again.text)


def _audit(w: World, target_id: str) -> list[dict[str, Any]]:
    r = w.admin.get(
        "/audit",
        params={"target_type": "execution_approval", "target_id": target_id, "limit": 50},
    )
    expect(r.status_code == 200, "GET /audit", r.text)
    return list(r.json()["data"])


def _events(w: World, type_: str) -> list[dict[str, Any]]:
    r = w.admin.get("/events", params={"event_type": type_, "limit": 100})
    expect(r.status_code == 200, "GET /events", r.text)
    return [e for e in r.json()["data"] if e.get("type") == type_]


def s03_expiry(w: World, o: Outcome) -> None:
    """Expiry -> the sweep fails the job; an audit entry and an event record it."""
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=3")
    aid = held["approval"]["id"]
    psql(
        w.env,
        "UPDATE admin.execution_approvals SET expires_at = now() - interval '1 second' "
        f"WHERE id = '{aid}'",
    )
    # Before the sweep runs, a decision on the lapsed approval is already refused.
    late = w.admin.decide(aid, "approve")
    expect(late.status_code == 409, "approving a lapsed approval answers 409", late.text)
    o.note("waiting for the worker's expiry sweep (up to ~2.5 min)")
    deadline = time.time() + 170
    state = ""
    while time.time() < deadline:
        state = w.admin.approval(aid).json()["state"]
        if state == "expired":
            break
        time.sleep(3)
    expect(state == "expired", "approval expired by the sweep", state)
    job = _wait_job(w.admin, held["job_id"], {"failed"})
    res = result_of(w, job["job_id"])
    expect(
        res.get("type") == "approval_expired" and res.get("status") == 403, "expired problem", res
    )
    audits = [a for a in _audit(w, aid) if a.get("action") == "expire"]
    expect(len(audits) == 1, "one expire audit entry", _audit(w, aid))
    expect(audits[0].get("actor_id") == w.cli.agent_id, "attributed to the filing agent", audits[0])
    events = [
        e
        for e in _events(w, "execution.approval_expired")
        if (e.get("data") or {}).get("approval_id") == aid
    ]
    expect(len(events) == 1, "one execution.approval_expired event", events)
    p = w.cli.run("api", "GET", f"/jobs/{held['job_id']}")
    expect(w.cli.parse(p.stdout).get("status") == "failed", "agent sees the job failed", p.stdout)


def s04_withdraw(w: World, o: Outcome) -> None:
    """Withdraw: only the filer; the job is cancelled and never runs."""
    reset_upstream(w.env)
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=4")
    aid = held["approval"]["id"]
    for who, human in (("other user", w.other), ("org admin", w.admin)):
        r = human.post(f"/executions/approvals/{aid}:withdraw")
        expect(r.status_code == 404, f"{who} cannot withdraw (404)", r.text)
    p = w.cli.run("api", "POST", f"/executions/approvals/{aid}:withdraw")
    body = w.cli.parse(p.stdout)
    expect(
        body.get("state") == "withdrawn", "filer withdraws via `jentic api`", p.stdout + p.stderr
    )
    job = _wait_job(w.admin, held["job_id"], {"cancelled"})
    expect(job["status"] == "cancelled", "job cancelled", job)
    again = w.cli.run("api", "POST", f"/executions/approvals/{aid}:withdraw")
    expect(
        '"status":409' in again.stdout.replace(" ", ""), "a second withdraw is 409", again.stdout
    )
    expect(calls_to(w.env, "/items") == [], "a withdrawn call never runs")


def s05_join_and_new(w: World, o: Outcome) -> None:
    """An identical retry joins its hold; a different query or body files a new one."""
    a = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=20", "--query", "x=1")
    b = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "x=1", "--query", "limit=20")
    c = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=21", "--query", "x=1")
    expect(a["approval"]["id"] == b["approval"]["id"], "same query (reordered) joins", [a, b])
    expect(a["approval"]["id"] != c["approval"]["id"], "a different query files a new hold", [a, c])
    d = broker_raw(
        w, "POST", "/orders", body=b'{"a": 1, "b": 2}', headers={"content-type": "application/json"}
    )
    e = broker_raw(
        w, "POST", "/orders", body=b'{"b":2,"a":1}', headers={"content-type": "application/json"}
    )
    f = broker_raw(
        w, "POST", "/orders", body=b'{"a": 1, "b": 3}', headers={"content-type": "application/json"}
    )
    expect(
        d.status_code == e.status_code == f.status_code == 202,
        "three holds",
        [d.text, e.text, f.text],
    )
    dj, ej, fj = d.json(), e.json(), f.json()
    expect(dj["approval"]["id"] == ej["approval"]["id"], "equivalent JSON body joins", [dj, ej])
    expect(
        dj["approval"]["id"] != fj["approval"]["id"], "a different body files a new hold", [dj, fj]
    )
    requested = [
        e
        for e in _events(w, "execution.approval_requested")
        if (e.get("data") or {}).get("approval_id") == a["approval"]["id"]
    ]
    expect(len(requested) == 1, "a joined retry emits no second requested event", requested)
    withdraw_all_pending(w)


def s06_pending_cap(w: World, o: Outcome) -> None:
    """The per-agent pending cap (3 here) holds, including under concurrent calls."""
    withdraw_all_pending(w)
    for i in range(3):
        cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", f"limit={600 + i}")
    rc, out, err = cli_execute(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=699")
    body = out.get("body") if isinstance(out.get("body"), dict) else {}
    problem_type = (body or {}).get("type") or ""
    expect(
        "approval_pending_limit_reached" in (problem_type + err + json.dumps(out)),
        "the 4th call is refused with approval_pending_limit_reached",
        {"rc": rc, "out": out, "err": err},
    )
    o.note(f"over-cap CLI call exits {rc}")
    withdraw_all_pending(w)

    def one(i: int) -> int:
        r = broker_raw(w, "GET", f"/items?limit={700 + i}")
        return r.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(one, range(8)))
    pending = w.admin.approvals(state="pending", agent_id=w.http.agent_id, limit=100)
    expect(
        codes.count(202) == 3 and codes.count(403) == 5,
        "8 concurrent calls: 3 held, 5 refused",
        codes,
    )
    expect(len(pending) == 3, "exactly 3 pending approvals", [p["id"] for p in pending])
    withdraw_all_pending(w)


def s07_rule_changed_to_deny(w: World, o: Outcome) -> None:
    """A rule changed to deny before the run -> the approved job is denied at run time."""
    reset_upstream(w.env)
    cid = w.creds["cli"]
    a = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=70")
    b = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=71")
    deny_rules = [
        {"effect": "deny", "methods": ["GET"], "path": "^/items$", "match_mode": "regex"},
        *[r for r in ASK_RULES if r["path"] != "^/items$"],
    ]
    set_rules(w.admin, cid, w.cli.agent_id, deny_rules)
    try:
        # Approved at once: the run-time re-authorisation reads the rules fresh
        # (its evaluator has no cache), so the new deny already applies.
        w.admin.decide(a["approval"]["id"], "approve")
        ja = _wait_job(w.admin, a["job_id"], {"completed", "failed"})
        if ja["status"] == "completed":
            o.gap(
                "2D: approved right after the rule became deny, the job still ran — the "
                "run-time re-authorisation did not see the fresh rules"
            )
        else:
            o.note("immediate approve after deny: denied at run time")
        # And after the broker's hot-path rule cache (30 s) has lapsed too.
        o.note("waiting 32 s past the broker's rule cache")
        time.sleep(32)
        w.admin.decide(b["approval"]["id"], "approve")
        jb = _wait_job(w.admin, b["job_id"], {"completed", "failed"})
        expect(jb["status"] == "failed", "approved job denied at run time", jb)
        res = result_of(w, b["job_id"])
        problem = res.get("problem") or res
        expect(problem.get("type") == "action_denied", "the run-time denial is action_denied", res)
        expect(
            [c for c in calls_to(w.env, "/items") if c["query"] == "limit=71"] == [],
            "the denied run never reached the upstream",
        )
    finally:
        set_rules(w.admin, cid, w.cli.agent_id, ASK_RULES)


def s08_worker_crash(w: World, o: Outcome) -> None:
    """A worker killed mid-run -> approval_resume_failed, and the call is never re-run."""
    reset_upstream(w.env)
    r = broker_raw(
        w, "POST", "/slow?seconds=45", body=b"{}", headers={"content-type": "application/json"}
    )
    expect(r.status_code == 202, "slow call held", r.text)
    held = r.json()
    w.admin.decide(held["approval"]["id"], "approve")
    _wait_job(w.admin, held["job_id"], {"running"}, timeout=30)
    deadline = time.time() + 20
    while time.time() < deadline and not calls_to(w.env, "/slow"):
        time.sleep(0.3)
    expect(len(calls_to(w.env, "/slow")) == 1, "the run reached the upstream once")
    o.note("SIGKILL app + broker (both run workers), then restart")
    subprocess.run([str(ROOT / "tests/e2e_manual/ask_tier/stack.sh"), "restart"], check=True)
    job = _wait_job(w.admin, held["job_id"], {"failed", "completed"}, timeout=120)
    expect(job["status"] == "failed", "the re-claimed approved job is failed", job)
    res = result_of(w, held["job_id"])
    expect(res.get("type") == "approval_resume_failed", "approval_resume_failed result", res)
    time.sleep(3)
    expect(len(calls_to(w.env, "/slow")) == 1, "never re-run", calls_to(w.env, "/slow"))


def s09_who_decides(w: World, o: Outcome) -> None:
    """The agent cannot decide (403); a non-owner sees 404; the member owner and org:admin can."""
    r1 = broker_raw(w, "GET", "/items?limit=90")
    r2 = broker_raw(w, "GET", "/items?limit=91")
    expect(r1.status_code == r2.status_code == 202, "two holds", [r1.text, r2.text])
    a1, a2 = r1.json()["approval"]["id"], r2.json()["approval"]["id"]
    agent_decide = agent_api(
        w, "POST", f"/executions/approvals/{a1}:decide", {"decision": "approve"}
    )
    expect(
        agent_decide.status_code == 403, "the filing agent cannot decide (403)", agent_decide.text
    )
    agent_read = agent_api(w, "GET", f"/executions/approvals/{a1}")
    expect(agent_read.status_code == 200, "the agent can read its own approval", agent_read.text)
    for verb in ("get", "decide"):
        r = w.other.approval(a1) if verb == "get" else w.other.decide(a1, "approve")
        expect(r.status_code == 404, f"a non-owner {verb} answers 404", r.text)
    expect(a1 not in [a["id"] for a in w.other.approvals()], "a non-owner does not list it")
    expect(a1 in [a["id"] for a in w.owner.approvals()], "the member owner lists it")
    r = w.owner.decide(a1, "approve")
    expect(r.status_code == 200, "the member owner (jobs:write) approves", r.text)
    r = w.admin.decide(a2, "deny", "admin says no")
    expect(r.status_code == 200, "org:admin decides another agent's approval", r.text)
    _wait_job(w.admin, r1.json()["job_id"], {"completed"})


def s10_post_json_bytes(w: World, o: Outcome) -> None:
    """A held POST replays its Content-Type and body byte for byte."""
    reset_upstream(w.env)
    raw = b'{"z": 1,   "a": [1, 2], "s": "caf\\u00e9"}'
    r = broker_raw(
        w, "POST", "/orders", body=raw, headers={"content-type": "application/json; charset=utf-8"}
    )
    expect(r.status_code == 202, "held", r.text)
    held = r.json()
    w.admin.decide(held["approval"]["id"], "approve")
    _wait_job(w.admin, held["job_id"], {"completed"})
    (call,) = calls_to(w.env, "/orders")
    expect(base64.b64decode(call["body_b64"]) == raw, "body byte for byte", call)
    expect(
        call["headers"].get("content-type") == "application/json; charset=utf-8",
        "Content-Type kept",
        call,
    )
    expect(
        call["headers"].get("authorization") == "Bearer upstream-secret-http",
        "credential injected",
        call,
    )
    # And through the CLI lane, which sets its own Content-Type.
    reset_upstream(w.env)
    held = cli_hold(w.cli, f"POST:{w.env.upstream}/orders", "-d", '{"cli": true}')
    w.admin.decide(held["approval"]["id"], "approve")
    _wait_job(w.admin, held["job_id"], {"completed"})
    (call,) = calls_to(w.env, "/orders")
    expect(
        (call["headers"].get("content-type") or "").startswith("application/json"),
        "CLI POST keeps JSON",
        call,
    )


def s11_standalone_broker_links(w: World, o: Outcome) -> None:
    """The standalone broker roots review_url and _links on the admin app's origin."""
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=110")
    review = held["approval"]["review_url"]
    expect(
        review.startswith(f"{w.env.app}/app/agents/approvals/"),
        "review_url on the app origin",
        held,
    )
    expect(
        held["_links"]["self"].startswith(f"{w.env.app}/jobs/"),
        "_links.self on the app origin",
        held,
    )
    # A browser asks for HTML; the SPA fallback serves deep links to it.
    page = httpx.get(review, headers={"accept": "text/html"}, timeout=30)
    expect(
        page.status_code == 200 and "<html" in page.text.lower(),
        "review_url serves the web UI",
        page.status_code,
    )
    log = (w.env.work / "broker.log").read_text()
    expect(
        "broker_jobs_api_base_url_unset" not in log, "no unset-base-url warning when it is pinned"
    )
    withdraw_all_pending(w)


def _http_mcp_payload(resp: dict[str, Any]) -> Any:
    err, payload = mcp_payload(resp)
    expect(not err, "MCP call succeeded", resp)
    return payload


def s12_mcp_lanes(w: World, o: Outcome) -> None:
    """Both MCP lanes, with and without URL elicitation, and wait_seconds."""
    target = f"GET:{w.env.upstream}/items"
    # HTTP mount, no elicitation: the held result at once.
    t0 = time.time()
    held = _http_mcp_payload(
        w.http.call("execute", {"operation_id": target, "inputs": {"limit": 120}})
    )
    expect(time.time() - t0 < 15, "held result returned without waiting", time.time() - t0)
    body = held.get("body", {})
    expect(held.get("status") == 202 and body.get("status") == "held", "HTTP MCP held result", held)
    job_id, aid = body["job_id"], body["approval"]["id"]
    t0 = time.time()
    polled = _http_mcp_payload(
        w.http.call("get_execution_result", {"job_id": job_id, "wait_seconds": 5})
    )
    waited = time.time() - t0
    expect(
        polled.get("status") == "held" and 4 <= waited < 20,
        "wait_seconds bounds a held poll",
        [polled, waited],
    )
    threading.Timer(3, lambda: w.owner.decide(aid, "approve")).start()
    t0 = time.time()
    done = _http_mcp_payload(
        w.http.call("get_execution_result", {"job_id": job_id, "wait_seconds": 30})
    )
    expect(done.get("status") == "completed", "wait returns once approved", done)
    o.note(f"HTTP MCP: approved mid-wait, returned after {time.time() - t0:.1f}s")

    # HTTP mount, URL elicitation declared on a legacy (2025-06-18) request: that
    # wire cannot carry an input-required result, so the held result comes back.
    caps: dict[str, Any] = {"elicitation": {"url": {}}}
    legacy = _http_mcp_payload(
        w.http.call("execute", {"operation_id": target, "inputs": {"limit": 125}}, caps=caps)
    )
    expect(
        legacy.get("body", {}).get("status") == "held",
        "legacy wire + url caps -> held result",
        legacy,
    )

    # HTTP mount, URL elicitation declared on a 2026-07-28 request.
    first = w.http.call(
        "execute", {"operation_id": target, "inputs": {"limit": 121}}, caps=caps, modern=True
    )
    res = first.get("result", {})
    reqs = res.get("inputRequests") or {}
    expect(
        "review_approval" in reqs and res.get("requestState"),
        "URL elicitation of the review page",
        first,
    )
    url = reqs["review_approval"]["params"]["url"]
    expect("/app/agents/approvals/" in url, "elicitation url is the review page", reqs)
    pending = w.owner.approvals(state="pending", agent_id=w.http.agent_id)
    aid2 = next(a["id"] for a in pending if url.endswith(a["id"]))
    threading.Timer(3, lambda: w.owner.decide(aid2, "approve")).start()
    retry = w.http.call(
        "execute",
        {"operation_id": target, "inputs": {"limit": 121}},
        caps=caps,
        modern=True,
        requestState=res["requestState"],
        inputResponses={"review_approval": {"action": "accept"}},
    )
    out = _http_mcp_payload(retry)
    expect(
        out.get("status") == 200 or out.get("http_status") == 200 or "completed" in json.dumps(out),
        "retry returns the run's result",
        out,
    )
    o.note("HTTP MCP URL elicitation: retry after accept returned the result")

    # Go stdio daemon, no elicitation.
    go = GoMcpLane(w.gocli)
    try:
        resp = go.call("execute", {"operation_id": target, "inputs": {"limit": 122}})
        err, payload = mcp_payload(resp)
        expect(not err and payload.get("status") == 202, "Go MCP held result", resp)
        gjob, gaid = payload["body"]["job_id"], payload["body"]["approval"]["id"]
        threading.Timer(3, lambda: w.admin.decide(gaid, "approve")).start()
        resp = go.call("get_execution_result", {"job_id": gjob, "wait_seconds": 30})
        err, payload = mcp_payload(resp)
        expect(
            not err and payload.get("status") == "completed", "Go MCP wait returns the result", resp
        )
    finally:
        go.close()

    # Go stdio daemon, URL elicitation declared at initialize.
    go = GoMcpLane(w.gocli, capabilities={"elicitation": {"url": {}}})
    try:
        resp = go.call("execute", {"operation_id": target, "inputs": {"limit": 123}})
        result = resp.get("result", {})
        if "inputRequests" in result:
            o.note("Go MCP with elicitation.url answers an input-required URL elicitation")
            gaid = next(
                a["id"] for a in w.admin.approvals(state="pending", agent_id=w.gocli.agent_id)
            )
            threading.Timer(3, lambda: w.admin.decide(gaid, "approve")).start()
            resp = go.call(
                "execute",
                {"operation_id": target, "inputs": {"limit": 123}},
                requestState=result.get("requestState"),
                inputResponses={"review_approval": {"action": "accept"}},
            )
            err, payload = mcp_payload(resp)
            expect(not err, "Go MCP elicitation retry", resp)
        else:
            err, payload = mcp_payload(resp)
            o.note(
                "Go MCP with elicitation.url (legacy initialize) answered: "
                f"{json.dumps(payload)[:200]}"
            )
    finally:
        go.close()
    withdraw_all_pending(w)


def s13_cli_held_exit(w: World, o: Outcome) -> None:
    """`jentic execute` on a held call: exit 3, `--wait`, and `jentic jobs wait`."""
    rc, out, _ = cli_execute(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=130")
    expect(out.get("status") == 202, "held envelope printed", out)
    if rc == 0:
        o.gap(
            "2C: `jentic execute` exits 0 on a held call (a pass-through); no exit 3 / --wait yet"
        )
    p = w.cli.run("jobs", "--help")
    if p.returncode != 0 or "wait" not in p.stdout:
        o.gap("2C: no `jentic jobs wait`; agents poll with `jentic api GET /jobs/{id}`")
    withdraw_all_pending(w)


def s14_inbox(w: World, o: Outcome) -> None:
    """The inbox's API side: a requires_action event and a pending count that clear on decide."""
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=140")
    aid = held["approval"]["id"]
    ev = [
        e
        for e in _events(w, "execution.approval_requested")
        if (e.get("data") or {}).get("approval_id") == aid
    ]
    expect(
        len(ev) == 1 and ev[0].get("requires_action") is True, "requested event requires action", ev
    )
    before = len(w.admin.approvals(state="pending"))
    w.admin.decide(aid, "deny", "inbox check")
    after = len(w.admin.approvals(state="pending"))
    expect(after == before - 1, "the pending list (badge source) drops it", [before, after])
    o.note("the UI side (inbox, badge, Waiting for you) is ui/e2e/docker/ask-tier-inbox.spec.ts")


def _migrate(w: World, code: str) -> None:
    env = {**os.environ, "JENTIC_CONFIG_FILE": str(w.env.work / "config.yaml")}
    p = subprocess.run(
        ["uv", "run", "python", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True
    )
    expect(p.returncode == 0, "migration step", p.stderr[-2000:])


def s15_migration_round_trip(w: World, o: Outcome) -> None:
    """Downgrade below the ask tier (ask -> deny, held -> cancelled), then upgrade back."""
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=150")
    stack = str(ROOT / "tests/e2e_manual/ask_tier/stack.sh")
    subprocess.run([stack, "stop"], check=False)
    try:
        _migrate(
            w,
            "from jentic_one.migrations.run import downgrade; "
            "downgrade('control', 'cc3d4e5f6a7b'); downgrade('admin', 'd2e3f4a5b6c7')",
        )
        effects = psql(
            w.env,
            "SELECT effect FROM control.agent_permission_rules "
            f"WHERE credential_id = '{w.creds['cli']}' ORDER BY sequence",
        ).split()
        expect(effects == ["deny", "deny", "deny", "allow"], "ask rules became deny", effects)
        width = psql(
            w.env,
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = 'control' AND table_name = 'agent_permission_rules' "
            "AND column_name = 'effect'",
        )
        expect(width == "10", "effect narrowed back to 10 chars", width)
        status = psql(w.env, f"SELECT status FROM admin.jobs WHERE id = '{held['job_id']}'")
        expect(status == "cancelled", "the held job was cancelled", status)
        tables = psql(
            w.env,
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'execution_approvals'",
        )
        expect(tables == "0", "execution_approvals dropped", tables)
    finally:
        _migrate(
            w, "from jentic_one.migrations.run import upgrade; upgrade('admin'); upgrade('control')"
        )
        subprocess.run([stack, "start"], check=True)
    agents = {"cli": w.cli.agent_id, "gomcp": w.gocli.agent_id, "http": w.http.agent_id}
    for lane, agent in agents.items():
        set_rules(w.admin, w.creds[lane], agent, ASK_RULES)
    # The broker's rule cache may still hold the downgraded (deny) rules.
    time.sleep(32)
    again = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=151")
    o.note(f"after re-upgrade an ask rule holds again ({again['approval']['id']})")
    withdraw_all_pending(w)


def s16_connect_session_interplay(w: World, o: Outcome) -> None:
    """A held call on an API bound through a connect session; both requests pending together."""
    reset_upstream(w.env)
    agent = HttpMcpLane(w.env, w.admin, "ask-connect")
    r = httpx.post(
        f"{w.env.app}/integrations:connect",
        headers={"authorization": f"Bearer {agent.token}"},
        json={
            "api": API,
            "auth_type": "bearer",
            "reason": "ask-tier e2e",
            "requested_permission_rules": ASK_RULES,
        },
        timeout=60,
    )
    expect(r.status_code in (200, 201, 202), "agent starts a connect session for the API", r.text)
    sid = r.json().get("session_id") or r.json().get("id")
    review = w.admin.get(f"/connect-sessions/{sid}")
    expect(review.status_code == 200, "admin reviews the session", review.text)
    data = review.json()
    conf = w.admin.post(
        f"/connect-sessions/{sid}:confirm",
        {
            "kind": "bearer",
            "token": "upstream-secret-connect",
            "digest": data["digest"],
            "expected_agent_id": (data.get("agent") or {}).get("agent_id"),
            "permission_rules": data.get("requested_permission_rules") or ASK_RULES,
        },
    )
    expect(conf.status_code == 200, "admin confirms with the bearer token", conf.text)
    hold = httpx.post(
        f"{w.env.broker}/{w.env.upstream}/orders",
        headers={"authorization": f"Bearer {agent.token}", "content-type": "application/json"},
        content=b'{"via": "connect"}',
        timeout=60,
    )
    expect(hold.status_code == 202, "the connect-bound API holds the ask call", hold.text)
    held = hold.json()
    # A second, still-open connect request from the same agent (another API),
    # pending beside the hold: both are the human's to act on.
    _import_api(w.admin, w.env, API_TWO, spec="ask-two.json")
    r2 = httpx.post(
        f"{w.env.app}/integrations:connect",
        headers={"authorization": f"Bearer {agent.token}"},
        json={"api": API_TWO, "auth_type": "bearer", "reason": "second request"},
        timeout=60,
    )
    expect(r2.status_code in (200, 201, 202), "a second connect request is open", r2.text)
    sid2 = r2.json().get("session_id") or r2.json().get("id")
    pend_approvals = [a["id"] for a in w.admin.approvals(state="pending", agent_id=agent.agent_id)]
    expect(held["approval"]["id"] in pend_approvals, "the approval is pending", pend_approvals)
    st = w.admin.get(f"/connect-sessions/{sid2}/status")
    expect(st.status_code == 200, "the connect request is readable", st.text)
    o.note(f"pending together: approval {held['approval']['id']} + connect {sid2} {st.json()}")
    w.admin.decide(held["approval"]["id"], "approve")
    _wait_job(w.admin, held["job_id"], {"completed"})
    (call,) = calls_to(w.env, "/orders")
    expect(
        call["headers"].get("authorization") == "Bearer upstream-secret-connect",
        "connect credential injected",
        call,
    )
    o.note("the UI side of the inbox is ui/e2e/docker/ask-tier-inbox.spec.ts")


SCENARIOS: dict[str, Callable[[World, Outcome], None]] = {
    "s01": s01_hold_approve_result,
    "s02": s02_deny_with_reason,
    "s03": s03_expiry,
    "s04": s04_withdraw,
    "s05": s05_join_and_new,
    "s06": s06_pending_cap,
    "s07": s07_rule_changed_to_deny,
    "s08": s08_worker_crash,
    "s09": s09_who_decides,
    "s10": s10_post_json_bytes,
    "s11": s11_standalone_broker_links,
    "s12": s12_mcp_lanes,
    "s13": s13_cli_held_exit,
    "s14": s14_inbox,
    "s15": s15_migration_round_trip,
    "s16": s16_connect_session_interplay,
}


def main(argv: list[str]) -> int:
    env = Env()
    world = setup(env)
    wanted = argv or list(SCENARIOS)
    outcomes: list[Outcome] = []
    for key in wanted:
        fn = SCENARIOS[key]
        o = Outcome(name=f"{key} {fn.__doc__.splitlines()[0] if fn.__doc__ else ''}")
        print(f"--- {o.name}")
        try:
            fn(world, o)
        except Exception as exc:  # report every scenario, then fail the run
            o.status = "fail"
            o.notes.append(f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        print(f"{o.status.upper()} {key}")
        outcomes.append(o)
    (env.work / "results.json").write_text(json.dumps([o.__dict__ for o in outcomes], indent=2))
    print("\n=== summary")
    for o in outcomes:
        tag = o.status.upper() + (" +GAP" if o.gaps else "")
        print(f"{tag:10} {o.name}")
        for g in o.gaps:
            print(f"{'':10}   gap: {g}")
    return 1 if any(o.status == "fail" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
