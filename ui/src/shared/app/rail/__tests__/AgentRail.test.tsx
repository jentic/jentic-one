import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { page, cdp } from '@vitest/browser/context';
import { act, type ReactElement } from 'react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent, userEvent, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { ToastHost } from '@/shared/app/rail/ToastHost';
import {
	AgentStreamProvider,
	adaptEvent,
	buildGroupKeyForTest,
	buildTraceBundle,
	formatFailurePillCount,
	formatStreamDayLabel,
	inlineActionsFor,
	isFailureSeverity,
	kindForType,
	matchesToastScope,
	primaryDestinationFor,
	severityForWire,
	streamDayKey,
	unacknowledgedFailureCount,
	useAgentStream,
	RAIL_COLLAPSED_STORAGE_KEY,
	TOAST_SCOPE_STORAGE_KEY,
	type StreamEvent,
} from '@/shared/lib/agentStream';
import type { EventResponse } from '@/shared/api';
import { decideCalls } from '@/shared/app/rail/mocks/handlers';
import { listAccessRequests, getAccessRequest } from '@/shared/lib/accessRequests';

/** A location probe so navigation from the rail can be asserted. */
function LocationProbe() {
	const loc = useLocation();
	return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

/** Render the rail with a backlog-only (live={false}) real-event provider. */
function renderRail(ui: ReactElement, route = '/dashboard') {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={[route]}>
				<AgentStreamProvider live={false}>
					<Routes>
						<Route path="/*" element={ui} />
					</Routes>
					<LocationProbe />
				</AgentStreamProvider>
			</MemoryRouter>
		</QueryClientProvider>,
	);
}

function wireEvent(
	over: Partial<EventResponse> & Pick<EventResponse, 'event_id' | 'type'>,
): EventResponse {
	return {
		_links: { self: `/events/${over.event_id}` },
		acknowledged: false,
		created_at: new Date().toISOString(),
		requires_action: false,
		severity: 'info' as EventResponse['severity'],
		summary: 'wire event',
		...over,
	};
}

function makeEvent(partial: Partial<StreamEvent>): StreamEvent {
	const base: StreamEvent = {
		id: 'ev_test',
		tsMs: Date.now(),
		type: 'execution.completed',
		kind: 'execution',
		severity: 'info',
		title: 'test event',
		tokens: {},
		links: {},
		requiresAction: false,
		acknowledged: false,
		groupKey: 'execution:execution.completed:',
	};
	return { ...base, ...partial };
}

beforeEach(async () => {
	window.localStorage.clear();
	window.sessionStorage.clear();
	// The rail is `hidden xl:flex` (xl = 1280px). Widen the page so the rail
	// and its controls join the accessibility tree; role queries skip
	// `display:none` content.
	await page.viewport(1440, 900);
});
afterEach(() => {
	window.localStorage.clear();
	window.sessionStorage.clear();
});

