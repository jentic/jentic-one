"""Issue #1556 agent-lane e2e scenarios, run against a live stack.

Start the stack first (``tests/e2e_manual/issue_1556/stack.sh up on``), then::

    PYTHONPATH=. uv run python -m tests.e2e_manual.issue_1556.scenarios [--gate on|off] [-k S1]

Every scenario runs on each agent lane it applies to (``cli``, ``gomcp``,
``httpmcp``) and prints one PASS/FAIL line per (scenario, lane); the exit code
is non-zero when anything failed. The human side (review, confirm, reject) is
driven through the control API exactly as the approve page does — token-less,
with the login JWT, echoing the review ``digest`` and ``expected_agent_id``.
"""

from __future__ import annotations

import argparse
import json
import secrets as secrets_mod
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tests.e2e_manual.issue_1556.lanes import (
    CliLane,
    Env,
    GoMcpLane,
    HttpMcpLane,
    Human,
    Lane,
    Result,
    StepError,
    bootstrap_admin,
    expect,
    follow_authorize,
    make_user,
    psql,
)
from tests.e2e_manual.issue_1556.upstream import APIS, ApiFixture

API = {a.key: a for a in APIS}
#: Confirm kind -> the session's resolved_flow.
MANUAL_FLOWS = {
    "api_key": "manual_api_key",  # pragma: allowlist secret
    "bearer": "manual_bearer",
    "basic": "manual_basic",
}


def ident(api: ApiFixture) -> dict[str, str]:
    return {"vendor": api.domain.replace(".", "-"), "name": "api", "version": "1.0.0"}


def target(api: ApiFixture) -> str:
    return f"GET:{api.server_url}{api.path}"


def minimal_rule(api: ApiFixture) -> dict[str, Any]:
    return {"effect": "allow", "match_mode": "exact", "methods": ["GET"], "path": api.path}


@dataclass
class Ctx:
    env: Env
    gate: bool
    admin: Human
    lanes: dict[str, Lane] = field(default_factory=dict)
    users: dict[str, Human] = field(default_factory=dict)
    registrations: dict[str, list[str]] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)


def secret(ctx: Ctx, label: str) -> str:
    """A distinctive secret value, remembered so the redaction check can look for it."""
    value = f"e2e-secret-{label}-{secrets_mod.token_hex(6)}"
    ctx.secrets.append(value)
    return value


# ---------------------------------------------------------------------------
# Agent-side helpers
# ---------------------------------------------------------------------------


def directive_of(res: Result) -> dict[str, Any]:
    d = res.error.get("agent_directive")
    if not isinstance(d, dict):
        raise StepError(f"denial carries no agent_directive: {res.error}")
    return dict(d)


def ensure_imported(lane: Lane, api: ApiFixture, admin: Human | None = None) -> None:
    """Import the catalog entry through the lane unless an earlier lane already did.

    Re-importing an identical spec fails its job (the revision already exists)
    and the job is retried with backoff, so ``import_api`` never converges —
    the lanes take turns importing instead (see the lane rotation in ``main``).
    """
    a = ident(api)
    if admin is not None and admin.get(f"/apis/{a['vendor']}/api/1.0.0").status_code == 200:
        return
    res = lane.import_api(api.api_id)
    expect(res.ok, f"import {api.api_id}", res.error or res.data)


def denied(lane: Lane, api: ApiFixture) -> Result:
    res = lane.execute(target(api))
    expect(not res.ok, f"execute {api.key} is denied before connect", res.data)
    return res


def check_api_directive(ctx: Ctx, lane: Lane, res: Result, api: ApiFixture) -> dict[str, Any]:
    d = directive_of(res)
    p = d["parameters"]
    expect(p.get("connect") == {"api": ident(api)}, "parameters.connect.api names the API", p)
    expect(p.get("suggested_rules") == [minimal_rule(api)], "suggested_rules = minimal rule", p)
    cmd = f"jentic connect --api {ident(api)['vendor']}/api/1.0.0"
    expect(p.get("suggested_command") == cmd, "suggested_command is the --api form", p)
    if lane.name != "cli":
        expect(res.error.get("next_tool") == "request_connection", "next_tool", res.error)
        expect(
            res.error.get("next_tool_arguments") == {"api": ident(api)},
            "next_tool_arguments carries the api target",
            res.error,
        )
        step = str(res.error.get("actionable_step", ""))
        expect(
            step == d.get("human_readable_instruction") and "--api" in step,
            "actionable_step relays the broker's API-target guidance",
            step,
        )
    return d


