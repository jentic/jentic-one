/**
 * One-shot deterministic enricher for `sessions-mock.json`.
 *
 * The mock is the single source of truth for the LLM Proxy · Sessions UI while
 * there is no backend. This script threads chat linkage (`turn_id` on calls,
 * `agent_id` on chat turns) and adds the request/response/timeline/governance
 * detail the drill-in drawers render. It is idempotent: derived fields are
 * recomputed from the existing rows every run. Run from `ui/`:
 *
 *   node scripts/enrich-sessions-mock.mjs
 *
 * Kept in-repo (not deleted) so the mock can be re-derived if base rows change.
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const MOCK_PATH = resolve(here, '../src/modules/llm-proxy/mocks/sessions-mock.json');
const PROXY_DIR = resolve(here, '../prototypes/session-data');

const doc = JSON.parse(readFileSync(MOCK_PATH, 'utf8'));

const REDACTED = '***';

/** ISO string or epoch-seconds → ms epoch (for temporal ordering). */
function tsToMs(ts) {
	if (ts == null) return NaN;
	if (typeof ts === 'number') return ts * 1000;
	const n = Number(ts);
	if (!Number.isNaN(n) && /^\d+(\.\d+)?$/.test(String(ts))) return n * 1000;
	return new Date(ts).getTime();
}

/** Which session a chat turn belongs to (mirrors mockData.ts scoping). */
function sessionOfTurn(turn) {
	if (/^turn_\d+$/.test(turn.turn_id)) return 'sess_flightops_2026_07_15';
	const m = turn.turn_id.match(/^(.*)_turn_\d+$/);
	return m ? m[1] : null;
}

// ---------------------------------------------------------------------------
// 1. Chat linkage — assign each turn an agent, and each call a turn.
//
// Turns in the mock are (mostly) the orchestrator's transcript, while calls are
// made by subagents. To give the per-agent drawer real turns we distribute a
// session's turns across the agents that actually made calls in that session,
// ordered by time, then link each call to the nearest preceding turn owned by
// the SAME agent (falling back to that agent's first/representative turn).
// ---------------------------------------------------------------------------

const callsBySession = new Map();
for (const c of doc.calls) {
	if (!callsBySession.has(c.session_id)) callsBySession.set(c.session_id, []);
	callsBySession.get(c.session_id).push(c);
}

const turnsBySession = new Map();
for (const t of doc.chat) {
	const sid = sessionOfTurn(t);
	if (!sid) continue;
	if (!turnsBySession.has(sid)) turnsBySession.set(sid, []);
	turnsBySession.get(sid).push(t);
}

// Reset then assign agent_id on turns.
for (const [sid, turns] of turnsBySession) {
	turns.sort((a, b) => tsToMs(a.ts) - tsToMs(b.ts));
	const sessionCalls = (callsBySession.get(sid) ?? [])
		.slice()
		.sort((a, b) => tsToMs(a.started_at) - tsToMs(b.started_at));
	// Ordered list of distinct call-making agents (first-appearance order).
	const callAgents = [];
	for (const c of sessionCalls) {
		if (!callAgents.includes(c.agent_id)) callAgents.push(c.agent_id);
	}
	// The session's own main agent (owns turns when no call-agents exist yet).
	const session = doc.sessions.find((s) => s.id === sid);
	const mainAgent = session?.agent_id ?? callAgents[0] ?? null;

	if (callAgents.length === 0) {
		for (const t of turns) t.agent_id = mainAgent;
		continue;
	}
	// Owners = the orchestrator (main agent) FIRST, then each call-making agent
	// in first-appearance order. The proxy transcript is fundamentally the
	// orchestrator's own thread — it plans and delegates before any subagent
	// runs, and stitches results at the end — so it must own a real slice of
	// turns rather than 0. Without this, an orchestrator that makes no tool calls
	// of its own (the common case) never appears in `callAgents` and its
	// Agent-detail drawer shows "Chat turns (0)" while its Chat button still
	// opens a real (session-first) turn — the two disagree. Prepending the main
	// agent gives it the opening slice and keeps the button and drawer in sync.
	const owners = [];
	if (mainAgent && !owners.includes(mainAgent)) owners.push(mainAgent);
	for (const a of callAgents) if (!owners.includes(a)) owners.push(a);
	// Spread the turns evenly across the owners, in time order, so each owner
	// (orchestrator first, then each working subagent) owns a contiguous slice.
	const per = turns.length / owners.length;
	turns.forEach((t, i) => {
		const idx = Math.min(owners.length - 1, Math.floor(i / per));
		t.agent_id = owners[idx];
	});
}