describe('agentStream — wire adaptation + pure helpers', () => {
	it('kindForType derives the namespace and buckets the unknown', () => {
		expect(kindForType('execution.failed')).toBe('execution');
		expect(kindForType('import.completed')).toBe('import');
		expect(kindForType('access_request.filed')).toBe('access_request');
		expect(kindForType('credential.expired')).toBe('credential');
		expect(kindForType('agent.self_registered')).toBe('agent');
		expect(kindForType('webhook.delivered')).toBe('other');
	});

	it('severityForWire normalises the enum + bare strings', () => {
		expect(severityForWire('critical')).toBe('critical');
		expect(severityForWire('error')).toBe('error');
		expect(severityForWire('warning')).toBe('warning');
		expect(severityForWire('info')).toBe('info');
	});

	it('adaptEvent lifts tokens, links and flags off the wire shape', () => {
		const ev = adaptEvent(
			wireEvent({
				event_id: 'evt_1',
				type: 'execution.failed',
				severity: 'critical' as EventResponse['severity'],
				summary: 'Execution failed',
				detail: 'boom',
				requires_action: true,
				trace_id: 'tr_9',
				data: { execution_id: 'exec_9', toolkit_id: 'slack' },
				_links: { self: '/events/evt_1', execution: '/executions/exec_9' },
			}),
		);
		expect(ev.id).toBe('evt_1');
		expect(ev.kind).toBe('execution');
		expect(ev.severity).toBe('critical');
		expect(ev.meta).toBe('boom');
		expect(ev.requiresAction).toBe(true);
		expect(ev.tokens.execution_id).toBe('exec_9');
		expect(ev.tokens.trace_id).toBe('tr_9');
		expect(ev.links.execution).toBe('/executions/exec_9');
	});

	it('adaptEvent lifts execution_id/job_id from _links when absent from data (#617)', () => {
		// The regressed real-world case: the backend surfaces the linked execution
		// ONLY as `_links.execution` (= /executions/{id}); `data` is empty. The
		// deep-link token must still resolve so "View execution" appears.
		const exec = adaptEvent(
			wireEvent({
				event_id: 'evt_link_exec',
				type: 'execution.failed',
				requires_action: true,
				trace_id: null,
				data: {},
				_links: { self: '/events/evt_link_exec', execution: '/executions/exec_link' },
			}),
		);
		expect(exec.tokens.execution_id).toBe('exec_link');

		const job = adaptEvent(
			wireEvent({
				event_id: 'evt_link_job',
				type: 'import.completed',
				data: {},
				_links: { self: '/events/evt_link_job', job: '/jobs/job_link' },
			}),
		);
		expect(job.tokens.job_id).toBe('job_link');
	});

	it('adaptEvent falls back to now (not 1970) for a missing/unparseable timestamp', () => {
		const before = Date.now();
		const ev = adaptEvent(
			wireEvent({
				event_id: 'evt_bad_ts',
				type: 'execution.completed',
				created_at: 'not-a-date',
			}),
		);
		expect(ev.tsMs).toBeGreaterThanOrEqual(before);
		expect(Number.isNaN(ev.tsMs)).toBe(false);
	});

	it('matchesToastScope honours each scope', () => {
		expect(matchesToastScope('critical', 'off')).toBe(false);
		expect(matchesToastScope('info', 'off')).toBe(false);
		expect(matchesToastScope('info', 'all')).toBe(true);
		expect(matchesToastScope('warning', 'warning')).toBe(true);
		expect(matchesToastScope('error', 'warning')).toBe(true);
		expect(matchesToastScope('info', 'warning')).toBe(false);
		expect(matchesToastScope('critical', 'critical')).toBe(true);
		expect(matchesToastScope('warning', 'critical')).toBe(false);
	});

	it('isFailureSeverity flags only error + critical (#671)', () => {
		expect(isFailureSeverity('critical')).toBe(true);
		expect(isFailureSeverity('error')).toBe(true);
		expect(isFailureSeverity('warning')).toBe(false);
		expect(isFailureSeverity('info')).toBe(false);
	});

	it('unacknowledgedFailureCount counts only unacked error/critical (#671)', () => {
		const events = [
			makeEvent({ id: 'e1', severity: 'error' }),
			makeEvent({ id: 'c1', severity: 'critical' }),
			makeEvent({ id: 'e2', severity: 'error', acknowledged: true }),
			makeEvent({ id: 'w1', severity: 'warning' }),
			makeEvent({ id: 'i1', severity: 'info' }),
		];
		expect(unacknowledgedFailureCount(events)).toBe(2);
		expect(unacknowledgedFailureCount([])).toBe(0);
		// Acknowledging every failure drops the count to zero.
		expect(unacknowledgedFailureCount(events.map((e) => ({ ...e, acknowledged: true })))).toBe(
			0,
		);
	});

	it('formatFailurePillCount caps at 99+ and clamps pathological inputs', () => {
		expect(formatFailurePillCount(0)).toBe('0');
		expect(formatFailurePillCount(1)).toBe('1');
		expect(formatFailurePillCount(99)).toBe('99');
		expect(formatFailurePillCount(100)).toBe('99+');
		// Pathological inputs must not leak "NaN" / "-1" onto the pill.
		expect(formatFailurePillCount(NaN)).toBe('0');
		expect(formatFailurePillCount(-1)).toBe('0');
		expect(formatFailurePillCount(-5)).toBe('0');
		expect(formatFailurePillCount(3.9)).toBe('3');
		expect(formatFailurePillCount(Infinity)).toBe('0');
	});

	it('failures toast regardless of scope; non-failures still honour scope (#671)', () => {
		// The ToastHost gate is `isFailureSeverity(sev) || matchesToastScope(sev, scope)`.
		// A failed unattended run must surface even under the quietest scope.
		const wouldToast = (sev: StreamEvent['severity'], scope: 'off' | 'critical' | 'all') =>
			isFailureSeverity(sev) || matchesToastScope(sev, scope);
		expect(wouldToast('error', 'off')).toBe(true);
		expect(wouldToast('critical', 'off')).toBe(true);
		// Non-failures obey scope as before.
		expect(wouldToast('info', 'off')).toBe(false);
		expect(wouldToast('warning', 'off')).toBe(false);
		expect(wouldToast('info', 'all')).toBe(true);
	});

	describe('timestamp date helpers (#705)', () => {
		it('streamDayKey buckets by local calendar day, not UTC', () => {
			const a = new Date(2026, 6, 16, 9, 0, 0).getTime(); // 16 Jul, local
			const b = new Date(2026, 6, 16, 23, 30, 0).getTime(); // same local day
			const c = new Date(2026, 6, 17, 0, 30, 0).getTime(); // next local day
			expect(streamDayKey(a)).toBe('2026-07-16');
			expect(streamDayKey(a)).toBe(streamDayKey(b));
			expect(streamDayKey(a)).not.toBe(streamDayKey(c));
			expect(streamDayKey(NaN)).toBe('');
		});

		it('formatStreamDayLabel resolves Today / Yesterday / dated', () => {
			const now = new Date(2026, 6, 17, 12, 0, 0).getTime();
			const today = new Date(2026, 6, 17, 8, 0, 0).getTime();
			const yesterday = new Date(2026, 6, 16, 8, 0, 0).getTime();
			const older = new Date(2026, 6, 13, 8, 0, 0).getTime();
			expect(formatStreamDayLabel(today, now)).toBe('Today');
			expect(formatStreamDayLabel(yesterday, now)).toBe('Yesterday');
			// Older days fall through to a compact weekday+date label. The label is
			// locale-formatted (TZ + locale are pinned in vitest.config.ts, #7), so
			// assert against the same formatter rather than a brittle literal.
			const olderLabel = formatStreamDayLabel(older, now);
			expect(olderLabel).not.toBe('Today');
			expect(olderLabel).not.toBe('Yesterday');
			expect(olderLabel).toBe(
				new Date(older).toLocaleDateString(undefined, {
					weekday: 'short',
					day: 'numeric',
					month: 'short',
				}),
			);
		});

		it('formatStreamDayLabel computes Yesterday by calendar rewind across a real DST boundary', async () => {
			// The suite is globally pinned to UTC (DST-free) for determinism, so a
			// fixed-24h rewind and a calendar-day rewind coincide and a UTC-only
			// test can't tell the fix from the bug. Override JUST this test's
			// timezone to a DST-observing zone via CDP so the in-page `Date`
			// genuinely straddles America/New_York spring-forward
			// (2026-03-08 02:00 EST → 03:00 EDT — a 23-hour local day), then
			// restore UTC so the rest of the suite stays deterministic.
			// The public `cdp()` type is intentionally minimal; the playwright
			// provider backs it with a real Chrome DevTools session that exposes
			// `send(method, params)`. Narrow to just that here.
			const session = cdp() as unknown as {
				send: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
			};
			await session.send('Emulation.setTimezoneOverride', {
				timezoneId: 'America/New_York',
			});
			try {
				// `now` = 2026-03-09 00:30 EDT (= 04:30 UTC). Because 2026-03-08 was
				// only 23h long, subtracting a fixed 24h of real ms overshoots to
				// 2026-03-07 23:30 EST — the WRONG local day. A calendar-day rewind
				// correctly lands on 2026-03-08.
				const now = Date.UTC(2026, 2, 9, 4, 30, 0);
				// Event on the previous LOCAL calendar day (2026-03-08 12:00 EDT =
				// 16:00 UTC). The fix returns 'Yesterday'; the fixed-24h form keys
				// 2026-03-07 and would fall through to a dated label instead.
				const prevDay = Date.UTC(2026, 2, 8, 16, 0, 0);
				// Two local days before `now` (2026-03-07) — never 'Yesterday'.
				const twoDaysAgo = Date.UTC(2026, 2, 7, 16, 0, 0);

				expect(formatStreamDayLabel(prevDay, now)).toBe('Yesterday');
				expect(formatStreamDayLabel(twoDaysAgo, now)).not.toBe('Yesterday');
				expect(formatStreamDayLabel(twoDaysAgo, now)).not.toBe('Today');
			} finally {
				// Restore the pinned UTC zone for every following test.
				await session.send('Emulation.setTimezoneOverride', { timezoneId: 'UTC' });
			}
		});
	});

	it('inlineActionsFor offers View + Deny for a filed access request, gated on action + ack', () => {
		const ev = makeEvent({
			type: 'access_request.filed',
			kind: 'access_request',
			// Real filed events are INFO severity; the action logic must not depend
			// on severity (see issue #652).
			severity: 'info',
			requiresAction: true,
			tokens: { access_request_id: 'ar_1' },
		});
		const kinds = inlineActionsFor(ev).map((a) => a.kind);
		expect(kinds).toContain('view_request');
		expect(kinds).toContain('deny');
		// Approve is not offered at the row — it lives inside the View dialog.
		expect(kinds).not.toContain('approve');
		// View opens the request dialog (no RPC); Deny is reason-gated.
		expect(inlineActionsFor(ev).find((a) => a.kind === 'view_request')?.opensRequest).toBe(
			true,
		);
		expect(inlineActionsFor(ev).find((a) => a.kind === 'deny')?.requiresReason).toBe(true);
		// Acknowledged → no decision actions.
		expect(inlineActionsFor({ ...ev, acknowledged: true }).map((a) => a.kind)).not.toContain(
			'view_request',
		);
	});

	it('inlineActionsFor falls back to Acknowledge for action-required non-decision events', () => {
		const ev = makeEvent({
			type: 'execution.failed',
			kind: 'execution',
			severity: 'critical',
			requiresAction: true,
			tokens: { execution_id: 'exec_1' },
		});
		expect(inlineActionsFor(ev).map((a) => a.kind)).toContain('acknowledge');
	});

	it('inlineActionsFor offers Acknowledge (not decide) for a filed event lacking a request id', () => {
		const ev = makeEvent({
			type: 'access_request.filed',
			kind: 'access_request',
			severity: 'warning',
			requiresAction: true,
			tokens: {},
		});
		const kinds = inlineActionsFor(ev).map((a) => a.kind);
		expect(kinds).toContain('acknowledge');
		expect(kinds).not.toContain('approve');
	});

	it('adaptEvent resolves agent_id from the top-level actor for agent.* events', () => {
		// A DCR self-registration event carries the agent as the ACTOR, not in
		// `data` — the token must still land so Review can deep-link.
		const ev = adaptEvent(
			wireEvent({
				event_id: 'evt_agent',
				type: 'agent.self_registered',
				requires_action: true,
				actor_id: 'agt_42',
				actor_type: 'agent',
			}),
		);
		expect(ev.kind).toBe('agent');
		expect(ev.tokens.agent_id).toBe('agt_42');
		// Explicit data wins over the actor fallback when both are present.
		const explicit = adaptEvent(
			wireEvent({
				event_id: 'evt_agent2',
				type: 'agent.registration_approved',
				actor_id: 'usr_1',
				actor_type: 'user',
				data: { agent_id: 'agt_43' },
			}),
		);
		expect(explicit.tokens.agent_id).toBe('agt_43');
		// But an unguarded `data.actor_id` must NOT outrank the guarded actor:
		// some emitters put the deciding USER's id in data.actor_id, and routing
		// Review to /agents/<user_id> would 404.
		const mixed = adaptEvent(
			wireEvent({
				event_id: 'evt_agent3',
				type: 'agent.self_registered',
				actor_id: 'agt_44',
				actor_type: 'agent',
				data: { actor_id: 'usr_9' },
			}),
		);
		expect(mixed.tokens.agent_id).toBe('agt_44');
		// And with NO guarded source at all, `data.actor_id` is ignored entirely
		// (no emitter populates it today; one that did could carry a user id).
		const unguarded = adaptEvent(
			wireEvent({
				event_id: 'evt_agent4',
				type: 'access_request.approved',
				actor_id: 'usr_1',
				actor_type: 'user',
				data: { actor_id: 'usr_9' },
			}),
		);
		expect(unguarded.tokens.agent_id).toBeUndefined();
	});

	it('inlineActionsFor offers Review + Acknowledge for a self-registered agent', () => {
		const ev = makeEvent({
			type: 'agent.self_registered',
			kind: 'agent',
			requiresAction: true,
			tokens: { agent_id: 'agt_42' },
		});
		const actions = inlineActionsFor(ev);
		const kinds = actions.map((a) => a.kind);
		expect(kinds).toContain('view_agent');
		expect(kinds).toContain('acknowledge');
		// Review deep-links to the agent's approval page.
		const review = actions.find((a) => a.kind === 'view_agent');
		expect(review?.label).toBe('Review');
		expect(review?.href?.(ev)).toBe('/agents/agt_42');
		// Once acknowledged the row keeps only the passive deep-link.
		const acked = inlineActionsFor({ ...ev, acknowledged: true });
		expect(acked.map((a) => a.kind)).toEqual(['view_agent']);
		expect(acked[0]?.label).toBe('View agent');
	});

	it('primaryDestinationFor routes agent events to the agent page', () => {
		const ev = makeEvent({
			type: 'agent.self_registered',
			kind: 'agent',
			tokens: { agent_id: 'agt_42' },
		});
		expect(primaryDestinationFor(ev)).toBe('/agents/agt_42');
	});

	describe('buildTraceBundle', () => {
		const now = 1_700_000_000_000;
		const windowMs = 5 * 60 * 1000;

		it('exports only events inside the trailing window, newest-first', () => {
			const recentA = makeEvent({ id: 'a', tsMs: now - 60_000 });
			const recentB = makeEvent({ id: 'b', tsMs: now - 10_000 });
			const old = makeEvent({ id: 'old', tsMs: now - 10 * 60 * 1000 });
			const bundle = buildTraceBundle([recentA, old, recentB], windowMs, now);
			expect(bundle.windowMs).toBe(windowMs);
			expect(bundle.eventCount).toBe(2);
			expect(bundle.events.map((e) => e.id)).toEqual(['b', 'a']);
			expect(bundle.exportedAt).toBe(new Date(now).toISOString());
		});

		it('falls back to ALL loaded events (windowMs: null) when the window is empty', () => {
			// The sparse-feed case the user hit: events exist, but all are older
			// than the window — the old code produced an empty file.
			const old1 = makeEvent({ id: 'o1', tsMs: now - 30 * 60 * 1000 });
			const old2 = makeEvent({ id: 'o2', tsMs: now - 20 * 60 * 1000 });
			const bundle = buildTraceBundle([old1, old2], windowMs, now);
			expect(bundle.windowMs).toBeNull();
			expect(bundle.eventCount).toBe(2);
			expect(bundle.events.map((e) => e.id)).toEqual(['o2', 'o1']);
		});

		it('reports an empty bundle only when there are no events at all', () => {
			const bundle = buildTraceBundle([], windowMs, now);
			expect(bundle.eventCount).toBe(0);
			expect(bundle.events).toEqual([]);
		});
	});

	it('primaryDestinationFor routes execution events to the monitor executions tab', () => {
		const ev = makeEvent({
			type: 'execution.failed',
			kind: 'execution',
			severity: 'critical',
			tokens: { execution_id: 'exec x', trace_id: 'tr_1' },
		});
		// The detail param is the underscore vocabulary the Executions tab reads —
		// `execution`/`trace` aliases switched the tab but left the sheet closed (#617).
		expect(primaryDestinationFor(ev)).toBe('/monitor?tab=executions&execution_id=exec%20x');
	});

	it('primaryDestinationFor falls back to trace_id when an execution has no execution_id', () => {
		const ev = makeEvent({
			type: 'execution.failed',
			kind: 'execution',
			severity: 'error',
			tokens: { trace_id: 'tr_9' },
		});
		expect(primaryDestinationFor(ev)).toBe('/monitor?tab=executions&trace_id=tr_9');
	});

	it('primaryDestinationFor never deep-links a placeholder "unknown" trace', () => {
		const ev = makeEvent({
			type: 'execution.failed',
			kind: 'execution',
			severity: 'error',
			tokens: { trace_id: 'unknown' },
		});
		expect(primaryDestinationFor(ev)).toBeNull();
	});

	it('primaryDestinationFor routes import events to the jobs tab by job_id', () => {
		const ev = makeEvent({
			type: 'import.completed',
			kind: 'import',
			severity: 'info',
			tokens: { job_id: 'job_7' },
		});
		expect(primaryDestinationFor(ev)).toBe('/monitor?tab=jobs&job_id=job_7');
	});

	it('primaryDestinationFor routes credential events to the credential detail', () => {
		const ev = makeEvent({
			type: 'credential.expired',
			kind: 'credential',
			severity: 'critical',
			tokens: { credential_id: 'cred_x' },
		});
		expect(primaryDestinationFor(ev)).toBe('/credentials/cred_x');
	});

	it('buildGroupKey prefers the most specific token', () => {
		const key = buildGroupKeyForTest({
			kind: 'execution',
			type: 'execution.completed',
			tokens: { toolkit_id: 'tk_a', operation_id: 'op_a' },
		});
		expect(key).toBe('execution:execution.completed:op_a');
	});

	it('buildGroupKey separates distinct agents and access requests', () => {
		// Two agents registering within the grouping window must NOT collapse
		// into one row (the second registration would hide behind a group head).
		const a = buildGroupKeyForTest({
			kind: 'agent',
			type: 'agent.self_registered',
			tokens: { agent_id: 'agt_1' },
		});
		const b = buildGroupKeyForTest({
			kind: 'agent',
			type: 'agent.self_registered',
			tokens: { agent_id: 'agt_2' },
		});
		expect(a).not.toBe(b);
		const req = buildGroupKeyForTest({
			kind: 'access_request',
			type: 'access_request.filed',
			tokens: { access_request_id: 'arq_1' },
		});
		expect(req).toBe('access_request:access_request.filed:arq_1');
	});

	it('buildGroupKey separates two requests filed by the SAME agent', () => {
		// Real `access_request.filed` events carry BOTH tokens: request_id from
		// the data payload and agent_id from the top-level actor. The request
		// id must win, or a CLI agent filing several requests in one burst
		// collapses them into one row and the extras hide behind the group head.
		const first = buildGroupKeyForTest({
			kind: 'access_request',
			type: 'access_request.filed',
			tokens: { access_request_id: 'arq_1', agent_id: 'agt_same' },
		});
		const second = buildGroupKeyForTest({
			kind: 'access_request',
			type: 'access_request.filed',
			tokens: { access_request_id: 'arq_2', agent_id: 'agt_same' },
		});
		expect(first).toBe('access_request:access_request.filed:arq_1');
		expect(second).toBe('access_request:access_request.filed:arq_2');
	});
});

