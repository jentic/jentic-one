#!/usr/bin/env python3
"""Enrich sessions-mock.json with 2 extra fully-navigable demo sessions so the
Sessions table + overview have multiple rows. The original flight-ops session
stays the rich, real one; the two added sessions are smaller, clearly-derived
demos (their calls carry "synthesised": true) that still resolve in the
playground (agents + calls + a couple of chat turns each).
"""
import json
import copy

PATH = "sessions-mock.json"
doc = json.load(open(PATH))

# only add if not already enriched (idempotent)
existing = {s["id"] for s in doc["sessions"]}


def money(x):
    return round(x, 4)


def make_session(sid, title, actor, start, apis, calls_spec, agent_specs):
    agents = []
    for a in agent_specs:
        agents.append({
            "id": a["id"], "actor_id": actor, "name": a["name"], "role": a["role"],
            "parent_id": a["parent"], "depth": a["depth"], "subagent_type": a["type"],
            "spawned_at": start,
            "stats": {"calls": 0, "allow": 0, "deny": 0, "error": 0, "cost_usd": 0, "tokens": 0, "apis": []},
            "rollup": {"calls": 0, "allow": 0, "deny": 0, "error": 0, "cost_usd": 0, "tokens": 0, "apis": []},
        })
    calls = []
    for i, c in enumerate(calls_spec):
        tin, tout = 800 + i * 90, 160 + i * 40
        calls.append({
            "call_id": f"call_{sid[5:12]}_{i:02d}", "session_id": sid, "execution_id": None,
            "agent_id": c["agent"], "actor_id": actor, "actor_type": "agent",
            "api_vendor": c["vendor"], "api_name": c["api"], "api_version": c["ver"],
            "operation_id": c["op"], "method": c["method"], "path": c["path"], "summary": c["summary"],
            "verdict": c.get("verdict", "allow"), "status": c.get("status", "completed"),
            "http_status": c.get("http", 200), "duration_ms": c.get("dur", 40),
            "started_at": c["ts"], "error": c.get("error"), "origin": "api",
            "destructive": c.get("destructive", False),
            "credential_id": c.get("cred"), "credential_provider": c.get("prov"),
            "credential_wire_type": c.get("wire"), "trace_id": "unknown",
            "tokens_in": tin, "tokens_out": tout,
            "cost_usd": money(tin / 1000 * 0.003 + tout / 1000 * 0.015),
            "synthesised": True,
        })
    # rollups
    def descendants(aid):
        kids = [x["id"] for x in agents if x["parent_id"] == aid]
        out = list(kids)
        for k in kids:
            out += descendants(k)
        return out
    for a in agents:
        own = [c for c in calls if c["agent_id"] == a["id"]]
        ids = [a["id"], *descendants(a["id"])]
        sub = [c for c in calls if c["agent_id"] in ids]

        def roll(cs):
            return {
                "calls": len(cs),
                "allow": sum(1 for c in cs if c["verdict"] == "allow"),
                "deny": sum(1 for c in cs if c["verdict"] == "deny"),
                "error": sum(1 for c in cs if c["status"] != "completed"),
                "cost_usd": money(sum(c["cost_usd"] for c in cs)),
                "tokens": sum(c["tokens_in"] + c["tokens_out"] for c in cs),
                "apis": sorted({c["api_name"] for c in cs}),
            }
        a["stats"], a["rollup"] = roll(own), roll(sub)
    tiles = {
        "calls": len(calls), "agents": len(agents), "apis": len(apis),
        "cost_usd": money(sum(c["cost_usd"] for c in calls)),
        "tokens": sum(c["tokens_in"] + c["tokens_out"] for c in calls),
    }
    session = {
        "id": sid, "title": title, "agent_id": agent_specs[0]["id"], "actor_id": actor,
        "started_at": start, "ended_at": calls[-1]["started_at"] if calls else start,
        "status": "completed", "tiles": tiles, "apis_touched": apis,
    }
    chat = [{
        "turn_id": f"{sid}_turn_000", "ts": start, "model": "claude-sonnet-4",
        "n_messages": 3, "first_user_msg": title,
        "assistant_text": f"Planning {title.lower()} and dispatching tool calls.",
        "tool_uses": [{"name": "Agent", "preview": agent_specs[1]["name"] if len(agent_specs) > 1 else "worker"}],
        "latency_ms": 1800, "usage": {"input_tokens": 900, "output_tokens": 210}, "status": "success",
    }]
    return session, agents, calls, chat


ADDED = []

