/**
 * LLM Proxy · Local mock shaping.
 *
 * Pure helpers that turn the bundled `sessions-mock.json` into the exact
 * response shapes the (future) backend will return. Used by BOTH:
 *   - the MSW handlers (backendless dev via `VITE_ENABLE_MSW=1`), and
 *   - the repository tier's fallback, so `/app/llm-proxy` still renders demo
 *     data when neither MSW nor a real backend is available (e.g. plain
 *     `npm run dev` against a backend that has no `/proxy/*` route yet).
 *
 * When the real backend lands, delete this file + the JSON; the repository
 * tier already speaks the real paths.
 */
import mock from '@/modules/llm-proxy/mocks/sessions-mock.json';
import type { ProxySession, SessionBundle, SessionsMockDoc } from '@/modules/llm-proxy/api/types';

const doc = mock as unknown as SessionsMockDoc;

export interface SessionListResponse {
	data: ProxySession[];
	has_more: boolean;
	next_cursor: string | null;
	/** Aggregate HTTP-method histogram across all sessions' calls (cheap rollup). */
	methods: Record<string, number>;
}

function isFlightOps(sessionId: string): boolean {
	return sessionId.includes('flightops');
}

function chartFor(calls: SessionBundle['calls']): SessionBundle['charts'] {
	const buckets = new Map<string, { allow: number; deny: number; error: number }>();
	for (const c of calls) {
		const d = new Date(c.started_at);
		const key = Number.isNaN(d.getTime())
			? '??:??'
			: `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
		const b = buckets.get(key) ?? { allow: 0, deny: 0, error: 0 };
		if (c.status !== 'completed') b.error += 1;
		else if (c.verdict === 'deny') b.deny += 1;
		else b.allow += 1;
		buckets.set(key, b);
	}
	const calls_over_time = [...buckets.entries()]
		.sort(([a], [b]) => a.localeCompare(b))
		.map(([t, v]) => ({ t, ...v }));
	return { calls_over_time };
}

export function listSessionsLocal(): SessionListResponse {
	const methods: Record<string, number> = {};
	for (const c of doc.calls) {
		const m = (c.method || 'OTHER').toUpperCase();
		methods[m] = (methods[m] ?? 0) + 1;
	}
	return { data: doc.sessions, has_more: false, next_cursor: null, methods };
}

export function bundleForLocal(sessionId: string): SessionBundle | null {
	const session = doc.sessions.find((s) => s.id === sessionId);
	if (!session) return null;
	const calls = doc.calls.filter((c) => c.session_id === sessionId);
	// Agents don't carry a session_id; scope them to those referenced by this
	// session's calls, plus their ancestors (so the tree stays connected).
	const referenced = new Set(calls.map((c) => c.agent_id));
	const byId = new Map(doc.agents.map((a) => [a.id, a]));
	const keep = new Set<string>();
	for (const id of referenced) {
		let cursor: string | null = id;
		while (cursor && !keep.has(cursor)) {
			keep.add(cursor);
			cursor = byId.get(cursor)?.parent_id ?? null;
		}
	}
	if (keep.size === 0 && session.agent_id) keep.add(session.agent_id);
	const agents = doc.agents.filter((a) => keep.has(a.id));
	// The real flight-ops session carries the full proxy transcript (turn_NNN);
	// the two derived demo sessions carry their own `${sid}_turn_*` turns.
	const chat = doc.chat.filter(
		(t) =>
			t.turn_id.startsWith(sessionId) ||
			(isFlightOps(sessionId) && /^turn_\d+$/.test(t.turn_id)),
	);
	return {
		session,
		agents,
		calls,
		chat,
		denials: isFlightOps(sessionId) ? doc.denials : [],
		charts: chartFor(calls),
		final_output: doc.final_outputs?.[sessionId] ?? null,
	};
}