// Link each call to a turn: nearest preceding same-agent turn, else that
// agent's first turn, else the session's first turn (graceful fallback).
const noTurnResolved = [];
for (const [sid, sessionCalls] of callsBySession) {
	const turns = (turnsBySession.get(sid) ?? [])
		.slice()
		.sort((a, b) => tsToMs(a.ts) - tsToMs(b.ts));
	for (const c of sessionCalls) {
		const callMs = tsToMs(c.started_at);
		const sameAgent = turns.filter((t) => t.agent_id === c.agent_id);
		let chosen = null;
		for (const t of sameAgent) {
			if (tsToMs(t.ts) <= callMs) chosen = t;
		}
		if (!chosen && sameAgent.length > 0) chosen = sameAgent[0];
		if (!chosen && turns.length > 0) {
			// Fallback: nearest preceding turn regardless of agent, else first.
			for (const t of turns) if (tsToMs(t.ts) <= callMs) chosen = t;
			if (!chosen) chosen = turns[0];
			noTurnResolved.push(c.call_id);
		}
		c.turn_id = chosen ? chosen.turn_id : null;
		if (!chosen) noTurnResolved.push(c.call_id);
	}
}

// ---------------------------------------------------------------------------
// 2. Request / response / timeline / governance derivation per call.
// ---------------------------------------------------------------------------

function lastPathSegment(path) {
	const clean = path.split('?')[0];
	const parts = clean.split('/').filter(Boolean);
	return parts[parts.length - 1] ?? clean;
}

