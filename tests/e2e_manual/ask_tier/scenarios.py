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
database is touched only to read state, to fast-forward a held execution's
``expires_at`` (s03), to hand an agent to a non-admin owner (OSS has no
claim-token minter, so a member cannot otherwise own a self-registered agent),
and to strip an agent's ``jobs:*`` grants (s09; agent grants have no admin route).

A held call is followed only through its held execution
(``/executions/holds/{id}``: ``execution_status`` for where it stands,
``/result`` for its outcome), never through ``/jobs``. Neither the reviewing
member owner nor the filing agent needs a ``jobs:*`` permission for that.

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
        ["agents:read", "credentials:read", "apis:read"],
    )
    other = make_user(env, admin, "other@ask-e2e.test", [])
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
    token: str | None = None,
) -> httpx.Response:
    """Call the standalone broker as an agent (the HTTP lane's by default), bytes as given."""
    url = f"{world.env.broker}/{world.env.upstream}{path}"
    h = {"authorization": f"Bearer {token or world.http.token}", **(headers or {})}
    return httpx.request(method, url, content=body, headers=h, timeout=60)


def agent_api(
    world: World, method: str, path: str, body: Any = None, *, token: str | None = None
) -> httpx.Response:
    """An agent (the HTTP lane's by default) calling the admin API with its own token."""
    return httpx.request(
        method,
        f"{world.env.app}{path}",
        json=body,
        headers={"authorization": f"Bearer {token or world.http.token}"},
        timeout=60,
    )


def calls_to(env: Env, path: str) -> list[dict[str, Any]]:
    return [c for c in upstream_calls(env) if c["path"] == path]


def withdraw_all_pending(world: World) -> None:
    """Settle every pending held execution (an admin deny), so caps start empty."""
    for h in world.admin.holds(state="pending", limit=100):
        world.admin.decide(h["id"], "deny", "e2e cleanup")


def problem_type(r: httpx.Response) -> str:
    """The RFC 9457 ``type`` of an error response, or ``""``."""
    try:
        body = r.json()
    except ValueError:
        return ""
    return str(body.get("type") or "") if isinstance(body, dict) else ""


def is_problem(r: httpx.Response, status: int, slug: str) -> bool:
    return r.status_code == status and problem_type(r).endswith(slug)


def wait_held(
    get: Callable[[str], httpx.Response],
    held_execution_id: str,
    statuses: set[str],
    timeout: float = 60,
) -> dict[str, Any]:
    """Poll ``GET /executions/holds/{id}`` (through ``get``) until ``execution_status``
    is one of ``statuses``; return the held execution."""
    path = f"/executions/holds/{held_execution_id}"
    deadline = time.time() + timeout
    held: dict[str, Any] = {}
    while time.time() < deadline:
        r = get(path)
        expect(r.status_code == 200, f"GET {path} -> {r.status_code}", r.text)
        held = r.json()
        if held.get("execution_status") in statuses:
            return held
        time.sleep(0.5)
    raise StepError(
        f"held execution {held_execution_id} never reached {statuses}; "
        f"last {held.get('execution_status')}",
        held,
    )