describe('AgentRail — shell-mounted live surface', () => {
	it('renders the header + seeded backlog, and has no critical a11y violations', async () => {
		const { container } = renderRail(<AgentRail />);
		expect(await screen.findByText('Agent rail')).toBeInTheDocument();
		// A seeded backlog event renders in the feed.
		expect(
			await screen.findByText(/Execution failed: slack\.postMessage/i),
		).toBeInTheDocument();
		await checkA11y(container);
	});

	it('does not hold the feed empty when the cursor rests on the rail during mount', async () => {
		// Regression: `mouseenter` before the backlog fetch resolves used to
		// snapshot ZERO visible ids, so every seeded event was held back and the
		// feed sat at "Holding · 4" with no rows. This is exactly what happens in
		// browser-mode CI, where the shared pointer can be parked over the rail
		// when the iframe mounts (and in prod when a user's cursor rests there
		// during page load). An empty feed must never freeze.
		renderRail(<AgentRail />);
		const aside = await screen.findByRole('complementary', { name: 'Agent rail' });
		fireEvent.mouseEnter(aside);
		expect(
			await screen.findByText(/Execution failed: slack\.postMessage/i),
		).toBeInTheDocument();
	});

	it('collapses and persists the collapsed state to localStorage', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText('Agent rail');

		await user.click(screen.getByRole('button', { name: 'Collapse agent rail' }));
		await waitFor(() =>
			expect(window.localStorage.getItem(RAIL_COLLAPSED_STORAGE_KEY)).toBe('1'),
		);
		expect(screen.getByRole('button', { name: 'Expand agent rail' })).toBeInTheDocument();
		expect(screen.queryByText('Agent rail')).not.toBeInTheDocument();
	});

	it('toggles audio-on-critical and persists the preference', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText('Agent rail');

		// Audio on critical is ON by default.
		const toggle = screen.getByRole('button', { name: /Audio on critical/i });
		expect(toggle).toHaveAttribute('aria-pressed', 'true');
		await user.click(toggle);
		expect(toggle).toHaveAttribute('aria-pressed', 'false');
	});

	it('writes the toast scope to localStorage when changed', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText('Agent rail');

		const select = screen.getByLabelText('Toasts');
		await user.selectOptions(select, 'critical');
		await waitFor(() =>
			expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('critical'),
		);
	});

	it('navigates to the monitor when a feed row is clicked', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		const row = await screen.findByText(/Execution failed: slack\.postMessage/i);
		await user.click(row);
		await waitFor(() =>
			expect(screen.getByTestId('location')).toHaveTextContent('/monitor?tab=executions'),
		);
	});

	it('shows a failure pill for unacknowledged failures and clears it once acknowledged (#671)', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText('Agent rail');
		// The seeded backlog has exactly one unacknowledged failure (the critical
		// execution.failed) → the pill reads "1 unacknowledged failure".
		const pill = await screen.findByRole('button', {
			name: /1 unacknowledged failure in recent activity. Show failures./i,
		});
		expect(pill).toBeInTheDocument();

		// Acknowledge the failure → the count drops to zero and the pill disappears.
		const ack = screen.getAllByRole('button', { name: 'Acknowledge' })[0];
		await user.click(ack);
		await waitFor(() =>
			expect(
				screen.queryByRole('button', { name: /unacknowledged failure/i }),
			).not.toBeInTheDocument(),
		);
	});

	it('focuses the feed on failures when the failure pill is clicked (#671)', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText('Agent rail');
		// Before: an info event (import completed) is visible in the feed.
		await screen.findByText(/Import completed: petstore/i);

		await user.click(
			await screen.findByRole('button', {
				name: /unacknowledged failure in recent activity. Show failures./i,
			}),
		);

		// After: the feed is filtered to error+critical, so the info import row
		// drops out while the critical failure remains.
		await waitFor(() =>
			expect(screen.queryByText(/Import completed: petstore/i)).not.toBeInTheDocument(),
		);
		expect(screen.getByText(/Execution failed: slack\.postMessage/i)).toBeInTheDocument();
	});

	it('focusFailures preserves the operator’s search + kind filters', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText('Agent rail');

		// The operator has narrowed the view: a search term + a kind chip + a
		// severity chip (warning) they picked on purpose.
		const searchBox = screen.getByLabelText('Filter rail events');
		await user.click(searchBox);
		await user.paste('slack');
		const execChip = screen.getByRole('button', { name: 'executions' });
		await user.click(execChip);
		await waitFor(() => expect(execChip).toHaveAttribute('aria-pressed', 'true'));
		const warningChip = screen.getByRole('button', { name: 'warning' });
		await user.click(warningChip);
		await waitFor(() => expect(warningChip).toHaveAttribute('aria-pressed', 'true'));

		// Clicking the failure pill must ADD failure severities, NOT wipe the
		// operator's search or kind filters.
		await user.click(
			screen.getByRole('button', {
				name: /unacknowledged failure in recent activity. Show failures./i,
			}),
		);

		expect(searchBox).toHaveValue('slack');
		expect(screen.getByRole('button', { name: 'executions' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		// The union path: the operator's `warning` chip survives alongside the
		// failure severities that were added.
		expect(screen.getByRole('button', { name: 'warning' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		// And the severities were added: both error and critical chips are pressed.
		expect(screen.getByRole('button', { name: 'error' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		expect(screen.getByRole('button', { name: 'critical' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
	});

	it('re-inserting a dismissed failure toast does not happen on scope change', async () => {
		const user = userEvent.setup();
		// Override the stream with a SINGLE critical event so `latest` is
		// deterministically the failure (failures toast regardless of scope, #671)
		// and no later event overwrites it.
		const failure = {
			event_id: 'evt_only_failure',
			type: 'execution.failed',
			severity: 'critical',
			summary: 'Execution failed: solo.run',
			detail: 'boom',
			created_at: new Date().toISOString(),
			requires_action: true,
			acknowledged: false,
			acknowledged_at: null,
			acknowledged_by: null,
			trace_id: 'tr_solo',
			data: { execution_id: 'exec_solo' },
			_links: { self: '/events/evt_only_failure' },
		};
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({ data: [failure], has_more: false, next_cursor: null }),
			),
			http.get('/events/stream', () => {
				const frame = `event: ${failure.type}\nid: ${failure.event_id}\ndata: ${JSON.stringify(
					failure,
				)}\n\n`;
				const encoder = new TextEncoder();
				const stream = new ReadableStream<Uint8Array>({
					start(controller) {
						controller.enqueue(encoder.encode(frame));
					},
				});
				return new HttpResponse(stream, {
					headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
				});
			}),
		);

		render(
			<QueryClientProvider
				client={
					new QueryClient({
						defaultOptions: {
							queries: { retry: false },
							mutations: { retry: false },
						},
					})
				}
			>
				<MemoryRouter initialEntries={['/dashboard']}>
					<AgentStreamProvider live={true}>
						<Routes>
							<Route
								path="/*"
								element={
									<>
										<AgentRail />
										<ToastHost />
									</>
								}
							/>
						</Routes>
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);

		// The failure toast pops, then the operator dismisses it.
		const dismiss = await screen.findByRole('button', { name: 'Dismiss toast' });
		await user.click(dismiss);
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: 'Dismiss toast' })).not.toBeInTheDocument(),
		);

		// Flip the toast scope (this re-runs ToastHost's insert effect with the
		// SAME `latest`). The dismissed failure toast must NOT re-appear.
		const scopeSelect = screen.getByLabelText('Toasts');
		await user.selectOptions(scopeSelect, 'all');
		await waitFor(() =>
			expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('all'),
		);
		await user.selectOptions(scopeSelect, 'critical');
		await waitFor(() =>
			expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('critical'),
		);
		expect(screen.queryByRole('button', { name: 'Dismiss toast' })).not.toBeInTheDocument();
	});

	it('re-toasts a failure that only TTL-expired (not operator-dismissed) after a scope change', async () => {
		const user = userEvent.setup();
		// A single critical failure so `latest` is deterministically the failure
		// and no later event overwrites it. Failures toast regardless of scope.
		const failure = {
			event_id: 'evt_ttl_failure',
			type: 'execution.failed',
			severity: 'critical',
			summary: 'Execution failed: ttl.run',
			detail: 'boom',
			created_at: new Date().toISOString(),
			requires_action: true,
			acknowledged: false,
			acknowledged_at: null,
			acknowledged_by: null,
			trace_id: 'tr_ttl',
			data: { execution_id: 'exec_ttl' },
			_links: { self: '/events/evt_ttl_failure' },
		};
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({ data: [failure], has_more: false, next_cursor: null }),
			),
			http.get('/events/stream', () => {
				const frame = `event: ${failure.type}\nid: ${failure.event_id}\ndata: ${JSON.stringify(
					failure,
				)}\n\n`;
				const encoder = new TextEncoder();
				const stream = new ReadableStream<Uint8Array>({
					start(controller) {
						controller.enqueue(encoder.encode(frame));
					},
				});
				return new HttpResponse(stream, {
					headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
				});
			}),
		);

		render(
			<QueryClientProvider
				client={
					new QueryClient({
						defaultOptions: {
							queries: { retry: false },
							mutations: { retry: false },
						},
					})
				}
			>
				<MemoryRouter initialEntries={['/dashboard']}>
					<AgentStreamProvider live={true}>
						<Routes>
							<Route
								path="/*"
								element={
									<>
										<AgentRail />
										<ToastHost />
									</>
								}
							/>
						</Routes>
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);

		// The failure toast pops. The rail feed also shows a row for the same
		// event, so key off the toast-only "Dismiss toast" control rather than the
		// title text (which the feed row shares).
		await screen.findByRole('button', { name: 'Dismiss toast' });

		// Let it AUTO-DISMISS via TTL (no operator interaction). The TTL is 6s and
		// the sweeper runs every 250ms, so wait past the horizon.
		await waitFor(
			() =>
				expect(
					screen.queryByRole('button', { name: 'Dismiss toast' }),
				).not.toBeInTheDocument(),
			{ timeout: 9000 },
		);

		// Flip the toast scope — this re-runs ToastHost's insert effect with the
		// SAME `latest`. A TTL-expired failure must re-toast (its id was NOT
		// remembered as dismissed): #671 says a failure must never be missed.
		const scopeSelect = screen.getByLabelText('Toasts');
		await user.selectOptions(scopeSelect, 'all');
		await waitFor(() =>
			expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('all'),
		);
		await screen.findByRole('button', { name: 'Dismiss toast' });
	}, 20000);

	it('acknowledges a seeded action-required event → row flips to Acked', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		// The seeded backlog has multiple action-required events; acknowledge the
		// first (the critical execution failure).
		await screen.findByText(/Execution failed: slack\.postMessage/i);
		const ack = screen.getAllByRole('button', { name: 'Acknowledge' })[0];
		await user.click(ack);
		await waitFor(() => expect(screen.getAllByText('Acked').length).toBeGreaterThanOrEqual(1));
	});

	it('approves a filed access request via the View dialog → records per-item approve decisions', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Access request filed: github read/i);
		// The filed access-request row offers View + Deny (not Approve/Acknowledge).
		await user.click(screen.getByRole('button', { name: 'View' }));

		// The dialog loads the request's items (ar_1 has three pending items: a
		// toolkit use, a credential bind with operation rules, and a platform scope
		// grant) into the "Awaiting Decision" rail, labelled by their target.
		await screen.findByText('Awaiting Decision');
		await screen.findByText(/is requesting access/i);
		const cards = await screen.findAllByText('toolkit');
		expect(cards.length).toBeGreaterThanOrEqual(1);
		expect((await screen.findAllByText('credential')).length).toBeGreaterThanOrEqual(1);

		// The credential.bind item carries permission rules, so the card surfaces a
		// read-only "Operations granted" summary with allow/block effects and the
		// concrete operationIds the binding will enforce on approval.
		expect(await screen.findByText(/Operations granted/i)).toBeInTheDocument();
		expect(screen.getByText('Allow')).toBeInTheDocument();
		expect(screen.getByText('Block')).toBeInTheDocument();
		expect(screen.getByText('repos/get')).toBeInTheDocument();

		// The scope.grant item gets its own "Platform scope" treatment (the scope
		// string as the headline), never mistaken for a per-resource grant.
		expect(
			await screen.findByRole('heading', { name: 'capabilities:execute' }),
		).toBeInTheDocument();
		expect(screen.getAllByText('Platform scope').length).toBeGreaterThanOrEqual(1);

		// Approve all (toolkit + credential + scope), then move to confirm and submit.
		await user.click(screen.getByRole('button', { name: 'Approve all' }));
		await user.click(screen.getByRole('button', { name: /Review & submit/i }));
		await user.click(screen.getByRole('button', { name: /Confirm decision/i }));

		await waitFor(() => {
			expect(decideCalls.length).toBe(1);
			expect(decideCalls[0]).toMatchObject({ request_id: 'ar_1' });
			expect(decideCalls[0].items).toHaveLength(3);
			expect(decideCalls[0].items.every((i) => i.decision === 'approved')).toBe(true);
		});
		// A success terminal screen confirms the grant.
		await screen.findByText('Access granted');
	});

	it('decides items individually in the View dialog (approve one, deny one with a reason)', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Access request filed: github read/i);
		await user.click(screen.getByRole('button', { name: 'View' }));
		await screen.findByText('Awaiting Decision');

		// Approve the first item (its card's Approve button).
		await user.click(screen.getAllByRole('button', { name: 'Approve' })[0]);

		// Deny the second item: clicking its card's "Deny {label}" affordance
		// expands the reason field INLINE on the card. The reason must be typed
		// before "Confirm deny" finalises it into the Denied lane.
		await user.click(screen.getByRole('button', { name: /^Deny credential$/i }));
		const reason = screen.getByLabelText(/Why deny\?/i);
		await user.click(reason);
		await user.paste('Only the toolkit is needed, not the credential.');
		await user.click(screen.getByRole('button', { name: /Confirm deny/i }));

		// Undo the toolkit approval, then re-approve it — the chip's "Move back to
		// pending" affordance returns the item to the rail (client-side draft only).
		await user.click(screen.getByRole('button', { name: /Move toolkit back to pending/i }));
		await user.click(screen.getAllByRole('button', { name: 'Approve' })[0]);

		// Reason was captured inline, so the confirm step submits straight away.
		await user.click(screen.getByRole('button', { name: /Review & submit/i }));

		// Traceability: the confirm step mirrors step 1 for DENIED items too — the
		// denied credential.bind still shows its "Operations granted" summary, and
		// its reason stays in an EDITABLE field (never a read-only preview that
		// would unmount on keystroke), so a reviewer who denied fast can see and
		// refine exactly what they turned down before submitting.
		expect(await screen.findByText(/Operations granted/i)).toBeInTheDocument();
		expect(screen.getByLabelText(/Reason \(sent back to the agent\)/i)).toHaveValue(
			'Only the toolkit is needed, not the credential.',
		);

		await user.click(screen.getByRole('button', { name: /Confirm decision/i }));

		await waitFor(() => {
			expect(decideCalls.length).toBe(1);
			const items = decideCalls[0].items;
			expect(items).toHaveLength(2);
			expect(items.find((i) => i.item_id === 'ari_1')?.decision).toBe('approved');
			const denied = items.find((i) => i.item_id === 'ari_2');
			expect(denied?.decision).toBe('denied');
			expect(denied?.decision_reason).toBe('Only the toolkit is needed, not the credential.');
		});
	});

	it('lets the operator caption reasonless "Deny all" items in the confirm step without the field unmounting', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Access request filed: github read/i);
		await user.click(screen.getByRole('button', { name: 'View' }));
		await screen.findByText('Awaiting Decision');

		// Deny all → every item is denied with NO reason, then advance to confirm.
		await user.click(screen.getByRole('button', { name: 'Deny all' }));
		await user.click(screen.getByRole('button', { name: /Review & submit/i }));

		// Each denied item exposes its own editable reason field. Submit is blocked
		// until they're all captioned (reasonless denials block `missingReason`).
		const fields = await screen.findAllByLabelText(/Reason \(sent back to the agent\)/i);
		expect(fields.length).toBe(3);
		const confirm = screen.getByRole('button', { name: /Confirm decision/i });
		expect(confirm).toBeDisabled();

		// Regression: typing a MULTI-character reason must not unmount the field on
		// the first keystroke. Type char-by-char (not paste) to prove it stays
		// mounted and focused throughout.
		for (const field of fields) {
			await user.click(field);
			await user.type(field, 'Not needed right now.');
		}
		// All three captions persisted in their fields…
		for (const field of screen.getAllByLabelText(/Reason \(sent back to the agent\)/i)) {
			expect(field).toHaveValue('Not needed right now.');
		}
		// …and the request is now submittable.
		expect(confirm).toBeEnabled();
		await user.click(confirm);

		await waitFor(() => {
			expect(decideCalls.length).toBe(1);
			expect(decideCalls[0].items.every((i) => i.decision === 'denied')).toBe(true);
			expect(
				decideCalls[0].items.every((i) => i.decision_reason === 'Not needed right now.'),
			).toBe(true);
		});
	});

	it('denies a whole filed access request from the row fast path after a reason', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Access request filed: github read/i);
		await user.click(screen.getByRole('button', { name: 'Deny' }));

		// A reason field appears; Confirm is disabled until it's filled.
		const reason = await screen.findByLabelText(/Reason \(sent back to the agent\)/i);
		const confirm = screen.getByRole('button', { name: /Confirm deny/i });
		expect(confirm).toBeDisabled();
		// No decision has been sent yet.
		expect(decideCalls.length).toBe(0);

		const reasonText = 'Scope too broad, narrow to a single repo.';
		await user.click(reason);
		await user.paste(reasonText);
		expect(confirm).toBeEnabled();
		await user.click(confirm);

		await waitFor(() => {
			expect(decideCalls.length).toBe(1);
			// The fast path denies every pending item with the one reason.
			expect(decideCalls[0].items.every((i) => i.decision === 'denied')).toBe(true);
			expect(decideCalls[0].items.every((i) => i.decision_reason === reasonText)).toBe(true);
		});
	});

	it('drops SSE heartbeat frames — no "Platform" row leaks into the feed', async () => {
		// The mocked /events/stream emits an `event: heartbeat` frame ahead of the
		// real backlog. The client must skip it; otherwise it adapts into an
		// `other`-kind ("Platform") row with a blank title.
		render(
			<QueryClientProvider
				client={
					new QueryClient({
						defaultOptions: {
							queries: { retry: false },
							mutations: { retry: false },
						},
					})
				}
			>
				<MemoryRouter initialEntries={['/dashboard']}>
					<AgentStreamProvider live={true}>
						<Routes>
							<Route path="/*" element={<AgentRail />} />
						</Routes>
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);
		// A real seeded event arrives over the same stream.
		await screen.findByText(/Execution failed: slack\.postMessage/i);
		// The heartbeat must NOT have produced a "Platform" row.
		expect(screen.queryByText('Platform')).not.toBeInTheDocument();
	});
});