if "sess_newsroom_2026_07_14" not in existing:
    ADDED.append(make_session(
        "sess_newsroom_2026_07_14",
        "Newsroom: pull headlines -> summarise -> post to sheet",
        "agnt_7b101c2d3e4f5a6b7c8d9e0f",
        "2026-07-14T09:12:00+00:00",
        ["nytimes-com", "googleapis-com-sheets"],
        [
            {"agent": "nr-a", "vendor": "nytimes-com", "api": "nytimes-com", "ver": "v3",
             "op": "op_topstories", "method": "GET", "path": "/topstories/v2/home.json",
             "summary": "Get top stories for a section.", "ts": "2026-07-14T09:12:10+00:00",
             "cred": "cred_nyt01", "prov": "static", "wire": "api_key"},
            {"agent": "nr-a", "vendor": "nytimes-com", "api": "nytimes-com", "ver": "v3",
             "op": "op_articlesearch", "method": "GET", "path": "/search/v2/articlesearch.json",
             "summary": "Search NYT articles.", "ts": "2026-07-14T09:12:22+00:00",
             "cred": "cred_nyt01", "prov": "static", "wire": "api_key"},
            {"agent": "nr-b", "vendor": "googleapis-com", "api": "googleapis-com-sheets", "ver": "v4",
             "op": "op_1c1d97ad4b5a1", "method": "POST", "path": "/v4/spreadsheets",
             "summary": "Creates a spreadsheet.", "ts": "2026-07-14T09:12:40+00:00",
             "destructive": True, "cred": "cred_gsheets02", "prov": "direct_oauth2", "wire": "oauth2"},
            {"agent": "nr-b", "vendor": "googleapis-com", "api": "googleapis-com-sheets", "ver": "v4",
             "op": "op_a22e87c92e2a2", "method": "POST",
             "path": "/v4/spreadsheets/{spreadsheetId}/values/{range}:append",
             "summary": "Appends values to a spreadsheet.", "ts": "2026-07-14T09:12:55+00:00",
             "destructive": True, "cred": "cred_gsheets02", "prov": "direct_oauth2", "wire": "oauth2"},
        ],
        [
            {"id": "nr-main", "name": "Newsroom Orchestrator", "role": "main", "parent": None, "depth": 0, "type": "orchestrator"},
            {"id": "nr-a", "name": "Fetcher — NYT headlines", "role": "subagent", "parent": "nr-main", "depth": 1, "type": "general-purpose"},
            {"id": "nr-b", "name": "Publisher — Sheets", "role": "subagent", "parent": "nr-main", "depth": 1, "type": "general-purpose"},
        ],
    ))

if "sess_fx_2026_07_13" not in existing:
    ADDED.append(make_session(
        "sess_fx_2026_07_13",
        "FX rates: fetch quotes (one denied write)",
        "agnt_8c202d3e4f5a6b7c8d9e0f10",
        "2026-07-13T16:40:00+00:00",
        ["1forge-com", "googleapis-com-sheets"],
        [
            {"agent": "fx-a", "vendor": "1forge-com", "api": "1forge-com", "ver": "v1",
             "op": "op_quotes", "method": "GET", "path": "/quotes",
             "summary": "Get FX quotes.", "ts": "2026-07-13T16:40:08+00:00",
             "cred": "cred_1forge01", "prov": "static", "wire": "api_key"},
            {"agent": "fx-a", "vendor": "1forge-com", "api": "1forge-com", "ver": "v1",
             "op": "op_symbols", "method": "GET", "path": "/symbols",
             "summary": "List available symbols.", "ts": "2026-07-13T16:40:16+00:00",
             "cred": "cred_1forge01", "prov": "static", "wire": "api_key"},
            {"agent": "fx-b", "vendor": "googleapis-com", "api": "googleapis-com-sheets", "ver": "v4",
             "op": "op_aae3899eb1114", "method": "PUT",
             "path": "/v4/spreadsheets/{spreadsheetId}/values/{range}",
             "summary": "Sets values in a range.", "ts": "2026-07-13T16:40:30+00:00",
             "verdict": "deny", "status": "denied", "http": None, "dur": 2, "destructive": True,
             "error": "PBAC: no rule grants write to this spreadsheet"},
        ],
        [
            {"id": "fx-main", "name": "FX Orchestrator", "role": "main", "parent": None, "depth": 0, "type": "orchestrator"},
            {"id": "fx-a", "name": "Quotes fetcher", "role": "subagent", "parent": "fx-main", "depth": 1, "type": "general-purpose"},
            {"id": "fx-b", "name": "Sheet writer", "role": "subagent", "parent": "fx-main", "depth": 1, "type": "general-purpose"},
        ],
    ))

for session, agents, calls, chat in ADDED:
    doc["sessions"].append(session)
    doc["agents"].extend(agents)
    doc["calls"].extend(calls)
    doc["chat"].extend(chat)

json.dump(doc, open(PATH, "w"), indent=2)
print("sessions now:", len(doc["sessions"]))
for s in doc["sessions"]:
    print(" ", s["id"], "| calls:", s["tiles"]["calls"], "| agents:", s["tiles"]["agents"])
