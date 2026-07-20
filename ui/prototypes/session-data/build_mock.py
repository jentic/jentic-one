#!/usr/bin/env python3
"""Generate the consolidated Sessions mock-data JSON from the real flight-ops run.

Reads the three real datasets captured from the multi-agent run:
  - this_run_proxy.jsonl          (LLM proxy: thinking, chat, subagent spawns)
  - this_run_execution_records.json (broker: real tool calls -> APIs)
  - this_run_events.json          (broker/control: credential + denial events)

...and reshapes them into ONE JSON document shaped to the TARGET UI schema
(the schema we want the backend to eventually serve). Every value is either
lifted from the real run or clearly synthesised (session_id/call_id correlation,
verdicts, token cost) to fill the known gaps documented in the plan.

Output: mock/sessions-mock.json
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def load_json(name: str):
    with open(os.path.join(HERE, name)) as f:
        return json.load(f)


def load_jsonl(name: str):
    out = []
    with open(os.path.join(HERE, name)) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---- operation_id -> (method, path, description) map (from jentic apis operations) ----
OP_MAP = {
    "op_c78960519bf8cf7ec56920e2d57307157c15ff4d": ("GET", "/ping", "Test connectivity and health of the API."),
    "op_272c3e0b15bad": ("GET", "/airlines", "Get airline information from the global airlines database."),
    "op_32445359bc5ad": ("GET", "/airports", "Get airport information from the global airports database."),
    "op_4fa4cf71b51cf": ("GET", "/flights", "Get real-time global flight positioning data."),
    "op_704ff4d3af951": ("GET", "/fleets", "Get aircraft fleet information."),
    "op_1c1d97ad4b5a1": ("POST", "/v4/spreadsheets", "Creates a spreadsheet, returning the newly created spreadsheet."),
    "op_73542d6e4366c": ("GET", "/v4/spreadsheets/{spreadsheetId}", "Returns the spreadsheet at the given ID."),
    "op_3cc2e4d0c39f9": ("POST", "/v4/spreadsheets/{spreadsheetId}:batchUpdate", "Applies one or more updates to the spreadsheet."),
    "op_9e0adf40c13c7": ("GET", "/v4/spreadsheets/{spreadsheetId}/values/{range}", "Returns a range of values from a spreadsheet."),
    "op_a22e87c92e2a2": ("POST", "/v4/spreadsheets/{spreadsheetId}/values/{range}:append", "Appends values to a spreadsheet."),
    "op_aae3899eb1114": ("PUT", "/v4/spreadsheets/{spreadsheetId}/values/{range}", "Sets values in a range of a spreadsheet."),
    "op_c9b967a72f78d": ("POST", "/v4/spreadsheets/{spreadsheetId}/values/{range}:clear", "Clears values from a spreadsheet."),
}


def op_lookup(op_id: str):
    if op_id in OP_MAP:
        return OP_MAP[op_id]
    # match by 13-char prefix (records store full 40-char ids)
    for k, v in OP_MAP.items():
        if op_id.startswith(k) or k.startswith(op_id[:13]):
            return v
    return ("POST", "/unknown", "")


# --- token cost model (synthesised — real usage lives in proxy usage field) ---
COST_PER_1K_IN = 0.003   # $ per 1k input tokens  (illustrative Claude-class pricing)
COST_PER_1K_OUT = 0.015  # $ per 1k output tokens


def money(cents: float) -> float:
    return round(cents, 4)


def build():
    proxy = load_jsonl("this_run_proxy.jsonl")
    execs = load_json("this_run_execution_records.json")
    events = load_json("this_run_events.json")

    # ---- Agent tree (from the real run) ----------------------------------
    # Main agent spawned 3 subagents; each subagent spawned 2 sub-subagents.
    # actor_id of the broker calls: agnt_6a564f4f5ef7bc45bdd64220 (main).
    main_actor = execs[0]["actor_id"] if execs else "agnt_main"

    agents = {
        "agent-main": {
            "id": "agent-main", "actor_id": main_actor,
            "name": "Flight-Ops Orchestrator", "role": "main",
            "parent_id": None, "depth": 0,
            "subagent_type": "orchestrator",
            "spawned_at": "2026-07-15T11:29:40Z",
        },
        "agent-a": {
            "id": "agent-a", "actor_id": main_actor,
            "name": "Subagent A — AirLabs ingestion", "role": "subagent",
            "parent_id": "agent-main", "depth": 1,
            "subagent_type": "general-purpose",
            "spawned_at": "2026-07-15T11:30:50Z",
        },
        "agent-b": {
            "id": "agent-b", "actor_id": main_actor,
            "name": "Subagent B — Sheets builder", "role": "subagent",
            "parent_id": "agent-main", "depth": 1,
            "subagent_type": "general-purpose",
            "spawned_at": "2026-07-15T11:30:52Z",
        },
        "agent-c": {
            "id": "agent-c", "actor_id": main_actor,
            "name": "Subagent C — Sync & maintenance", "role": "subagent",
            "parent_id": "agent-main", "depth": 1,
            "subagent_type": "general-purpose",
            "spawned_at": "2026-07-15T11:33:10Z",
        },
        # sub-subagents (leaf workers) — visible only to the proxy in reality;
        # here we surface them so the playground tree has depth 2.
        "agent-a1": {"id": "agent-a1", "actor_id": main_actor, "name": "A·1 — Airlines & airports fetch", "role": "subagent", "parent_id": "agent-a", "depth": 2, "subagent_type": "worker", "spawned_at": "2026-07-15T11:30:58Z"},
        "agent-a2": {"id": "agent-a2", "actor_id": main_actor, "name": "A·2 — Live flights + fleets", "role": "subagent", "parent_id": "agent-a", "depth": 2, "subagent_type": "worker", "spawned_at": "2026-07-15T11:31:05Z"},
        "agent-b1": {"id": "agent-b1", "actor_id": main_actor, "name": "B·1 — Create + structure sheet", "role": "subagent", "parent_id": "agent-b", "depth": 2, "subagent_type": "worker", "spawned_at": "2026-07-15T11:31:20Z"},
        "agent-b2": {"id": "agent-b2", "actor_id": main_actor, "name": "B·2 — Write & append rows", "role": "subagent", "parent_id": "agent-b", "depth": 2, "subagent_type": "worker", "spawned_at": "2026-07-15T11:32:10Z"},
        "agent-c1": {"id": "agent-c1", "actor_id": main_actor, "name": "C·1 — Read-back & verify", "role": "subagent", "parent_id": "agent-c", "depth": 2, "subagent_type": "worker", "spawned_at": "2026-07-15T11:33:20Z"},
        "agent-c2": {"id": "agent-c2", "actor_id": main_actor, "name": "C·2 — Clear & re-sync", "role": "subagent", "parent_id": "agent-c", "depth": 2, "subagent_type": "worker", "spawned_at": "2026-07-15T11:34:00Z"},
    }

    # deterministic assignment of each exec record to a leaf agent by API + method
    def assign_agent(api_name: str, method: str, op_id: str) -> str:
        m, path, _ = op_lookup(op_id)
        if api_name == "airlabs-co":
            if path in ("/flights", "/fleets"):
                return "agent-a2"
            return "agent-a1"
        # google sheets
        if m == "POST" and path == "/v4/spreadsheets":
            return "agent-b1"
        if ":batchUpdate" in path:
            return "agent-b1"
        if ":clear" in path:
            return "agent-c2"
        if m == "GET":
            return "agent-c1"
        return "agent-b2"

    return proxy, execs, events, agents, assign_agent


def build_calls(execs, events, agents, assign_agent):
    """Turn each broker execution_record into a target-schema 'call' object.

    Fills the correlation + governance gaps (session_id, call_id, method, path,
    verdict, tokens, cost, credential) that the raw record lacks today.
    """
    # credential per API (from credential.accessed events)
    cred_by_api = {}
    for e in events:
        if e.get("type") == "credential.accessed":
            d = e.get("data") or {}
            cred_by_api[d.get("api_name")] = {
                "credential_id": d.get("credential_id"),
                "provider": d.get("provider"),
                "wire_type": d.get("wire_type"),
            }

    SESSION_ID = "sess_flightops_2026_07_15"
    calls = []
    for i, r in enumerate(execs):
        api = r["api_name"]
        op_id = r["operation_id"]
        method, path, desc = op_lookup(op_id)
        agent_id = assign_agent(api, method, op_id)
        # synthesised token usage + cost, scaled by method (writes cost more)
        tin = 900 + (i % 5) * 130
        tout = 180 + (i % 4) * 70
        cost = money((tin / 1000) * COST_PER_1K_IN + (tout / 1000) * COST_PER_1K_OUT)
        cred = cred_by_api.get(api) or cred_by_api.get(f"{api}-sheets") or {}
        destructive = ":clear" in path or (method in ("PUT", "POST") and "spreadsheets" in path)
        calls.append({
            "call_id": f"call_{r['id'][5:17]}",
            "session_id": SESSION_ID,
            "execution_id": r["id"],
            "agent_id": agent_id,
            "actor_id": r["actor_id"],
            "actor_type": r["actor_type"],
            # --- the tool call itself (method/path DERIVED from operation_id) ---
            "api_vendor": r["api_vendor"],
            "api_name": api,
            "api_version": r["api_version"],
            "operation_id": op_id,
            "method": method,
            "path": path,
            "summary": desc,
            # --- outcome ---
            "verdict": "allow",              # every call in this run was allowed post-grant
            "status": r["status"],           # completed
            "http_status": r["http_status"],
            "duration_ms": r["duration_ms"],
            "started_at": r["started_at"],
            "error": r["error"],
            "origin": r["origin"],
            "destructive": destructive,
            # --- governance / provenance ---
            "credential_id": cred.get("credential_id"),
            "credential_provider": cred.get("provider"),
            "credential_wire_type": cred.get("wire_type"),
            "trace_id": r["trace_id"],       # 'unknown' in the real run (gap)
            # --- cost/usage (synthesised) ---
            "tokens_in": tin,
            "tokens_out": tout,
            "cost_usd": cost,
        })

    # --- SYNTHESISED demo calls: one DENY + one ERROR so the UI exercises all
    # verdict/outcome states. The real run was 100% allow post-grant. Clearly
    # flagged with "synthesised": true so they can be filtered out later. ---
    calls.append({
        "call_id": "call_synth_deny_01",
        "session_id": SESSION_ID,
        "execution_id": None,
        "agent_id": "agent-c2",
        "actor_id": calls[0]["actor_id"] if calls else "agnt_main",
        "actor_type": "agent",
        "api_vendor": "googleapis-com", "api_name": "googleapis-com-sheets", "api_version": "v4",
        "operation_id": "op_c9b967a72f78d",
        "method": "POST", "path": "/v4/spreadsheets/{spreadsheetId}/values/{range}:clear",
        "summary": "Clears values from a spreadsheet.",
        "verdict": "deny", "status": "denied", "http_status": None,
        "duration_ms": 2, "started_at": "2026-07-15T11:34:12.000000+00:00",
        "error": "PBAC: no rule grants clear on this spreadsheet range",
        "origin": "api", "destructive": True,
        "credential_id": None, "credential_provider": None, "credential_wire_type": None,
        "trace_id": "unknown",
        "tokens_in": 640, "tokens_out": 120, "cost_usd": money(0.64 * COST_PER_1K_IN + 0.12 * COST_PER_1K_OUT),
        "synthesised": True,
    })
    calls.append({
        "call_id": "call_synth_err_01",
        "session_id": SESSION_ID,
        "execution_id": None,
        "agent_id": "agent-a2",
        "actor_id": calls[0]["actor_id"] if calls else "agnt_main",
        "actor_type": "agent",
        "api_vendor": "airlabs-co", "api_name": "airlabs-co", "api_version": "v9",
        "operation_id": "op_4fa4cf71b51cf",
        "method": "GET", "path": "/flights", "summary": "Get real-time global flight positioning data.",
        "verdict": "allow", "status": "failed", "http_status": 429,
        "duration_ms": 812, "started_at": "2026-07-15T11:32:40.000000+00:00",
        "error": "upstream 429 Too Many Requests (rate limited)",
        "origin": "api", "destructive": False,
        "credential_id": "cred_6a576d94dc2cb62e8fc244a3", "credential_provider": "static", "credential_wire_type": "api_key",
        "trace_id": "unknown",
        "tokens_in": 900, "tokens_out": 180, "cost_usd": money(0.9 * COST_PER_1K_IN + 0.18 * COST_PER_1K_OUT),
        "synthesised": True,
    })
    return calls, SESSION_ID


def build_denials(events):
    out = []
    for e in events:
        if e.get("type") == "access_request.denied":
            d = e.get("data") or {}
            out.append({
                "request_id": d.get("request_id"),
                "status": d.get("status"),
                "summary": e.get("summary"),
                "created_at": e.get("created_at"),
                "actor_id": e.get("actor_id"),
            })
    return out


def build_chat(proxy):
    turns = []
    for i, r in enumerate(proxy):
        tool_uses = []
        for t in (r.get("response_tool_uses") or []):
            inp = t.get("input")
            if isinstance(inp, dict):
                preview = inp.get("description") or inp.get("prompt") or json.dumps(inp)
            else:
                preview = str(inp)
            tool_uses.append({"name": t.get("name"), "preview": (preview or "")[:220]})
        turns.append({
            "turn_id": f"turn_{i:03d}",
            "ts": r.get("ts"),
            "model": r.get("model"),
            "n_messages": r.get("n_messages"),
            "first_user_msg": (r.get("first_user_msg") or "")[:400],
            "assistant_text": (r.get("response_text") or "")[:800],
            "tool_uses": tool_uses,
            "latency_ms": r.get("latency_ms"),
            "usage": r.get("usage"),
            "status": r.get("status"),
            # Starting attribution: the proxy transcript is the orchestrator's own
            # thread, so default every turn to the main agent. enrich-sessions-mock.mjs
            # then redistributes a share to each call-making subagent by timestamp
            # (keeping an orchestrator slice) so the per-agent Agent-detail drawer
            # shows real turns instead of 0. Mirrors run 2 (inject_markets_media.py).
            "agent_id": "agent-main",
        })
    return turns


def assemble(proxy, execs, events, agents, assign_agent):
    calls, session_id = build_calls(execs, events, agents, assign_agent)
    denials = build_denials(events)
    chat = build_chat(proxy)

    for a in agents.values():
        a_calls = [c for c in calls if c["agent_id"] == a["id"]]
        a["stats"] = {
            "calls": len(a_calls),
            "allow": sum(1 for c in a_calls if c["verdict"] == "allow"),
            "deny": sum(1 for c in a_calls if c["verdict"] == "deny"),
            "error": sum(1 for c in a_calls if c["status"] != "completed"),
            "cost_usd": money(sum(c["cost_usd"] for c in a_calls)),
            "tokens": sum(c["tokens_in"] + c["tokens_out"] for c in a_calls),
            "apis": sorted({c["api_name"] for c in a_calls}),
        }

    # rollup stats include descendants (so intermediate nodes show subtree totals)
    def descendants(aid):
        kids = [x["id"] for x in agents.values() if x["parent_id"] == aid]
        out = list(kids)
        for k in kids:
            out += descendants(k)
        return out

    for a in agents.values():
        ids = [a["id"]] + descendants(a["id"])
        sub = [c for c in calls if c["agent_id"] in ids]
        a["rollup"] = {
            "calls": len(sub),
            "allow": sum(1 for c in sub if c["verdict"] == "allow"),
            "deny": sum(1 for c in sub if c["verdict"] == "deny"),
            "error": sum(1 for c in sub if c["status"] != "completed"),
            "cost_usd": money(sum(c["cost_usd"] for c in sub)),
            "tokens": sum(c["tokens_in"] + c["tokens_out"] for c in sub),
            "apis": sorted({c["api_name"] for c in sub}),
        }

    total_calls = len(calls)
    total_cost = money(sum(c["cost_usd"] for c in calls))
    total_tokens = sum(c["tokens_in"] + c["tokens_out"] for c in calls)
    apis_touched = sorted({c["api_name"] for c in calls})
    starts = sorted(c["started_at"] for c in calls)

    from collections import defaultdict
    buckets = defaultdict(lambda: {"allow": 0, "deny": 0, "error": 0})
    for c in calls:
        try:
            dt = datetime.fromisoformat(c["started_at"].replace("Z", "+00:00"))
            key = dt.strftime("%H:%M")
        except Exception:
            key = "??:??"
        if c["status"] != "completed":
            buckets[key]["error"] += 1
        else:
            buckets[key][c["verdict"]] += 1
    series = [{"t": k, **v} for k, v in sorted(buckets.items())]

    session = {
        "id": session_id,
        "title": "Flight-Ops: live flights -> Google Sheet report",
        "agent_id": "agent-main",
        "actor_id": agents["agent-main"]["actor_id"],
        "started_at": starts[0] if starts else None,
        "ended_at": starts[-1] if starts else None,
        "status": "completed",
        "tiles": {
            "calls": total_calls,
            "agents": len(agents),
            "apis": len(apis_touched),
            "cost_usd": total_cost,
            "tokens": total_tokens,
        },
        "apis_touched": apis_touched,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "flight-ops multi-agent run 2026-07-15 (proxy + broker + events)",
        "note": (
            "Single source of truth for the LLM Proxy > Sessions UI while there "
            "is no backend. Fields marked SYNTHESISED in the plan (session_id, "
            "call_id, verdict, tokens, cost, method/path derivation) fill the "
            "documented correlation/governance gaps. Swap this file for real API "
            "responses once the backend lands."
        ),
        "sessions": [session],
        "agents": list(agents.values()),
        "calls": calls,
        "chat": chat,
        "denials": denials,
        "charts": {"calls_over_time": series},
    }


if __name__ == "__main__":
    proxy, execs, events, agents, assign_agent = build()
    doc = assemble(proxy, execs, events, agents, assign_agent)
    os.makedirs(os.path.join(HERE, "mock"), exist_ok=True)
    out = os.path.join(HERE, "mock", "sessions-mock.json")
    with open(out, "w") as f:
        json.dump(doc, f, indent=2)
    print("wrote", out)
    print("sessions:", len(doc["sessions"]), "agents:", len(doc["agents"]),
          "calls:", len(doc["calls"]), "chat:", len(doc["chat"]),
          "denials:", len(doc["denials"]), "series:", len(doc["charts"]["calls_over_time"]))
    print("tiles:", doc["sessions"][0]["tiles"])