def connect_from_directive(lane: Lane, res: Result, reason: str = "e2e") -> Result:
    """Start the connect the denial asks for, the way the skill tells each lane to."""
    d = directive_of(res)
    p = d["parameters"]
    rules = p.get("suggested_rules")
    if lane.name == "cli":
        conn = p.get("connect") or {}
        return lane.connect(
            api=conn.get("api"),
            vendor=conn.get("vendor_key"),
            registration=conn.get("registration_id"),
            rules=rules,
            reason=reason,
        )
    args = dict(res.error.get("next_tool_arguments") or {})
    args["requested_permission_rules"] = rules
    args["reason"] = reason
    return lane.connect(raw=args)


def session_of(ctx: Ctx, res: Result, flow: str | None = None) -> str:
    expect(res.ok, "connect returns a session", res.error)
    sid = res.data.get("session_id")
    expect(isinstance(sid, str) and sid.startswith("cs_"), "session_id", res.data)
    url = res.data.get("approval_url")
    expect(url == f"{ctx.env.app}/app/agents?approve={sid}", "token-less approval_url", res.data)
    if flow is not None:
        expect(res.data.get("resolved_flow") == flow, f"resolved_flow {flow}", res.data)
    return str(sid)


def execute_ok(lane: Lane, api: ApiFixture) -> None:
    res = None
    for _ in range(10):
        res = lane.execute(target(api))
        if res.ok:
            break
        time.sleep(1)  # broker binding/rule caches have a short TTL
    assert res is not None
    expect(res.ok and res.status == 200, f"execute {api.key} -> 200 after approval", res.error)
    body = res.data.get("body")
    expect(isinstance(body, dict) and body.get("authenticated") is True, "upstream saw it", body)


def error_code(res: Result) -> str | None:
    e = res.error
    for key in ("error_code",):
        if isinstance(e.get(key), str):
            pass
    details = e.get("details") or {}
    for cand in (
        details.get("code"),
        details.get("error_code"),
        (details.get("problem") or {}).get("code")
        if isinstance(details.get("problem"), dict)
        else None,
        e.get("code"),
        e.get("type"),
    ):
        if isinstance(cand, str) and cand:
            return cand
    return json.dumps(e)


# ---------------------------------------------------------------------------
# Human-side helpers
# ---------------------------------------------------------------------------


def oauth_finish(ctx: Ctx, confirm: Any) -> None:
    expect(confirm.status_code == 200, "confirm", confirm.text)
    body = confirm.json()
    expect(body.get("kind") in ("authorization_code", "reauthorize"), "auth-code confirm", body)
    cb = follow_authorize(ctx.env, body["authorize_url"])
    expect(cb.status_code < 500, f"oauth callback -> {cb.status_code}", cb.text[:500])


def wait_status(ctx: Ctx, sid: str, want: str, error_code: str | None = None) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for _ in range(20):
        r = ctx.admin.status(sid)
        if r.status_code == 200:
            last = r.json()
            if last.get("status") == want and (
                error_code is None or last.get("error_code") == error_code
            ):
                return last
        else:
            last = {"http": r.status_code, "body": r.text}
        time.sleep(1)
    raise StepError(f"session {sid} never reached {want}/{error_code}: {last}")


def set_owner(ctx: Ctx, agent_id: str, owner_id: str) -> None:
    r = ctx.admin.patch(f"/agents/{agent_id}", {"owner_id": owner_id})
    expect(r.status_code == 200, "set agent owner", r.text)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

LANE_SCENARIOS: list[tuple[str, Callable[[Ctx, Lane], None], bool | None]] = []
GLOBAL_SCENARIOS: list[tuple[str, Callable[[Ctx], None], bool | None]] = []


def lane_scenario(name: str, gate: bool | None = True) -> Callable[..., Any]:
    def deco(fn: Callable[[Ctx, Lane], None]) -> Callable[[Ctx, Lane], None]:
        LANE_SCENARIOS.append((name, fn, gate))
        return fn

    return deco


def global_scenario(name: str, gate: bool | None = True) -> Callable[..., Any]:
    def deco(fn: Callable[[Ctx], None]) -> Callable[[Ctx], None]:
        GLOBAL_SCENARIOS.append((name, fn, gate))
        return fn

    return deco