def result_of(world: World, held_execution_id: str) -> dict[str, Any]:
    """A finished held execution's result, read by the admin."""
    r = world.admin.held_result(held_execution_id)
    expect(
        r.status_code == 200,
        f"GET /executions/holds/{held_execution_id}/result -> {r.status_code}",
        r.text,
    )
    return dict(r.json())


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def s01_hold_approve_result(w: World, o: Outcome) -> None:
    """Ask rule -> 202 held -> approve -> the result reaches the agent (CLI lane)."""
    reset_upstream(w.env)
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=10")
    hid = held["held_execution_id"]
    expect(hid.startswith("held_"), "held_execution_id", held)
    expect("job_id" not in held and "approval" not in held, "no job_id or approval object", held)
    expect(held["review_url"].endswith(f"/app/agents/approvals/{hid}"), "review_url", held)
    links = held["_links"]
    expect(
        links["self"].endswith(f"/executions/holds/{hid}")
        and links["result"].endswith(f"/executions/holds/{hid}/result")
        and links["withdraw"].endswith(f"/executions/holds/{hid}:withdraw"),
        "held envelope links point at the held execution",
        links,
    )
    expect(calls_to(w.env, "/items") == [], "nothing reached the upstream before a decision")
    r = w.admin.hold(hid)
    expect(r.status_code == 200, "GET held execution", r.text)
    detail = r.json()
    expect(
        detail["state"] == "pending" and detail["execution_status"] == "held",
        "pending and held",
        detail,
    )
    expect("job_id" not in detail, "the held execution carries no job_id", detail)
    expect(
        detail["_links"].get("withdraw") and not detail["_links"].get("result"),
        "withdraw link while pending, no result link yet",
        detail["_links"],
    )
    expect(
        detail["request"]["url"].endswith("/items?limit=10"), "reviewer sees the exact URL", detail
    )
    early = w.admin.held_result(hid)
    expect(
        is_problem(early, 409, "held_execution_result_not_ready"),
        "no result before the call finishes (409 held_execution_result_not_ready)",
        early.text,
    )
    r = w.admin.decide(hid, "approve", "looks fine")
    expect(r.status_code == 200 and r.json()["state"] == "approved", "approve", r.text)
    done = wait_held(w.admin.get, hid, {"completed", "failed"})
    expect(done["execution_status"] == "completed", "approved call completed", done)
    expect(
        done["_links"].get("result") and done["_links"].get("execution"),
        "result and execution links once run",
        done["_links"],
    )
    expect(not done["_links"].get("withdraw"), "no withdraw link once decided", done["_links"])
    # The agent reads its own result through the CLI (`jentic api`).
    p = w.cli.run("api", "GET", f"/executions/holds/{hid}/result")
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
    o.note(f"held execution {hid} -> completed, upstream hit once")


def s02_deny_with_reason(w: World, o: Outcome) -> None:
    """Deny with a reason -> the agent reads a 403 problem carrying it."""
    reset_upstream(w.env)
    held = cli_hold(w.cli, f"POST:{w.env.upstream}/orders", "-d", '{"n": 2}')
    hid = held["held_execution_id"]
    r = w.admin.decide(hid, "deny", "not today")
    expect(r.status_code == 200 and r.json()["state"] == "denied", "deny", r.text)
    wait_held(w.admin.get, hid, {"failed"})
    res = result_of(w, hid)
    expect(res.get("type") == "approval_denied" and res.get("status") == 403, "denied problem", res)
    expect("not today" in res.get("detail", ""), "reason reaches the agent", res)
    expect(calls_to(w.env, "/orders") == [], "a denied call never runs")
    again = w.admin.decide(hid, "approve")
    expect(
        is_problem(again, 409, "held_execution_already_settled"),
        "a second decision is refused (409 held_execution_already_settled)",
        again.text,
    )


def _audit(w: World, target_id: str) -> list[dict[str, Any]]:
    r = w.admin.get(
        "/audit",
        params={"target_type": "held_execution", "target_id": target_id, "limit": 50},
    )
    expect(r.status_code == 200, "GET /audit", r.text)
    return list(r.json()["data"])


def _events(w: World, type_: str) -> list[dict[str, Any]]:
    r = w.admin.get("/events", params={"event_type": type_, "limit": 100})
    expect(r.status_code == 200, "GET /events", r.text)
    return [e for e in r.json()["data"] if e.get("type") == type_]


def _events_for(w: World, type_: str, held_execution_id: str) -> list[dict[str, Any]]:
    """``type_`` events whose data names ``held_execution_id``."""
    return [
        e
        for e in _events(w, type_)
        if (e.get("data") or {}).get("held_execution_id") == held_execution_id
    ]


