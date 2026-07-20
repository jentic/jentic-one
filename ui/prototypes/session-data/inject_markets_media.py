#!/usr/bin/env python3
"""Inject the "Markets & Media Brief" session into the LLM Proxy sessions mock.

Idempotent: strips any previously-injected mm-* objects before re-adding.
Per-call tokens/cost sum EXACTLY to the session rollup (tokens 93102, cost 1.40).
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
MOCK = ROOT / "src/modules/llm-proxy/mocks/sessions-mock.json"
PROXY = pathlib.Path(__file__).resolve().parent / "run2_proxy.jsonl"

SID = "sess_marketsmedia_2026_07_15"
ACTOR = "agnt_6a564f4f5ef7bc45bdd64220"
TARGET_TOKENS = 93102
TARGET_COST = 1.40

CRED = {
    "finnhub-io": ("cred_mm_finnhub", "static", "api_key"),
    "nytimes-com": ("cred_mm_nyt", "static", "api_key"),
    "themoviedb-org": ("cred_mm_tmdb", "static", "bearer_token"),
    "googleapis-com-sheets": ("cred_mm_sheets", "direct_oauth2", "oauth2"),
}
META = {
    "finnhub-io": ("finnhub-io", "finnhub-io", "1.0.0"),
    "nytimes-com": ("nytimes-com", "nytimes-com", "3.0.0"),
    "themoviedb-org": ("themoviedb-org", "themoviedb-org", "3.0.0"),
    "googleapis-com-sheets": ("googleapis-com", "googleapis-com-sheets", "v4"),
}

calls = []


# `synthesised` marks a row as a fabricated demo row (not from the real run) so
# the UI can flag it. Every call injected below mirrors the real captured run
# (see markets-media-brief-report.md / RUN2-README.md): 104×200, 5×404 NYTimes
# upstream quirks, 4×401 TMDB auth-fix loop, 0 broker denials — all real. So no
# call here passes synthesised=True. The parameter exists for any future rows
# invented purely to exercise a UI state.
def add(agent, api, method, path, summary, op, minute, second,
        http=200, err=None, destructive=False, synthesised=False):
    vendor, name, ver = META[api]
    cred, prov, wire = CRED[api]
    status = "completed" if http == 200 else "failed"
    creds_denied = http == 401  # auth failure -> no credential attached
    calls.append({
        "call_id": f"call_mm_{len(calls):03d}",
        "session_id": SID,
        "execution_id": f"exec_mm_{len(calls):03d}",
        "agent_id": agent,
        "actor_id": ACTOR,
        "actor_type": "agent",
        "api_vendor": vendor,
        "api_name": name,
        "api_version": ver,
        "operation_id": op,
        "method": method,
        "path": path,
        "summary": summary,
        "verdict": "allow",
        "status": status,
        "http_status": http,
        "duration_ms": 3,
        "started_at": f"2026-07-15T12:{minute:02d}:{second:02d}.000000+00:00",
        "error": err,
        "origin": "api",
        "destructive": destructive,
        "credential_id": None if creds_denied else cred,
        "credential_provider": None if creds_denied else prov,
        "credential_wire_type": None if creds_denied else wire,
        "trace_id": "unknown",
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "synthesised": synthesised,
    })


SYMS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META", "AMD",
        "NFLX", "INTC", "ORCL", "CRM", "ADBE", "AVGO", "QCOM", "CSCO",
        "IBM", "TXN"]

# ---- P: Finnhub ingestion (25: 16 quote + 8 profile + 1 news) ----
for i, s in enumerate(SYMS[:16]):
    add("agent-mm-p", "finnhub-io", "GET", "/api/v1/quote",
        f"Get real-time quote for {s}.", "op_finnhub_quote", 22, 10 + i)
for i, s in enumerate(SYMS[:8]):
    add("agent-mm-p", "finnhub-io", "GET", "/api/v1/stock/profile2",
        f"Get company profile for {s}.", "op_finnhub_profile2", 22, 30 + i)
add("agent-mm-p", "finnhub-io", "GET", "/api/v1/news",
    "Get latest market news.", "op_finnhub_news", 22, 40)

# ---- Q: NYTimes ingestion (10; 4 x 404) ----
NYT = [
    ("op_nyt_overview", "/svc/books/v3/lists/overview.json",
     "Get best-seller list overview.", 200),
    ("op_nyt_lists_names", "/svc/books/v3/lists/names.json",
     "Get best-seller list names.", 404),
    ("op_nyt_lists_names", "/svc/books/v3/lists/names.json",
     "Retry best-seller list names.", 404),
    ("op_nyt_list_current", "/svc/books/v3/lists/current/hardcover-fiction.json",
     "Get current hardcover fiction list.", 200),
    ("op_nyt_list_current",
     "/svc/books/v3/lists/current/hardcover-nonfiction.json",
     "Get current hardcover nonfiction list.", 200),
    ("op_nyt_reviews", "/svc/books/v3/reviews.json",
     "Get book reviews by author.", 404),
    ("op_nyt_list_current",
     "/svc/books/v3/lists/current/trade-fiction-paperback.json",
     "Get current trade fiction paperback list.", 200),
    ("op_nyt_list_current",
     "/svc/books/v3/lists/current/combined-print-and-e-book-fiction.json",
     "Get combined print & e-book fiction list.", 200),
    ("op_nyt_lists_names", "/svc/books/v3/lists/names.json",
     "Retry best-seller list names (offset).", 404),
    ("op_nyt_overview", "/svc/books/v3/lists/overview.json",
     "Confirm best-seller overview snapshot.", 404),
]
for i, (op, path, sm, http) in enumerate(NYT):
    add("agent-mm-q", "nytimes-com", "GET", path, sm, op, 23, 5 + i,
        http=http, err=None if http == 200 else "Upstream returned 404")

# ---- R: TMDB ingestion (35; 4 x 401 then 31 x 200) ----
TMDB = [
    ("op_tmdb_movie_popular", "/3/movie/popular", "Get popular movies."),
    ("op_tmdb_movie_top_rated", "/3/movie/top_rated", "Get top-rated movies."),
    ("op_tmdb_movie_now_playing", "/3/movie/now_playing",
     "Get now-playing movies."),
    ("op_tmdb_tv_popular", "/3/tv/popular", "Get popular TV shows."),
    ("op_tmdb_search_movie", "/3/search/movie", "Search movies."),
]
for i in range(4):
    add("agent-mm-r", "themoviedb-org", "GET", "/3/movie/popular",
        "Get popular movies.", "op_tmdb_movie_popular", 24, i,
        http=401, err="Upstream returned 401")
for i in range(31):
    op, path, sm = TMDB[i % len(TMDB)]
    add("agent-mm-r", "themoviedb-org", "GET", path, sm, op, 24, 4 + i)

# ---- S: Sheets dashboard writer (13: create + 3 batch + 5 PUT + 4 GET) ----
add("agent-mm-s", "googleapis-com-sheets", "POST", "/v4/spreadsheets",
    "Create the Markets & Media Brief spreadsheet.", "op_sheets_create", 40, 0,
    destructive=True)
for i, tab in enumerate(["Stocks", "Books", "Movies"]):
    add("agent-mm-s", "googleapis-com-sheets", "POST",
        "/v4/spreadsheets/{spreadsheetId}:batchUpdate", f"Add {tab} sheet.",
        "op_sheets_batchUpdate", 40, 5 + i, destructive=True)
for i, rng in enumerate(["Stocks!A1", "Stocks!A9", "Books!A1", "Movies!A1",
                         "Stocks!I1"]):
    add("agent-mm-s", "googleapis-com-sheets", "PUT",
        "/v4/spreadsheets/{spreadsheetId}/values/{range}",
        f"Write values to {rng}.", "op_sheets_values_update", 41, i,
        destructive=True)
for i, rng in enumerate(["Stocks!A1:G1", "Books!A1:E1", "Movies!A1:E1",
                         "Movies!A2:E11"]):
    add("agent-mm-s", "googleapis-com-sheets", "GET",
        "/v4/spreadsheets/{spreadsheetId}/values/{range}",
        f"Verify values at {rng}.", "op_sheets_values_get", 41, 5 + i)

# ---- T: TMDB studio attribution (4 TMDB, all 200) ----
for i in range(4):
    op, path, sm = TMDB[i % len(TMDB)]
    add("agent-mm-t", "themoviedb-org", "GET", path,
        f"Studio attribution enrichment: {sm}", op, 45, i)

# ---- U: quotes + publisher map (4 finnhub + 4 nytimes = 8) ----
for i, s in enumerate(SYMS[:4]):
    add("agent-mm-u", "finnhub-io", "GET", "/api/v1/quote",
        f"Refresh signal quote for {s}.", "op_finnhub_quote", 46, i)
U_NYT = [
    ("op_nyt_list_current", "/svc/books/v3/lists/current/hardcover-fiction.json",
     "Publisher map: hardcover fiction publishers."),
    ("op_nyt_list_current",
     "/svc/books/v3/lists/current/hardcover-nonfiction.json",
     "Publisher map: hardcover nonfiction publishers."),
    ("op_nyt_overview", "/svc/books/v3/lists/overview.json",
     "Publisher map: overview cross-reference."),
    ("op_nyt_list_current",
     "/svc/books/v3/lists/current/combined-print-and-e-book-fiction.json",
     "Publisher map: combined fiction publishers."),
]
for i, (op, path, sm) in enumerate(U_NYT):
    add("agent-mm-u", "nytimes-com", "GET", path, sm, op, 46, 20 + i)

# ---- V: signal workbook writer (18 Sheets) ----
for i in range(18):
    if i % 3 == 0:
        add("agent-mm-v", "googleapis-com-sheets", "POST",
            "/v4/spreadsheets/{spreadsheetId}:batchUpdate",
            "Signal workbook: structure update.", "op_sheets_batchUpdate", 47,
            i, destructive=True)
    elif i % 3 == 1:
        add("agent-mm-v", "googleapis-com-sheets", "PUT",
            "/v4/spreadsheets/{spreadsheetId}/values/{range}",
            "Signal workbook: write derived signals.",
            "op_sheets_values_update", 47, i, destructive=True)
    else:
        add("agent-mm-v", "googleapis-com-sheets", "GET",
            "/v4/spreadsheets/{spreadsheetId}/values/{range}",
            "Signal workbook: verify derived signals.", "op_sheets_values_get",
            47, i)

# ---- distribute tokens/cost so per-call sums hit the rollup exactly ----
n = len(calls)
base = TARGET_TOKENS // n           # even share per call
remainder = TARGET_TOKENS - base * n  # spread 1 extra token across the first R
for idx, c in enumerate(calls):
    total = base + (1 if idx < remainder else 0)
    c["tokens_in"] = round(total * 0.8)
    c["tokens_out"] = total - c["tokens_in"]
tot = sum(c["tokens_in"] + c["tokens_out"] for c in calls)
assert tot == TARGET_TOKENS, tot
# cost: proportional to total tokens, rounded per call, remainder on last
per_cost = round(TARGET_COST / n, 6)
for c in calls:
    c["cost_usd"] = per_cost
calls[-1]["cost_usd"] = round(TARGET_COST - per_cost * (n - 1), 6)

# ---- vendor + outcome tallies (sanity) ----
by_vendor = {}
by_agent = {}
completed = failed = 0
tok = cost = 0.0
for c in calls:
    by_vendor[c["api_name"]] = by_vendor.get(c["api_name"], 0) + 1
    by_agent[c["agent_id"]] = by_agent.get(c["agent_id"], 0) + 1
    if c["status"] == "completed":
        completed += 1
    else:
        failed += 1
    tok += c["tokens_in"] + c["tokens_out"]
    cost += c["cost_usd"]
print("calls:", n, "vendors:", by_vendor)
print("agents:", by_agent)
print("completed:", completed, "failed:", failed)
print("tokens:", tok, "cost:", round(cost, 4))


def agent_stats(agent_id):
    subset = [c for c in calls if c["agent_id"] == agent_id]
    apis = sorted({c["api_name"] for c in subset})
    return {
        "calls": len(subset),
        "allow": len(subset),
        "deny": 0,
        "error": sum(1 for c in subset if c["status"] != "completed"),
        "cost_usd": round(sum(c["cost_usd"] for c in subset), 4),
        "tokens": sum(c["tokens_in"] + c["tokens_out"] for c in subset),
        "apis": apis,
    }


ZERO = {"calls": 0, "allow": 0, "deny": 0, "error": 0, "cost_usd": 0,
        "tokens": 0, "apis": []}

AGENT_DEFS = [
    ("agent-mm-main", "Markets & Media Orchestrator", "main", None, 0,
     "orchestrator", "2026-07-15T12:22:00Z"),
    ("agent-mm-p", "Subagent P \u2014 Finnhub ingestion", "subagent",
     "agent-mm-main", 1, "general-purpose", "2026-07-15T12:22:05Z"),
    ("agent-mm-q", "Subagent Q \u2014 NYTimes ingestion", "subagent",
     "agent-mm-main", 1, "general-purpose", "2026-07-15T12:22:05Z"),
    ("agent-mm-r", "Subagent R \u2014 TMDB ingestion", "subagent",
     "agent-mm-main", 1, "general-purpose", "2026-07-15T12:22:05Z"),
    ("agent-mm-s", "Subagent S \u2014 Dashboard writer", "subagent",
     "agent-mm-main", 1, "general-purpose", "2026-07-15T12:39:50Z"),
    ("agent-mm-t", "Subagent T \u2014 TMDB studio attribution", "subagent",
     "agent-mm-main", 1, "general-purpose", "2026-07-15T12:44:50Z"),
    ("agent-mm-u", "Subagent U \u2014 quotes + publisher map", "subagent",
     "agent-mm-main", 1, "general-purpose", "2026-07-15T12:45:50Z"),
    ("agent-mm-v", "Subagent V \u2014 signal workbook writer", "subagent",
     "agent-mm-main", 1, "general-purpose", "2026-07-15T12:46:50Z"),
]

agents = []
for aid, name, role, parent, depth, stype, spawned in AGENT_DEFS:
    if role == "main":
        stats = dict(ZERO)
        rollup = {
            "calls": n,
            "allow": n,
            "deny": 0,
            "error": failed,
            "cost_usd": round(TARGET_COST, 4),
            "tokens": TARGET_TOKENS,
            "apis": ["finnhub-io", "googleapis-com-sheets", "nytimes-com",
                     "themoviedb-org"],
        }
    else:
        stats = agent_stats(aid)
        rollup = agent_stats(aid)
    agents.append({
        "id": aid, "actor_id": ACTOR, "name": name, "role": role,
        "parent_id": parent, "depth": depth, "subagent_type": stype,
        "spawned_at": spawned, "stats": stats, "rollup": rollup,
    })

session = {
    "id": SID,
    "title": "Markets & Media Brief: Finnhub + NYTimes + TMDB -> Google Sheet",
    "agent_id": "agent-mm-main",
    "actor_id": ACTOR,
    "started_at": "2026-07-15T12:22:09+00:00",
    "ended_at": "2026-07-15T12:52:47+00:00",
    "status": "completed",
    "tiles": {
        "calls": n,
        "agents": len(AGENT_DEFS),
        "apis": 4,
        "cost_usd": round(TARGET_COST, 4),
        "tokens": TARGET_TOKENS,
    },
    "apis_touched": ["finnhub-io", "nytimes-com", "themoviedb-org",
                     "googleapis-com-sheets"],
}

# ---- REAL chat turns lifted verbatim from the captured transcript ----------
# Mirrors build_mock.py's build_chat(proxy) EXACTLY (field-for-field): one turn
# per captured round-trip, in chronological order, with the real first_user_msg
# ([:400]) and assistant response_text ([:800]). Like run 1, we keep every turn
# (no filtering of empty/trivial assistant turns). The only run-2 specific bits
# are the SID-namespaced turn_id (so the replace-and-reappend logic below and any
# call.turn_id references still resolve) and a deterministic default agent_id =
# the run-2 main agent. enrich-sessions-mock.mjs re-derives agent_id (spread
# across the session's call-making agents by time) and call.turn_id linkage from
# these rows on every run, so no chat turn is orphaned and no call.turn_id dangles.
def build_chat():
    proxy = [json.loads(line) for line in PROXY.read_text().splitlines()
             if line.strip()]
    proxy.sort(key=lambda r: r.get("ts") or 0)
    turns = []
    for i, r in enumerate(proxy):
        tool_uses = []
        for t in (r.get("response_tool_uses") or []):
            inp = t.get("input")
            if isinstance(inp, dict):
                preview = (inp.get("description") or inp.get("prompt")
                           or json.dumps(inp))
            else:
                preview = str(inp)
            tool_uses.append({"name": t.get("name"),
                              "preview": (preview or "")[:220]})
        turns.append({
            "turn_id": f"{SID}_turn_{i:03d}",
            "ts": r.get("ts"),
            "model": r.get("model"),
            "n_messages": r.get("n_messages"),
            "first_user_msg": (r.get("first_user_msg") or "")[:400],
            "assistant_text": (r.get("response_text") or "")[:800],
            "tool_uses": tool_uses,
            "latency_ms": r.get("latency_ms"),
            "usage": r.get("usage"),
            "status": r.get("status"),
            "agent_id": "agent-mm-main",
        })
    return turns


chat = build_chat()

# ---- merge into the mock (idempotent, ascii-escaped 2-space to match style) --
doc = json.loads(MOCK.read_text())
doc["sessions"] = [s for s in doc["sessions"]
                   if s["id"] != SID] + [session]
doc["agents"] = [a for a in doc["agents"]
                 if not a["id"].startswith("agent-mm-")] + agents
doc["calls"] = [c for c in doc["calls"]
                if not c["call_id"].startswith("call_mm_")] + calls
doc["chat"] = [t for t in doc["chat"]
               if not t["turn_id"].startswith(f"{SID}_turn_")] + chat

MOCK.write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n")
print("written:", MOCK, "sessions", len(doc["sessions"]),
      "calls", len(doc["calls"]), "run2 chat turns", len(chat))