def _manual_connect_flow(
    ctx: Ctx, lane: Lane, api: ApiFixture, kind: str, secret: dict[str, str], approver: Human
) -> str:
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    expect(res.status in (403, None) or lane.name != "cli", "403 denial", res.error)
    check_api_directive(ctx, lane, res, api)
    conn = connect_from_directive(lane, res, reason=f"{lane.name} needs {api.key}")
    flow = MANUAL_FLOWS[kind]
    sid = session_of(ctx, conn, flow)
    rv = approver.review(sid)
    expect(rv.status_code == 200, "token-less review", rv.text)
    data = rv.json()
    expect(data["target_kind"] == "api" and data["can_confirm"] is True, "review", data)
    expect(data["requested_permission_rules"], "requested rules pre-filled", data)
    expect((data.get("agent") or {}).get("agent_id") == lane.agent_id, "review agent", data)
    r = approver.confirm_reviewed(sid, {"kind": kind, **secret})
    expect(r.status_code == 200 and r.json().get("kind") == "connected", "confirm", r.text)
    execute_ok(lane, api)
    return str(r.json()["credential_id"])


@lane_scenario("S1 api key header")
def s1_header(ctx: Ctx, lane: Lane) -> None:
    _manual_connect_flow(
        ctx, lane, API["apikey"], "api_key", {"key": secret(ctx, f"hdr-{lane.name}")}, ctx.admin
    )


@lane_scenario("S1 api key query")
def s1_query(ctx: Ctx, lane: Lane) -> None:
    _manual_connect_flow(
        ctx, lane, API["apikeyq"], "api_key", {"key": secret(ctx, f"qry-{lane.name}")}, ctx.admin
    )


@lane_scenario("S2 bearer approved by org:admin")
def s2_bearer_admin(ctx: Ctx, lane: Lane) -> None:
    owen = ctx.users["owen"]
    set_owner(ctx, lane.agent_id, owen.user_id)
    try:
        cred = _manual_connect_flow(
            ctx,
            lane,
            API["bearer"],
            "bearer",
            {"token": secret(ctx, f"tok-{lane.name}")},
            ctx.admin,
        )
        c = ctx.admin.get(f"/credentials/{cred}").json()
        expect(c.get("created_by") == ctx.admin.user_id, "credential attributed to approver", c)
    finally:
        set_owner(ctx, lane.agent_id, ctx.admin.user_id)