def s03_expiry(w: World, o: Outcome) -> None:
    """Expiry -> the sweep fails the call; an audit entry and an event record it."""
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=3")
    hid = held["held_execution_id"]
    psql(
        w.env,
        "UPDATE admin.held_executions SET expires_at = now() - interval '1 second' "
        f"WHERE id = '{hid}'",
    )
    # Before the sweep runs, a decision on the lapsed hold is already refused.
    late = w.admin.decide(hid, "approve")
    expect(
        is_problem(late, 409, "held_execution_already_settled"),
        "approving a lapsed hold answers 409 held_execution_already_settled",
        late.text,
    )
    o.note("waiting for the worker's expiry sweep (up to ~2.5 min)")
    deadline = time.time() + 170
    state = ""
    while time.time() < deadline:
        state = w.admin.hold(hid).json()["state"]
        if state == "expired":
            break
        time.sleep(3)
    expect(state == "expired", "held execution expired by the sweep", state)
    wait_held(w.admin.get, hid, {"failed"})
    res = result_of(w, hid)
    expect(
        res.get("type") == "approval_expired" and res.get("status") == 403, "expired problem", res
    )
    audits = [a for a in _audit(w, hid) if a.get("action") == "expire"]
    expect(len(audits) == 1, "one expire audit entry", _audit(w, hid))
    expect(audits[0].get("actor_id") == w.cli.agent_id, "attributed to the filing agent", audits[0])
    events = _events_for(w, "execution.approval_expired", hid)
    expect(len(events) == 1, "one execution.approval_expired event", events)
    p = w.cli.run("api", "GET", f"/executions/holds/{hid}")
    seen = w.cli.parse(p.stdout)
    expect(
        seen.get("state") == "expired" and seen.get("execution_status") == "failed",
        "agent sees its held call expired and failed",
        p.stdout,
    )


def s04_withdraw(w: World, o: Outcome) -> None:
    """Withdraw: only the filer; the call is cancelled and never runs."""
    reset_upstream(w.env)
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=4")
    hid = held["held_execution_id"]
    for who, human in (("other user", w.other), ("org admin", w.admin)):
        r = human.withdraw(hid)
        expect(
            is_problem(r, 404, "held_execution_not_found"), f"{who} cannot withdraw (404)", r.text
        )
    p = w.cli.run("api", "POST", f"/executions/holds/{hid}:withdraw")
    body = w.cli.parse(p.stdout)
    expect(
        body.get("state") == "withdrawn", "filer withdraws via `jentic api`", p.stdout + p.stderr
    )
    wait_held(w.admin.get, hid, {"cancelled"})
    again = w.cli.run("api", "POST", f"/executions/holds/{hid}:withdraw")
    expect(
        '"status":409' in again.stdout.replace(" ", ""), "a second withdraw is 409", again.stdout
    )
    expect(calls_to(w.env, "/items") == [], "a withdrawn call never runs")


def s05_join_and_new(w: World, o: Outcome) -> None:
    """An identical retry joins its hold; a different query or body files a new one."""
    a = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=20", "--query", "x=1")
    b = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "x=1", "--query", "limit=20")
    c = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=21", "--query", "x=1")
    hid = "held_execution_id"
    expect(a[hid] == b[hid], "same query (reordered) joins", [a, b])
    expect(a[hid] != c[hid], "a different query files a new hold", [a, c])
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
    expect(dj[hid] == ej[hid], "equivalent JSON body joins", [dj, ej])
    expect(dj[hid] != fj[hid], "a different body files a new hold", [dj, fj])
    requested = _events_for(w, "execution.approval_requested", a[hid])
    expect(len(requested) == 1, "a joined retry emits no second requested event", requested)
    withdraw_all_pending(w)