/** A small, realistic redacted request for a call (params for GET, body for writes). */
function deriveRequest(call) {
	const method = call.method.toUpperCase();
	const isWrite = method === 'POST' || method === 'PUT' || method === 'PATCH';
	const params = {};

	// Path params surfaced from `{placeholder}` segments.
	const placeholders = [...call.path.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
	for (const p of placeholders) {
		if (p === 'spreadsheetId') params[p] = '1x9REDACTEDsheetIdAbCdEf';
		else if (p === 'range') params[p] = 'Report!A1:H200';
		else params[p] = `${REDACTED}`;
	}

	// Vendor/path-flavoured query params.
	const vendor = call.api_vendor;
	if (vendor === 'airlabs-co') {
		params.api_key = REDACTED;
		if (call.path.includes('flights'))
			Object.assign(params, { bbox: '49,-11,61,2', _fields: 'flight_icao,lat,lng,alt,dir' });
		if (call.path.includes('airlines')) params.iata_code = 'BA';
	} else if (vendor === 'nytimes-com') {
		params['api-key'] = REDACTED;
		if (call.path.includes('articlesearch'))
			Object.assign(params, { q: 'markets', sort: 'newest', page: 0 });
	} else if (vendor === 'finnhub-io') {
		params.token = REDACTED;
		if (call.path.includes('quote')) params.symbol = 'AAPL';
		if (call.path.includes('profile2')) params.symbol = 'AAPL';
		if (call.path.includes('news')) Object.assign(params, { category: 'general' });
	} else if (vendor === 'themoviedb-org') {
		params.api_key = REDACTED;
		if (call.path.includes('search'))
			Object.assign(params, { query: 'dune', language: 'en-US', page: 1 });
		else Object.assign(params, { language: 'en-US', page: 1 });
	} else if (vendor === '1forge-com') {
		params.api_key = REDACTED;
		if (call.path.includes('quotes')) params.pairs = 'EURUSD,GBPUSD,USDJPY';
	} else if (vendor === 'open-meteo-com') {
		if (call.path.includes('search')) Object.assign(params, { name: 'Dublin', count: 1 });
		else
			Object.assign(params, {
				latitude: 53.3498,
				longitude: -6.2603,
				hourly: 'temperature_2m',
			});
	} else if (vendor === 'googleapis-com') {
		params.access_token = REDACTED;
	}

	const request = { params };

	if (isWrite) {
		if (call.path.endsWith(':batchUpdate')) {
			request.body = {
				requests: [
					{ updateSheetProperties: { properties: { title: 'Report' }, fields: 'title' } },
					{
						repeatCell: {
							range: { sheetId: 0 },
							cell: { userEnteredFormat: { textFormat: { bold: true } } },
							fields: 'userEnteredFormat.textFormat.bold',
						},
					},
				],
			};
		} else if (call.path.endsWith(':append') || call.path.includes('/values/')) {
			request.body = {
				valueInputOption: 'USER_ENTERED',
				values: [
					['Flight', 'Airline', 'Origin', 'Destination', 'Status'],
					['BA117', 'British Airways', 'LHR', 'JFK', 'En route'],
					['EI105', 'Aer Lingus', 'DUB', 'BOS', 'Boarding'],
				],
			};
		} else if (call.path.endsWith(':clear')) {
			request.body = { ranges: ['Report!A2:H1000'] };
		} else if (call.path === '/v4/spreadsheets') {
			request.body = {
				properties: { title: 'Flight-Ops Report 2026-07-15' },
				sheets: [{ properties: { title: 'Report' } }],
			};
		} else {
			request.body = { note: 'write payload redacted for brevity', token: REDACTED };
		}
	}

	return request;
}

/** A short, realistic redacted response snippet keyed on outcome + endpoint. */
function deriveResponseSnippet(call) {
	const tone =
		call.verdict === 'deny' || call.status === 'denied'
			? 'deny'
			: call.status !== 'completed'
				? 'error'
				: 'allow';

	if (tone === 'deny') {
		return JSON.stringify(
			{
				error: 'access_denied',
				reason: call.error ?? 'policy denied the request',
				request_id: 'req_' + call.call_id.slice(-8),
			},
			null,
			2,
		);
	}
	if (tone === 'error') {
		return JSON.stringify(
			{
				error: call.http_status === 429 ? 'rate_limited' : 'upstream_error',
				status: call.http_status,
				message: call.error ?? 'upstream returned an error',
			},
			null,
			2,
		);
	}

	const seg = lastPathSegment(call.path);
	const vendor = call.api_vendor;
	if (call.path === '/ping') return JSON.stringify({ ok: true, message: 'pong' }, null, 2);
	if (vendor === 'airlabs-co' && call.path.includes('flights')) {
		return JSON.stringify(
			{
				response: [{ flight_icao: 'BAW117', lat: 51.5, lng: -0.4, alt: 11277, dir: 289 }],
				terms: '…',
			},
			null,
			2,
		);
	}
	if (vendor === 'googleapis-com' && call.method === 'POST' && call.path === '/v4/spreadsheets') {
		return JSON.stringify(
			{
				spreadsheetId: '1x9REDACTEDsheetIdAbCdEf',
				spreadsheetUrl: 'https://docs.google.com/…',
				properties: { title: 'Flight-Ops Report 2026-07-15' },
			},
			null,
			2,
		);
	}
	if (vendor === 'googleapis-com' && (call.path.includes(':append') || call.method === 'PUT')) {
		return JSON.stringify(
			{ updatedRange: 'Report!A1:H4', updatedRows: 4, updatedColumns: 8, updatedCells: 32 },
			null,
			2,
		);
	}
	if (vendor === 'finnhub-io' && call.path.includes('quote')) {
		return JSON.stringify(
			{ c: 214.29, d: 1.87, dp: 0.88, h: 215.1, l: 211.5, o: 212.4, pc: 212.42 },
			null,
			2,
		);
	}
	if (vendor === 'nytimes-com') {
		return JSON.stringify(
			{
				status: 'OK',
				num_results: 12,
				results: [{ title: 'Markets steady as…', section: 'business' }],
			},
			null,
			2,
		);
	}
	if (vendor === 'themoviedb-org') {
		return JSON.stringify(
			{
				page: 1,
				total_results: 500,
				results: [{ id: 693134, title: 'Dune: Part Two', vote_average: 8.2 }],
			},
			null,
			2,
		);
	}
	if (vendor === 'open-meteo-com') {
		return JSON.stringify(
			{ latitude: 53.35, longitude: -6.26, hourly: { temperature_2m: [12.4, 12.1, 11.8] } },
			null,
			2,
		);
	}
	if (vendor === '1forge-com') {
		return JSON.stringify(
			[{ symbol: 'EURUSD', price: 1.0842, timestamp: 1784115000 }],
			null,
			2,
		);
	}
	return JSON.stringify(
		{ ok: true, resource: seg, http_status: call.http_status ?? 200 },
		null,
		2,
	);
}

/** Stage timings that roughly sum to duration_ms; credential/upstream null on deny. */
function deriveTimeline(call) {
	const denied = call.verdict === 'deny' || call.status === 'denied';
	const total = typeof call.duration_ms === 'number' ? call.duration_ms : 0;
	if (denied) {
		// Denied at the policy gate: queued + policy only, no downstream.
		const queued = Math.max(1, Math.round(total * 0.4));
		const policy = Math.max(1, total - queued);
		return { queued_ms: queued, policy_ms: policy, credential_ms: null, upstream_ms: null };
	}
	// Allow / error: split queued (small) + policy (small) + credential (small) +
	// upstream (the bulk). Guard tiny durations so every stage is >= 0.
	const queued = Math.max(0, Math.round(total * 0.05));
	const policy = Math.max(0, Math.round(total * 0.08));
	const credential = call.credential_id ? Math.max(0, Math.round(total * 0.07)) : 0;
	const upstream = Math.max(0, total - queued - policy - credential);
	return {
		queued_ms: queued,
		policy_ms: policy,
		credential_ms: credential,
		upstream_ms: upstream,
	};
}

/** Scopes an operation touches, derived from vendor + method (read vs write). */
function scopesFor(call) {
	const vendor = call.api_vendor;
	const write = call.method !== 'GET';
	if (vendor === 'googleapis-com') {
		return write
			? ['sheets:spreadsheets.write', 'sheets:spreadsheets.read']
			: ['sheets:spreadsheets.read'];
	}
	const base = vendor.split(/[.-]/)[0] || vendor;
	return write ? [`${base}:write`, `${base}:read`] : [`${base}:read`];
}

/** Governance rule + scopes + grant hint for a call. */
function deriveGovernance(call) {
	const denied = call.verdict === 'deny' || call.status === 'denied';
	const required = scopesFor(call);
	if (denied) {
		const missing = required[0];
		return {
			rule: {
				id: 'rule_default_deny',
				name: 'Default deny (no matching allow)',
				matched: true,
			},
			scopes_required: required,
			scopes_granted: required.filter((s) => s.endsWith(':read') || s.includes('.read')),
			grant_hint: `Grant "${missing}" to this actor (or add an allow rule for ${call.method} ${call.path}) to permit this call.`,
		};
	}
	const write = call.method !== 'GET';
	return {
		rule: {
			id: write ? 'rule_writes_reviewed' : 'rule_reads_allowed',
			name: write
				? `Allow reviewed writes to ${call.api_name}`
				: `Allow reads from ${call.api_name}`,
			matched: true,
		},
		scopes_required: required,
		scopes_granted: required,
		grant_hint: null,
	};
}

for (const call of doc.calls) {
	call.request = deriveRequest(call);
	call.response_snippet = deriveResponseSnippet(call);
	call.timeline = deriveTimeline(call);
	const gov = deriveGovernance(call);
	call.rule = gov.rule;
	call.scopes_required = gov.scopes_required;
	call.scopes_granted = gov.scopes_granted;
	call.grant_hint = gov.grant_hint;
}

// ---------------------------------------------------------------------------
// 3. Final output — the run's closing message per session.
//
// Both the far-right Result node and the pinned Outcome bar read this from the
// session bundle. The two REAL captured runs (flight-ops, markets-media) get
// their ACTUAL last substantive assistant turn, verbatim, lifted straight from
// the captured proxy transcript — nothing synthesised or reworded. The
// fabricated demo sessions (newsroom, fx, weather) are synthetic anyway, so
// they get a deterministic synthesis from their own call activity.
// ---------------------------------------------------------------------------

/**
 * Real sessions map to their captured proxy transcript (JSONL, one round-trip
 * per line). Each line has a `response_text` (the model's turn) and a `ts`.
 */
const REAL_RUN_TRANSCRIPTS = {
	sess_flightops_2026_07_15: 'this_run_proxy.jsonl',
	sess_marketsmedia_2026_07_15: 'run2_proxy.jsonl',
};

// Minimum length (trimmed chars) for an assistant turn to count as the run's
// "closing message" rather than a trivial follow-up (e.g. "open the
// spreadsheet") or a terse self-summary. The real closing reports are ~1.7k–2.3k
// chars; the trailing noise turns are <300, so this cleanly separates them.
const SUBSTANTIVE_TURN_MIN_CHARS = 400;

/**
 * Extract the ACTUAL last substantive assistant turn from a captured proxy
 * transcript, verbatim. Walks the lines in chronological order and returns the
 * last `response_text` whose trimmed length clears the substantive threshold,
 * skipping trailing trivial/empty turns. Returns null if the file is missing or
 * has no substantive turn (caller falls back to synthesis).
 */
function extractVerbatimFinalOutput(fileName) {
	const path = resolve(PROXY_DIR, fileName);
	if (!existsSync(path)) return null;
	const rows = readFileSync(path, 'utf8')
		.split('\n')
		.filter((l) => l.trim())
		.map((l) => JSON.parse(l))
		.sort((a, b) => tsToMs(a.ts) - tsToMs(b.ts));
	let chosen = null;
	for (const row of rows) {
		const text = typeof row.response_text === 'string' ? row.response_text : '';
		if (text.trim().length >= SUBSTANTIVE_TURN_MIN_CHARS) chosen = text;
	}
	return chosen ? { summary: chosen } : null;
}

/** Short, honest synthesis for the fabricated demo sessions, from activity. */
function synthesiseFinalOutput(session, sessionCalls, lastTurn) {
	const total = sessionCalls.length;
	if (total === 0) {
		return lastTurn?.assistant_text ? { summary: lastTurn.assistant_text } : null;
	}
	const denied = sessionCalls.filter((c) => c.verdict === 'deny' || c.status === 'denied').length;
	const errored = sessionCalls.filter(
		(c) => c.status !== 'completed' && !(c.verdict === 'deny' || c.status === 'denied'),
	).length;
	const vendors = [...new Set(sessionCalls.map((c) => c.api_name || c.api_vendor))];
	const lines = [
		`${session.title}.`,
		'',
		'Summary',
		`- Tool calls: ${total} across ${vendors.length} API${vendors.length === 1 ? '' : 's'} (${vendors.join(', ')})`,
		`- Governance: ${denied} denied, ${errored} errored, ${total - denied - errored} allowed`,
	];
	if (lastTurn?.assistant_text) {
		lines.push('', 'Notes', `- ${lastTurn.assistant_text}`);
	}
	return { summary: lines.join('\n') };
}

const finalOutputs = {};
for (const session of doc.sessions) {
	const sid = session.id;
	const sessionCalls = callsBySession.get(sid) ?? [];
	const turns = (turnsBySession.get(sid) ?? [])
		.slice()
		.sort((a, b) => tsToMs(a.ts) - tsToMs(b.ts));
	const lastTurn = turns[turns.length - 1] ?? null;
	// Real captured runs: verbatim last substantive assistant turn from the
	// transcript. Demo sessions (and any real run whose transcript is missing):
	// deterministic synthesis so no session is ever left without a final output.
	const verbatim = REAL_RUN_TRANSCRIPTS[sid]
		? extractVerbatimFinalOutput(REAL_RUN_TRANSCRIPTS[sid])
		: null;
	finalOutputs[sid] = verbatim ?? synthesiseFinalOutput(session, sessionCalls, lastTurn);
}
doc.final_outputs = finalOutputs;

writeFileSync(MOCK_PATH, JSON.stringify(doc, null, 2) + '\n', 'utf8');

const unresolved = [...new Set(noTurnResolved)];
console.log(`Enriched ${doc.calls.length} calls, ${doc.chat.length} chat turns.`);
console.log(
	`Final outputs: ${Object.values(finalOutputs).filter(Boolean).length}/${doc.sessions.length} sessions have a closing synthesis.`,
);
console.log(
	`Turn linkage: ${doc.calls.filter((c) => c.turn_id).length}/${doc.calls.length} calls resolved to a turn.`,
);
if (unresolved.length)
	console.log(`Fell back to cross-agent/first turn for ${unresolved.length} call(s).`);