describe('rail — oauth additions (3a-5, phase-3a §4.8)', () => {
	const OAUTH_CLIENT_ID = 'oc_dcr_app';

	function registeredWire(over: Partial<EventResponse> = {}): EventResponse {
		return wireEvent({
			event_id: 'evt_oauth_registered',
			type: 'oauth_client.registered',
			severity: 'info' as EventResponse['severity'],
			summary: 'OAuth client registered: MCP App',
			requires_action: true,
			data: { oauth_client_id: OAUTH_CLIENT_ID },
			...over,
		});
	}

	it('kindForType buckets the oauth_client.* / oauth_grant.* namespaces into oauth', () => {
		expect(kindForType('oauth_client.registered')).toBe('oauth');
		expect(kindForType('oauth_client.approved')).toBe('oauth');
		expect(kindForType('oauth_grant.created')).toBe('oauth');
		expect(kindForType('oauth_grant.revoked')).toBe('oauth');
	});

	it('inlineActionsFor offers Review (→ Settings queue) + Acknowledge for a DCR registration', () => {
		const ev = makeEvent({
			type: 'oauth_client.registered',
			kind: 'oauth',
			requiresAction: true,
			tokens: { oauth_client_id: OAUTH_CLIENT_ID },
			groupKey: `oauth:oauth_client.registered:${OAUTH_CLIENT_ID}`,
		});
		const actions = inlineActionsFor(ev);
		const review = actions.find((a) => a.kind === 'view_oauth_queue');
		expect(review?.label).toBe('Review');
		// The D7 approve/deny verbs live on the Settings approval queue tab.
		expect(review?.href?.(ev)).toBe('/settings?tab=queue');
		expect(actions.map((a) => a.kind)).toContain('acknowledge');
		// Once settled the actionable slot goes passive.
		expect(inlineActionsFor({ ...ev, acknowledged: true }).map((a) => a.kind)).not.toContain(
			'view_oauth_queue',
		);
	});

	it('primaryDestinationFor deep-links grant events to the agent, client events to the queue', () => {
		// A grant row names the bound agent — its Connected-clients panel is the
		// §4.8 surface that lists (and can revoke) the grant.
		const grant = makeEvent({
			type: 'oauth_grant.created',
			kind: 'oauth',
			tokens: { grant_id: 'ocg_1', agent_id: 'agt_42' },
		});
		expect(primaryDestinationFor(grant)).toBe('/agents/agt_42');
		// A client lifecycle row has no agent — it goes to the approval queue.
		const registered = makeEvent({
			type: 'oauth_client.registered',
			kind: 'oauth',
			tokens: { oauth_client_id: OAUTH_CLIENT_ID },
		});
		expect(primaryDestinationFor(registered)).toBe('/settings?tab=queue');
	});

	it('settles the actionable registration row when the APPROVE event arrives over SSE', async () => {
		// Backlog: the actionable registration alone. SSE then delivers the
		// approve decision — the live mirror must settle the registered row
		// (drop its Review prompt) without waiting for a backlog refetch.
		const registered = registeredWire();
		const approved = wireEvent({
			event_id: 'evt_oauth_approved',
			type: 'oauth_client.approved',
			summary: 'OAuth client approved: MCP App',
			data: { oauth_client_id: OAUTH_CLIENT_ID },
		});
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({ data: [registered], has_more: false, next_cursor: null }),
			),
			http.get('/events/stream', () => {
				const frames = [registered, approved]
					.map(
						(e) =>
							`event: ${e.type}\nid: ${e.event_id}\ndata: ${JSON.stringify(e)}\n\n`,
					)
					.join('');
				const encoder = new TextEncoder();
				const stream = new ReadableStream<Uint8Array>({
					start(controller) {
						controller.enqueue(encoder.encode(frames));
					},
				});
				return new HttpResponse(stream, {
					headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
				});
			}),
		);
		render(
			<QueryClientProvider
				client={
					new QueryClient({
						defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
					})
				}
			>
				<MemoryRouter initialEntries={['/dashboard']}>
					<AgentStreamProvider live={true}>
						<Routes>
							<Route path="/*" element={<AgentRail />} />
						</Routes>
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);
		// Both rows land in the feed…
		await screen.findByText(/OAuth client registered: MCP App/i);
		await screen.findByText(/OAuth client approved: MCP App/i);
		// …and the registration's actionable Review prompt is gone (settled).
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument(),
		);
	});

	it('settles the actionable registration row on DENY via the context (no SSE event exists)', async () => {
		// A deny emits no oauth_client.* event (§4.8/D7) — the deny mutation
		// calls `settleOAuthClientRegistration` itself. Drive the context handle
		// exactly like `useDenyOAuthClient` does and watch the row settle.
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({
					data: [registeredWire()],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		let settle: ((oauthClientId: string) => void) | undefined;
		function SettleProbe() {
			settle = useAgentStream().settleOAuthClientRegistration;
			return null;
		}
		renderRail(
			<>
				<AgentRail />
				<SettleProbe />
			</>,
		);
		await screen.findByRole('button', { name: 'Review' });

		// A settle for a DIFFERENT client must not touch the row.
		act(() => settle?.('oc_other_client'));
		expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument();

		act(() => settle?.(OAUTH_CLIENT_ID));
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument(),
		);
		// The row itself stays in the feed — only its actionable slot settled.
		expect(screen.getByText(/OAuth client registered: MCP App/i)).toBeInTheDocument();
	});
});