def s06_pending_cap(w: World, o: Outcome) -> None:
    """The per-agent pending cap (3 here) holds, including under concurrent calls."""
    withdraw_all_pending(w)
    for i in range(3):
        cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", f"limit={600 + i}")
    rc, out, err = cli_execute(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=699")
    body = out.get("body") if isinstance(out.get("body"), dict) else {}
    ptype = (body or {}).get("type") or ""
    expect(
        "approval_pending_limit_reached" in (ptype + err + json.dumps(out)),
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
    pending = w.admin.holds(state="pending", agent_id=w.http.agent_id, limit=100)
    expect(
        codes.count(202) == 3 and codes.count(403) == 5,
        "8 concurrent calls: 3 held, 5 refused",
        codes,
    )
    expect(len(pending) == 3, "exactly 3 pending held executions", [p["id"] for p in pending])
    withdraw_all_pending(w)


def s07_rule_changed_to_deny(w: World, o: Outcome) -> None:
    """A rule changed to deny before the run -> the approved call is denied at run time."""
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
        w.admin.decide(a["held_execution_id"], "approve")
        ha = wait_held(w.admin.get, a["held_execution_id"], {"completed", "failed"})
        if ha["execution_status"] == "completed":
            o.gap(
                "2D: approved right after the rule became deny, the call still ran — the "
                "run-time re-authorisation did not see the fresh rules"
            )
        else:
            o.note("immediate approve after deny: denied at run time")
        # And after the broker's hot-path rule cache (30 s) has lapsed too.
        o.note("waiting 32 s past the broker's rule cache")
        time.sleep(32)
        w.admin.decide(b["held_execution_id"], "approve")
        hb = wait_held(w.admin.get, b["held_execution_id"], {"completed", "failed"})
        expect(hb["execution_status"] == "failed", "approved call denied at run time", hb)
        res = result_of(w, b["held_execution_id"])
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
    hid = r.json()["held_execution_id"]
    w.admin.decide(hid, "approve")
    wait_held(w.admin.get, hid, {"running"}, timeout=30)
    deadline = time.time() + 20
    while time.time() < deadline and not calls_to(w.env, "/slow"):
        time.sleep(0.3)
    expect(len(calls_to(w.env, "/slow")) == 1, "the run reached the upstream once")
    o.note("SIGKILL app + broker (both run workers), then restart")
    subprocess.run([str(ROOT / "tests/e2e_manual/ask_tier/stack.sh"), "restart"], check=True)
    held = wait_held(w.admin.get, hid, {"failed", "completed"}, timeout=120)
    expect(held["execution_status"] == "failed", "the re-claimed approved call is failed", held)
    res = result_of(w, hid)
    expect(res.get("type") == "approval_resume_failed", "approval_resume_failed result", res)
    time.sleep(3)
    expect(len(calls_to(w.env, "/slow")) == 1, "never re-run", calls_to(w.env, "/slow"))


def s09_who_decides(w: World, o: Outcome) -> None:
    """No jobs:* anywhere: the member owner views and decides, the agent follows its own
    call; the agent cannot decide (403); a non-owner sees 404; org:admin decides any."""
    me = w.owner.get("/users/me").json()
    expect(
        not any(str(p).startswith("jobs:") for p in me.get("permissions", [])),
        "the member owner holds no jobs:* permission",
        me,
    )
    # A fresh agent of the member owner's, stripped of its jobs:* grants.
    agent = HttpMcpLane(w.env, w.admin, "ask-nojobs")
    psql(
        w.env,
        f"UPDATE admin.agents SET owner_id = '{w.owner.user_id}' WHERE id = '{agent.agent_id}'",
    )
    _bind(w.admin, agent.agent_id, "nojobs", ASK_RULES)
    psql(
        w.env,
        "DELETE FROM admin.actor_permission_grants "
        f"WHERE actor_id = '{agent.agent_id}' AND permission LIKE 'jobs:%'",
    )
    agent.reissue_token()
    tok = agent.token
    no_jobs = agent_api(w, "GET", "/jobs", token=tok)
    expect(
        no_jobs.status_code == 403, "the agent holds no jobs:read (GET /jobs is 403)", no_jobs.text
    )

    r1 = broker_raw(w, "GET", "/items?limit=90", token=tok)
    r2 = broker_raw(w, "GET", "/items?limit=91", token=tok)
    expect(r1.status_code == r2.status_code == 202, "two holds", [r1.text, r2.text])
    h1, h2 = r1.json()["held_execution_id"], r2.json()["held_execution_id"]

    # The filing agent: reads and lists its own, never decides.
    agent_decide = agent_api(
        w, "POST", f"/executions/holds/{h1}:decide", {"decision": "approve"}, token=tok
    )
    expect(
        is_problem(agent_decide, 403, "held_execution_decide_forbidden"),
        "the filing agent cannot decide (403 held_execution_decide_forbidden)",
        agent_decide.text,
    )
    agent_read = agent_api(w, "GET", f"/executions/holds/{h1}", token=tok)
    expect(
        agent_read.status_code == 200 and agent_read.json()["execution_status"] == "held",
        "the agent reads its own held execution without jobs:read",
        agent_read.text,
    )
    agent_list = agent_api(w, "GET", "/executions/holds", token=tok)
    expect(
        agent_list.status_code == 200 and h1 in [h["id"] for h in agent_list.json()["data"]],
        "the agent lists its own held executions",
        agent_list.text,
    )

    # An unrelated user: as if it did not exist.
    for verb, resp in (
        ("get", w.other.hold(h1)),
        ("result", w.other.held_result(h1)),
        ("decide", w.other.decide(h1, "approve")),
    ):
        expect(
            is_problem(resp, 404, "held_execution_not_found"),
            f"a non-owner {verb} answers 404",
            resp.text,
        )
    expect(h1 not in [h["id"] for h in w.other.holds()], "a non-owner does not list it")

    # The member owner, with no jobs:* permission: lists, reads, decides.
    expect(h1 in [h["id"] for h in w.owner.holds()], "the member owner lists it")
    detail = w.owner.hold(h1)
    expect(
        detail.status_code == 200 and detail.json()["request"]["url"].endswith("/items?limit=90"),
        "the member owner reads the held request",
        detail.text,
    )
    r = w.owner.decide(h1, "approve")
    expect(
        r.status_code == 200 and r.json()["state"] == "approved",
        "the member owner (no jobs:write) approves",
        r.text,
    )
    r = w.admin.decide(h2, "deny", "admin says no")
    expect(r.status_code == 200, "org:admin decides another agent's held execution", r.text)

    # The agent follows its approved call to the end and reads the outcome.
    wait_held(lambda path: agent_api(w, "GET", path, token=tok), h1, {"completed"})
    res = agent_api(w, "GET", f"/executions/holds/{h1}/result", token=tok)
    expect(
        res.status_code == 200 and res.json().get("http_status") == 200,
        "the agent reads its result without jobs:read",
        res.text,
    )
    owner_res = w.owner.held_result(h1)
    expect(owner_res.status_code == 200, "the member owner reads the result", owner_res.text)
    denied = agent_api(w, "GET", f"/executions/holds/{h2}/result", token=tok)
    expect(
        denied.status_code == 200
        and denied.json().get("type") == "approval_denied"
        and "admin says no" in denied.json().get("detail", ""),
        "the agent reads the denial and its reason",
        denied.text,
    )


def s10_post_json_bytes(w: World, o: Outcome) -> None:
    """A held POST replays its Content-Type and body byte for byte."""
    reset_upstream(w.env)
    raw = b'{"z": 1,   "a": [1, 2], "s": "caf\\u00e9"}'
    r = broker_raw(
        w, "POST", "/orders", body=raw, headers={"content-type": "application/json; charset=utf-8"}
    )
    expect(r.status_code == 202, "held", r.text)
    hid = r.json()["held_execution_id"]
    w.admin.decide(hid, "approve")
    wait_held(w.admin.get, hid, {"completed"})
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
    w.admin.decide(held["held_execution_id"], "approve")
    wait_held(w.admin.get, held["held_execution_id"], {"completed"})
    (call,) = calls_to(w.env, "/orders")
    expect(
        (call["headers"].get("content-type") or "").startswith("application/json"),
        "CLI POST keeps JSON",
        call,
    )


def s11_standalone_broker_links(w: World, o: Outcome) -> None:
    """The standalone broker roots review_url and _links on the admin app's origin."""
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=110")
    review = held["review_url"]
    expect(
        review.startswith(f"{w.env.app}/app/agents/approvals/"),
        "review_url on the app origin",
        held,
    )
    for rel in ("self", "result", "withdraw"):
        expect(
            held["_links"][rel].startswith(f"{w.env.app}/executions/holds/"),
            f"_links.{rel} on the app origin",
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
    """Both MCP lanes, with and without URL elicitation, and get_execution_result's wait."""
    target = f"GET:{w.env.upstream}/items"
    # HTTP mount, no elicitation: the held result at once.
    t0 = time.time()
    held = _http_mcp_payload(
        w.http.call("execute", {"operation_id": target, "inputs": {"limit": 120}})
    )
    expect(time.time() - t0 < 15, "held result returned without waiting", time.time() - t0)
    body = held.get("body", {})
    expect(held.get("status") == 202 and body.get("status") == "held", "HTTP MCP held result", held)
    hid = body["held_execution_id"]
    t0 = time.time()
    polled = _http_mcp_payload(
        w.http.call("get_execution_result", {"held_execution_id": hid, "timeout": 5})
    )
    waited = time.time() - t0
    expect(
        polled.get("held_execution_id") == hid
        and polled.get("state") == "pending"
        and polled.get("status") == "held"
        and 4 <= waited < 20,
        "timeout (s) bounds a held poll",
        [polled, waited],
    )
    threading.Timer(3, lambda: w.owner.decide(hid, "approve")).start()
    t0 = time.time()
    done = _http_mcp_payload(
        w.http.call("get_execution_result", {"held_execution_id": hid, "timeout": 30})
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
    pending = w.owner.holds(state="pending", agent_id=w.http.agent_id)
    hid2 = next(h["id"] for h in pending if url.endswith(h["id"]))
    threading.Timer(3, lambda: w.owner.decide(hid2, "approve")).start()
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
        ghid = payload["body"]["held_execution_id"]
        threading.Timer(3, lambda: w.admin.decide(ghid, "approve")).start()
        resp = go.call("get_execution_result", {"held_execution_id": ghid, "timeout": 30})
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
            ghid = next(h["id"] for h in w.admin.holds(state="pending", agent_id=w.gocli.agent_id))
            threading.Timer(3, lambda: w.admin.decide(ghid, "approve")).start()
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


def _only_pending(w: World, agent_id: str, timeout: float = 30) -> str:
    """The id of ``agent_id``'s single pending held execution, once it appears."""
    deadline = time.time() + timeout
    pending: list[dict[str, Any]] = []
    while time.time() < deadline:
        pending = w.admin.holds(state="pending", agent_id=agent_id)
        if len(pending) == 1:
            return str(pending[0]["id"])
        time.sleep(0.5)
    raise StepError(f"expected one pending held execution for {agent_id}", pending)


def s13_cli_held_exit(w: World, o: Outcome) -> None:
    """`jentic execute` on a held call: exit 3, `--wait`, and `jentic executions poll`."""
    withdraw_all_pending(w)
    target = f"GET:{w.env.upstream}/items"
    rc, out, err = cli_execute(w.cli, target, "--query", "limit=130")
    expect(out.get("status") == 202, "held envelope printed", out)
    expect(rc == 3, "a held call exits 3 (TIMEOUT_PENDING)", {"rc": rc, "err": err})
    hid = out["body"]["held_execution_id"]

    # `executions poll` with a short budget on a still-held call: exit 3.
    p = w.cli.run("executions", "poll", hid, "--timeout", "3s", "--json")
    expect(p.returncode == 3, "`executions poll` times out with exit 3", p.stdout + p.stderr)
    # Approved mid-wait: exit 0 with the upstream response.
    threading.Timer(3, lambda: w.admin.decide(hid, "approve")).start()
    p = w.cli.run("executions", "poll", hid, "--json")
    res = w.cli.parse(p.stdout)
    expect(
        p.returncode == 0 and res.get("status") == 200,
        "`executions poll` prints the run's result",
        p.stdout + p.stderr,
    )

    # Denied: `executions poll` exits 2 with the denial.
    _, out, _ = cli_execute(w.cli, target, "--query", "limit=131")
    dhid = out["body"]["held_execution_id"]
    w.admin.decide(dhid, "deny", "cli deny")
    p = w.cli.run("executions", "poll", dhid, "--json")
    expect(
        p.returncode == 2 and "cli deny" in p.stdout + p.stderr,
        "`executions poll` on a denied call exits 2 with the reason",
        p.stdout + p.stderr,
    )

    # `execute --wait`: blocks on the decision, then prints the result.
    # stdin at /dev/null: execute reads no request body from it.
    proc = w.cli.popen(
        "execute", target, "--query", "limit=132", "--wait", "--json", stdin=subprocess.DEVNULL
    )
    try:
        whid = _only_pending(w, w.cli.agent_id)
        w.admin.decide(whid, "approve")
        stdout, stderr = proc.communicate(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
    res = w.cli.parse(stdout)
    expect(
        proc.returncode == 0 and isinstance(res, dict) and res.get("status") == 200,
        "`execute --wait` returns the approved call's result",
        stdout + stderr,
    )
    withdraw_all_pending(w)


def s14_inbox(w: World, o: Outcome) -> None:
    """The inbox's API side: a requires_action event and a pending count that clear on decide."""
    held = cli_hold(w.cli, f"GET:{w.env.upstream}/items", "--query", "limit=140")
    hid = held["held_execution_id"]
    ev = _events_for(w, "execution.approval_requested", hid)
    expect(
        len(ev) == 1 and ev[0].get("requires_action") is True, "requested event requires action", ev
    )
    before = len(w.admin.holds(state="pending"))
    w.admin.decide(hid, "deny", "inbox check")
    after = len(w.admin.holds(state="pending"))
    expect(after == before - 1, "the pending list (badge source) drops it", [before, after])
    decided = _events_for(w, "execution.approval_decided", hid)
    expect(len(decided) == 1, "one execution.approval_decided event", decided)
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
    # The held call's job, read while held_executions still exists.
    job_id = psql(
        w.env,
        f"SELECT job_id FROM admin.held_executions WHERE id = '{held['held_execution_id']}'",
    )
    expect(bool(job_id), "the held execution names its job", job_id)
    stack = str(ROOT / "tests/e2e_manual/ask_tier/stack.sh")
    subprocess.run([stack, "stop"], check=False)
    try:
        _migrate(
            w,
            "from jentic_one.migrations.run import downgrade; "
            "downgrade('control', 'cc3d4e5f6a7b'); "  # pragma: allowlist secret
            "downgrade('admin', '6ae76df5ece5')",  # pragma: allowlist secret
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
        status = psql(w.env, f"SELECT status FROM admin.jobs WHERE id = '{job_id}'")
        expect(status == "cancelled", "the held job was cancelled", status)
        tables = psql(
            w.env,
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'held_executions'",
        )
        expect(tables == "0", "held_executions dropped", tables)
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
    o.note(f"after re-upgrade an ask rule holds again ({again['held_execution_id']})")
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
    hid = hold.json()["held_execution_id"]
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
    pend = [h["id"] for h in w.admin.holds(state="pending", agent_id=agent.agent_id)]
    expect(hid in pend, "the held execution is pending", pend)
    st = w.admin.get(f"/connect-sessions/{sid2}/status")
    expect(st.status_code == 200, "the connect request is readable", st.text)
    o.note(f"pending together: held execution {hid} + connect {sid2} {st.json()}")
    w.admin.decide(hid, "approve")
    wait_held(w.admin.get, hid, {"completed"})
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