@lane_scenario("S3 repeat ask rotates")
def s3_repeat(ctx: Ctx, lane: Lane) -> None:
    api = API["basic"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    check_api_directive(ctx, lane, res, api)
    waiter = None
    if isinstance(lane, CliLane):
        a = ident(api)
        waiter = lane.popen(
            "connect",
            "--api",
            f"{a['vendor']}/api/1.0.0",
            "--wait",
            "--timeout",
            "90s",
            "--reason",
            "first ask",
        )
        time.sleep(4)
    first = connect_from_directive(lane, res, reason="first ask")
    sid = session_of(ctx, first, "manual_basic")
    second = connect_from_directive(lane, res, reason="second ask")
    sid2 = session_of(ctx, second, "manual_basic")
    expect(sid == sid2, "repeat ask returns the same session", [first.data, second.data])
    rv = ctx.admin.review(sid).json()
    expect(rv["reason"] in ("first ask", "e2e lane test"), "reason not replaced", rv)
    if waiter is not None:
        try:
            out, err = waiter.communicate(timeout=60)
        except Exception:
            waiter.kill()
            raise StepError("old --wait did not end after the repeat ask") from None
        expect(waiter.returncode != 0, "old --wait exits non-zero", [out, err])
        expect("replaced" in (out + err), "old --wait says ended or replaced", [out, err])
    rules = rv["requested_permission_rules"] or [minimal_rule(api)]
    r = ctx.admin.confirm_reviewed(
        sid, {"kind": "basic", "username": "u", "password": "p", "permission_rules": rules}
    )
    expect(r.status_code == 200, "confirm basic", r.text)
    execute_ok(lane, api)


@lane_scenario("S4 oauth awaiting_app own client")
def s4_awaiting_app(ctx: Ctx, lane: Lane) -> None:
    api = API["oauth"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    check_api_directive(ctx, lane, res, api)
    sid = session_of(ctx, connect_from_directive(lane, res), "awaiting_app")
    rv = ctx.admin.review(sid)
    expect(rv.status_code == 200 and rv.json()["state"] == "awaiting_app", "review", rv.text)
    conf = ctx.admin.confirm_reviewed(
        sid,
        {
            "kind": "own_oauth_client",
            "client_id": f"own-{lane.name}",
            "client_secret": "own-secret",  # pragma: allowlist secret
            "confirmed_scopes": ["read"],
        },
    )
    oauth_finish(ctx, conf)
    wait_status(ctx, sid, "connected")
    execute_ok(lane, api)


@lane_scenario("S5 ambiguous shared apps", gate=None)
def s5_ambiguous(ctx: Ctx, lane: Lane) -> None:
    api = API["shared"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    p = directive_of(res)["parameters"]
    conn = p.get("connect") or {}
    expect(conn.get("vendor_key") == ident(api)["vendor"], "directive names the vendor", p)
    expect("registration_id" not in conn, "no registration_id when several match", p)
    first = connect_from_directive(lane, res)
    expect(not first.ok, "unpinned connect is refused", first.data)
    blob = json.dumps(first.error)
    cands = (first.error.get("details") or {}).get("candidates") or []
    expect(len(cands) == 2 and "matches 2 OAuth apps" in blob, "ambiguous_vendor", first.error)
    expect("your user" in str(first.error.get("actionable_step")), "ask the user", first.error)
    regs = ctx.registrations["shared"]
    for reg in regs:
        expect(reg in blob, "candidates list every registration", first.error)
    if lane.name == "cli":
        pinned = lane.connect(
            vendor=conn["vendor_key"], registration=regs[1], rules=p.get("suggested_rules")
        )
    else:
        args = dict(
            first.error.get("next_tool_arguments") or res.error.get("next_tool_arguments") or {}
        )
        args["oauth_app_registration_id"] = regs[1]
        args["requested_permission_rules"] = p.get("suggested_rules")
        pinned = lane.connect(raw=args)
    sid = session_of(ctx, pinned, "authorization_code")
    rv = ctx.admin.review(sid).json()
    conf = ctx.admin.confirm(
        sid,
        {
            "confirmed_scopes": ["read"],
            "permission_rules": rv["requested_permission_rules"],
            "digest": rv["digest"],
            "expected_agent_id": lane.agent_id,
        },
    )
    oauth_finish(ctx, conf)
    wait_status(ctx, sid, "connected")
    execute_ok(lane, api)


@lane_scenario("P1 single shared app via directive", gate=None)
def p1_single(ctx: Ctx, lane: Lane) -> None:
    api = API["single"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    p = directive_of(res)["parameters"]
    reg = ctx.registrations["single"][0]
    expect(
        p.get("connect") == {"vendor_key": ident(api)["vendor"], "registration_id": reg},
        "directive names vendor + registration_id",
        p,
    )
    if lane.name != "cli":
        expect(
            res.error.get("next_tool_arguments")
            == {"vendor": ident(api)["vendor"], "oauth_app_registration_id": reg},
            "next_tool_arguments pins the registration",
            res.error,
        )
    else:
        cmd = str(p.get("suggested_command"))
        expect(f"--registration {reg}" in cmd, "suggested_command pins the registration", p)
    sid = session_of(ctx, connect_from_directive(lane, res), "authorization_code")
    rv = ctx.admin.review(sid).json()
    conf = ctx.admin.confirm(
        sid,
        {"confirmed_scopes": ["read"], "permission_rules": rv["requested_permission_rules"]},
    )
    oauth_finish(ctx, conf)
    wait_status(ctx, sid, "connected")
    execute_ok(lane, api)


@lane_scenario("V config vendor connect", gate=None)
def v_vendor(ctx: Ctx, lane: Lane) -> None:
    api = API["vendor"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    p = directive_of(res)["parameters"]
    expect(p.get("connect") == {"vendor_key": "e2evendor"}, "directive names the config vendor", p)
    expect(p.get("suggested_command") == "jentic connect e2evendor", "suggested_command", p)
    if lane.name != "cli":
        expect(res.error.get("next_tool_arguments") == {"vendor": "e2evendor"}, "nta", res.error)
    sid = session_of(ctx, connect_from_directive(lane, res), "authorization_code")
    rv = ctx.admin.review(sid).json()
    conf = ctx.admin.confirm(
        sid,
        {"confirmed_scopes": ["read"], "permission_rules": rv["requested_permission_rules"]},
    )
    oauth_finish(ctx, conf)
    wait_status(ctx, sid, "connected")
    execute_ok(lane, api)


@lane_scenario("S6 reject + cooldown")
def s6_reject(ctx: Ctx, lane: Lane) -> None:
    api = API["reject"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    waiter = None
    if isinstance(lane, CliLane):
        a = ident(api)
        waiter = lane.popen(
            "connect",
            "--api",
            f"{a['vendor']}/api/1.0.0",
            "--wait",
            "--timeout",
            "90s",
        )
        time.sleep(4)
    conn = connect_from_directive(lane, res)
    sid = session_of(ctx, conn, "manual_bearer")
    r = ctx.admin.reject(sid)
    expect(r.status_code == 204, "reject", r.text)
    st = wait_status(ctx, sid, "failed", "rejected")
    expect(st.get("error_code") == "rejected", "status failed/rejected", st)
    if waiter is not None:
        out, err = waiter.communicate(timeout=60)
        expect(waiter.returncode != 0, "--wait exits non-zero on reject", [out, err])
        expect("reject" in (out + err).lower(), "--wait reports the rejection", [out, err])
    again = connect_from_directive(lane, res)
    expect(not again.ok, "re-ask after reject is refused", again.data)
    blob = json.dumps(again.error)
    expect(
        "recently rejected" in blob and "Do not ask again" in blob, "429 recently_rejected", blob
    )


@lane_scenario("S6 expiry")
def s6_expire(ctx: Ctx, lane: Lane) -> None:
    api = API["expire"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    sid = session_of(ctx, connect_from_directive(lane, res), "manual_bearer")
    psql(
        ctx.env,
        "UPDATE control.connect_sessions SET created_at = now() - interval '73 hours' "
        f"WHERE id = '{sid}'",
    )
    wait_status(ctx, sid, "expired")
    rv = ctx.admin.review(sid)
    expect(rv.status_code in (403, 404, 409), "expired session is not reviewable", rv.text)
    again = connect_from_directive(lane, res)
    sid2 = session_of(ctx, again, "manual_bearer")
    expect(sid2 != sid, "re-ask after expiry opens a new session", again.data)
    r = ctx.admin.reject(sid2)
    expect(r.status_code == 204, "clean up with reject", r.text)


@lane_scenario("E connect resolution errors")
def e_resolution(ctx: Ctx, lane: Lane) -> None:
    """How ``:connect`` picks the state, from the agent's side (plan table)."""
    mixed, noauth = API["mixed"], API["noauth"]
    for api in (mixed, noauth):
        ensure_imported(lane, api, ctx.admin)
    # OAuth + a key scheme, no auth_type -> 400 auth_type_required with options.
    r = lane.connect(api=ident(mixed), rules=[minimal_rule(mixed)])
    expect(not r.ok, "mixed API without auth_type is refused", r.data)
    opts = (r.error.get("details") or {}).get("options") or []
    expect(set(opts) >= {"apiKey", "oauth"} or len(opts) >= 2, "options listed", r.error)
    # Pick the key scheme -> manual_api_key.
    r = lane.connect(api=ident(mixed), auth_type="api_key", rules=[minimal_rule(mixed)])
    sid = session_of(ctx, r, "manual_api_key")
    # An undeclared scheme -> 422 auth_type_not_declared.
    r = lane.connect(api=ident(mixed), auth_type="basic", rules=[minimal_rule(mixed)])
    expect(not r.ok and "not one the API" in json.dumps(r.error), "undeclared scheme", r.error)
    # No declared scheme -> 409 no_declared_scheme, do not retry.
    r = lane.connect(api=ident(noauth), rules=[minimal_rule(noauth)])
    expect(not r.ok and "declares no auth scheme" in json.dumps(r.error), "no scheme", r.error)
    # An API-target OAuth connect resolves shared apps like the vendor key does:
    # several -> ambiguous with candidates; exactly one -> created OAuth flow.
    r = lane.connect(api=ident(API["shared"]), rules=[minimal_rule(API["shared"])])
    expect(
        not r.ok and len((r.error.get("details") or {}).get("candidates") or []) == 2,
        "api target with two shared apps is ambiguous",
        r.error,
    )
    r = lane.connect(api=ident(API["single"]), rules=[minimal_rule(API["single"])])
    expect(
        r.ok and r.data.get("resolved_flow") == "authorization_code",
        "api target with one shared app resolves it",
        r.data or r.error,
    )
    for open_sid in (sid, r.data.get("session_id")):
        ctx.admin.post(f"/connect-sessions/{open_sid}:cancel")
    # Unknown API -> unknown_api.
    r = lane.connect(api={"vendor": "nope-test", "name": "api", "version": "1.0.0"})
    expect(not r.ok, "unknown api refused", r.data)


@global_scenario("H confirm guards (digest, rules, kind)")
def h_confirm_guards(ctx: Ctx) -> None:
    api = API["perms"]
    lane = HttpMcpLane(ctx.env, ctx.admin, name="guards-agent")
    ensure_imported(lane, api, ctx.admin)
    sid = session_of(ctx, lane.connect(api=ident(api)), "manual_bearer")
    rv = ctx.admin.review(sid).json()
    base = {"kind": "bearer", "token": "t", "expected_agent_id": lane.agent_id}
    r = ctx.admin.confirm(
        sid, {**base, "digest": "0" * 64, "permission_rules": [minimal_rule(api)]}
    )
    expect(r.status_code == 409 and "review_stale" in r.text, "digest mismatch", r.text)
    r = ctx.admin.confirm(sid, {**base, "digest": rv["digest"], "permission_rules": []})
    expect(r.status_code == 422 and "rules_required" in r.text, "rules required", r.text)
    r = ctx.admin.confirm(
        sid,
        {
            "expected_agent_id": lane.agent_id,
            "kind": "api_key",
            "key": "k",
            "digest": rv["digest"],
            "permission_rules": [minimal_rule(api)],
        },
    )
    expect(r.status_code == 400 and "confirm_kind_mismatch" in r.text, "kind mismatch", r.text)
    r = ctx.admin.confirm(
        sid,
        {
            **base,
            "expected_agent_id": "agnt_x",
            "digest": rv["digest"],
            "permission_rules": [minimal_rule(api)],
        },
    )
    expect(r.status_code == 409 and "review_stale" in r.text, "agent mismatch", r.text)
    r = ctx.admin.confirm(
        sid, {**base, "digest": rv["digest"], "permission_rules": [minimal_rule(api)]}
    )
    expect(r.status_code == 200, "confirm", r.text)
    execute_ok(lane, api)


@global_scenario("S6 agent archived mid-session")
def s6_archive(ctx: Ctx) -> None:
    api = API["archive"]
    lane = HttpMcpLane(ctx.env, ctx.admin, name="archive-agent")
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    sid = session_of(ctx, connect_from_directive(lane, res), "manual_bearer")
    rv = ctx.admin.review(sid).json()
    r = ctx.admin.delete(f"/agents/{lane.agent_id}")
    expect(r.status_code in (200, 204), "archive agent", r.text)
    conf = ctx.admin.confirm(
        sid,
        {
            "kind": "bearer",
            "token": "x",
            "digest": rv["digest"],
            "expected_agent_id": lane.agent_id,
            "permission_rules": rv["requested_permission_rules"],
        },
    )
    expect(conf.status_code == 409, "confirm refused after archive", conf.text)
    expect("agent_inactive" in conf.text or "archived" in conf.text, "agent inactive", conf.text)
    # Cancelling the agent's open sessions on archive is follow-up F1 (not built).


@global_scenario("S7 owner without agents:write / non-owner")
def s7_perms(ctx: Ctx) -> None:
    api = API["perms"]
    nowrite = ctx.users["nowrite"]
    other = ctx.users["other"]
    lane = HttpMcpLane(ctx.env, ctx.admin, name="perms-agent")
    set_owner(ctx, lane.agent_id, nowrite.user_id)
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    sid = session_of(ctx, connect_from_directive(lane, res), "manual_bearer")
    rv = nowrite.review(sid)
    expect(rv.status_code == 403, "owner without agents:write is not an approver", rv.text)
    admin_rv = ctx.admin.review(sid).json()
    conf = nowrite.confirm(
        sid,
        {
            "kind": "bearer",
            "token": "x",
            "digest": admin_rv["digest"],
            "expected_agent_id": lane.agent_id,
            "permission_rules": admin_rv["requested_permission_rules"],
        },
    )
    expect(conf.status_code == 403, "owner without agents:write cannot confirm", conf.text)
    rej = nowrite.reject(sid)
    expect(rej.status_code == 403, "owner without agents:write cannot reject", rej.text)
    o1 = other.review(sid)
    o2 = other.review("cs_" + "0" * 24)
    expect(o1.status_code == 403 and o2.status_code == 403, "non-owner 403", [o1.text, o2.text])
    same = [{k: v for k, v in o.json().items() if k != "instance"} for o in (o1, o2)]
    expect(same[0] == same[1], "non-owner 403 is uniform", same)
    oc = other.confirm(sid, {"kind": "bearer", "token": "x", "digest": "d", "permission_rules": []})
    expect(oc.status_code == 403, "non-owner cannot confirm", oc.text)
    r = ctx.admin.confirm_reviewed(sid, {"kind": "bearer", "token": "ok"})
    expect(r.status_code == 200, "org:admin confirms", r.text)
    execute_ok(lane, api)


@global_scenario("S4 existing credential bind / narrower / re-authorize")
def s4_bind(ctx: Ctx) -> None:
    api = API["oauthbind"]
    a = HttpMcpLane(ctx.env, ctx.admin, name="bind-a")
    b = HttpMcpLane(ctx.env, ctx.admin, name="bind-b")
    c = HttpMcpLane(ctx.env, ctx.admin, name="bind-c")
    ensure_imported(a, api, ctx.admin)
    # A: awaiting_app -> own client, granted [read].
    sid_a = session_of(ctx, a.connect(api=ident(api), scopes=["read"], rules=[minimal_rule(api)]))
    conf = ctx.admin.confirm_reviewed(
        sid_a,
        {
            "kind": "own_oauth_client",
            "client_id": "bind-client",
            "client_secret": "s",
            "confirmed_scopes": ["read"],
        },
    )
    oauth_finish(ctx, conf)
    wait_status(ctx, sid_a, "connected")
    execute_ok(a, api)
    cred = ctx.admin.get(f"/connect-sessions/{sid_a}").json()
    # B asks for [read]: granted covers it -> bind.
    sid_b = session_of(ctx, b.connect(api=ident(api), scopes=["read"], rules=[minimal_rule(api)]))
    rv = ctx.admin.review(sid_b).json()
    existing = rv.get("existing_credentials") or []
    expect(len(existing) == 1 and existing[0]["can_bind"] is True, "B can bind A's cred", rv)
    cred_id = existing[0]["credential_id"]
    r = ctx.admin.confirm_reviewed(sid_b, {"kind": "existing_credential", "credential_id": cred_id})
    expect(r.status_code == 200 and r.json()["kind"] == "connected", "bind existing", r.text)
    execute_ok(b, api)
    # C asks for [read, write]: narrower -> no bind, no re-authorize (A, B bound).
    sid_c = session_of(
        ctx, c.connect(api=ident(api), scopes=["read", "write"], rules=[minimal_rule(api)])
    )
    rv = ctx.admin.review(sid_c).json()
    ex = next(e for e in rv["existing_credentials"] if e["credential_id"] == cred_id)
    expect(ex["can_bind"] is False and ex["missing_scopes"] == ["write"], "narrower", ex)
    expect(ex["can_reauthorize"] is False, "no re-authorize while others are bound", ex)
    expect(set(ex["other_bound_agent_ids"]) == {a.agent_id, b.agent_id}, "lists bound", ex)
    r = ctx.admin.confirm_reviewed(sid_c, {"kind": "existing_credential", "credential_id": cred_id})
    expect(r.status_code in (409, 422), "binding a narrower credential is refused", r.text)
    r = ctx.admin.confirm_reviewed(sid_c, {"kind": "reauthorize", "credential_id": cred_id})
    expect(r.status_code in (409, 422), "re-authorize refused while others are bound", r.text)
    # Suspending a binding keeps it counted (``:resume`` would hand the agent
    # the wider grant); purging A and B makes re-authorize available.
    d = ctx.admin.delete(f"/agents/{a.agent_id}/credentials/{cred_id}")
    expect(d.status_code in (200, 204), "suspend A", d.text)
    rv = ctx.admin.review(sid_c).json()
    ex = next(e for e in rv["existing_credentials"] if e["credential_id"] == cred_id)
    expect(ex["can_reauthorize"] is False, "a suspended binding still counts", ex)
    for agent in (a, b):
        d = ctx.admin.delete(f"/agents/{agent.agent_id}/credentials/{cred_id}?purge=true")
        expect(d.status_code in (200, 204), "purge binding", d.text)
    rv = ctx.admin.review(sid_c).json()
    ex = next(e for e in rv["existing_credentials"] if e["credential_id"] == cred_id)
    expect(ex["can_reauthorize"] is True, "re-authorize offered once nobody else is bound", ex)
    r = ctx.admin.confirm_reviewed(sid_c, {"kind": "reauthorize", "credential_id": cred_id})
    oauth_finish(ctx, r)
    execute_ok(c, api)
    _ = cred


@lane_scenario("G gate off: api target refused", gate=False)
def g_api_refused(ctx: Ctx, lane: Lane) -> None:
    api = API["gateoff"]
    ensure_imported(lane, api, ctx.admin)
    res = denied(lane, api)
    p = directive_of(res)["parameters"]
    expect("connect" not in p, "no connect target without the gate", p)
    expect("suggested_command" not in p, "no --api suggested_command", p)
    expect(p.get("suggested_rules") == [minimal_rule(api)], "suggested_rules still ride", p)
    if lane.name != "cli":
        expect(res.error.get("next_tool") == "whoami", "next_tool stays whoami", res.error)
    conn = lane.connect(api=ident(api), rules=[minimal_rule(api)])
    expect(not conn.ok, "api connect refused", conn.data)
    blob = json.dumps(conn.error)
    expect("does not take connect requests" in blob, "manual_flows_disabled (404)", conn.error)
    expect("Report the gap" in blob, "fall back to reporting the gap once", conn.error)


@global_scenario("R secrets never leak (responses, audit, events, logs)", gate=True)
def r_redaction(ctx: Ctx) -> None:
    expect(bool(ctx.secrets), "earlier scenarios entered secrets")
    blobs: dict[str, str] = {}
    for name in ("app", "broker"):
        blobs[f"{name}.log"] = (ctx.env.work / f"{name}.log").read_text(errors="replace")
    blobs["audit"] = ctx.admin.get("/audit", params={"limit": 200}).text
    blobs["events"] = ctx.admin.get("/events", params={"limit": 200}).text
    blobs["credentials"] = ctx.admin.get("/credentials", params={"limit": 200}).text
    blobs["sessions"] = ctx.admin.get("/connect-sessions", params={"limit": 200}).text
    for value in ctx.secrets:
        for where, text in blobs.items():
            expect(value not in text, f"secret leaked into {where}", value)
    expect(
        "connect_session" in blobs["audit"] or "credential" in blobs["audit"],
        "audit has rows",
        blobs["audit"][:500],
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def setup(env: Env, gate: bool) -> Ctx:
    admin = bootstrap_admin(env)
    ctx = Ctx(env=env, gate=gate, admin=admin)
    ctx.users["owen"] = make_user(
        env,
        admin,
        "owen@e2e.test",
        ["agents:read", "agents:write", "credentials:read", "credentials:write"],
    )
    ctx.users["nowrite"] = make_user(
        env, admin, "nowrite@e2e.test", ["agents:read", "credentials:read", "credentials:write"]
    )
    ctx.users["other"] = make_user(
        env,
        admin,
        "other@e2e.test",
        ["agents:read", "agents:write", "credentials:read", "credentials:write"],
    )
    # Shared OAuth apps (Phase 1/2): two for "shared", one for "single".
    existing = admin.get("/oauth-app-registrations", params={"limit": 100}).json().get("data", [])
    for key, count in (("shared", 2), ("single", 1)):
        api = API[key]
        have = [r["id"] for r in existing if r.get("catalog_api_id") == api.api_id]
        while len(have) < count:
            r = admin.post(
                "/oauth-app-registrations",
                {
                    "name": f"{key} app {len(have) + 1}",
                    "api_vendor": ident(api)["vendor"],
                    "catalog_api_id": api.api_id,
                    "display_name": f"E2E {key}",
                    "flow_kind": "authorization_code",
                    "client_id": f"{key}-client-{len(have) + 1}",
                    "client_secret": "shared-secret",  # pragma: allowlist secret
                    "authorize_url": f"{env.fake_as}/authorize",
                    "token_url": f"{env.fake_as}/token",
                    "default_scopes": ["read"],
                },
            )
            expect(r.status_code == 201, f"register shared app {key}", r.text)
            have.append(r.json()["id"])
        ctx.registrations[key] = have
    cli = CliLane(env, admin, "cli-agent")
    gocli = CliLane(env, admin, "gomcp-agent", lane="gomcp")
    ctx.lanes["cli"] = cli
    ctx.lanes["gomcp"] = GoMcpLane(gocli)
    ctx.lanes["httpmcp"] = HttpMcpLane(env, admin, name="httpmcp-agent")
    return ctx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", choices=["on", "off"], default="on")
    ap.add_argument("-k", default="", help="substring filter on scenario names")
    ap.add_argument("--lanes", default="cli,gomcp,httpmcp")
    args = ap.parse_args()
    gate = args.gate == "on"
    env = Env(db="jentic_lanes_on" if gate else "jentic_lanes_off")
    ctx = setup(env, gate)
    results: list[tuple[str, str, str, str]] = []
    lanes = [ctx.lanes[n] for n in args.lanes.split(",")]
    for index, (name, fn, g) in enumerate(LANE_SCENARIOS):
        if (g is not None and g != gate) or args.k not in name:
            continue
        # Rotate which lane goes first, so each lane gets to import an API.
        order = lanes[index % len(lanes) :] + lanes[: index % len(lanes)]
        for lane in order:
            try:
                fn(ctx, lane)
                results.append((name, lane.name, "PASS", ""))
            except Exception as exc:
                results.append((name, lane.name, "FAIL", f"{exc}"))
                traceback.print_exc()
            print(f"{results[-1][2]:4}  {name} [{lane.name}] {results[-1][3][:1500]}", flush=True)
    for name, gfn, g in GLOBAL_SCENARIOS:
        if (g is not None and g != gate) or args.k not in name:
            continue
        try:
            gfn(ctx)
            results.append((name, "-", "PASS", ""))
        except Exception as exc:
            results.append((name, "-", "FAIL", f"{exc}"))
            traceback.print_exc()
        print(f"{results[-1][2]:4}  {name} {results[-1][3][:1500]}", flush=True)
    for lane in ctx.lanes.values():
        lane.close()
    print("\nSUMMARY")
    for r in results:
        print(f"  {r[2]:4}  {r[0]} [{r[1]}]")
    return 1 if any(r[2] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