describe('ToastHost — scoped transient notifications', () => {
	it('does not render any toast on mount with a backlog-only stream', async () => {
		renderRail(<ToastHost />);
		// No `latest` fires when live={false}, so nothing pops.
		await waitFor(() => {
			expect(screen.queryByRole('button', { name: 'Dismiss toast' })).not.toBeInTheDocument();
		});
	});
});

describe('access-request repository — real contract against the mock', () => {
	it('listAccessRequests returns the AccessRequestResponse shape (actor_id, approve_url, expires_at, created_by)', async () => {
		const page = await listAccessRequests({ status: 'pending' });
		expect(page.data.length).toBeGreaterThan(0);
		const ar = page.data[0];
		// Contract pin: the agent is `actor_id`, the human filer is `requested_by`
		// — they MUST be distinct fields (the bug was conflating them).
		expect(ar.actor_id).toBeTruthy();
		expect(ar.requested_by).toBeTruthy();
		expect(ar.actor_id).not.toBe(ar.requested_by);
		// Required AccessRequestResponse fields the mock previously omitted.
		expect(ar.filed_at).toBeTruthy();
		expect(ar.expires_at).toBeTruthy();
		expect((ar as unknown as { approve_url?: string }).approve_url).toMatch(/access-requests/);
		expect((ar as unknown as { created_by?: string }).created_by).toBeTruthy();
	});

	it('listAccessRequests filters by status (the mock falls through to the queried status, not just pending)', async () => {
		// A status the seed has none of must come back empty — proving the handler
		// honours the filter rather than always returning the pending seed.
		const approved = await listAccessRequests({ status: 'approved' });
		expect(approved.data.every((r) => r.status === 'approved')).toBe(true);
		const pending = await listAccessRequests({ status: 'pending' });
		expect(pending.data.length).toBeGreaterThan(0);
		expect(pending.data.every((r) => r.status === 'pending')).toBe(true);
	});

	it('getAccessRequest surfaces a typed RailApiError for an unknown id', async () => {
		await expect(getAccessRequest('ar_does_not_exist')).rejects.toMatchObject({
			name: 'RailApiError',
			status: 404,
		});
	});
});
