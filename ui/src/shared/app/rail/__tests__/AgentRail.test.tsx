import { describe, it, expect, beforeEach, afterEach, onTestFinished } from 'vitest';
import { page, cdp } from 'vitest/browser';
import { act, useState, type ReactElement } from 'react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within, userEvent, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { ActivityStreamPanel } from '@/shared/app/rail/ActivityStreamPanel';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { ShellActivityEffects } from '@/shared/app/rail/ShellActivityEffects';
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
	isRetiredEventType,
	kindForType,
	matchesToastScope,
	primaryDestinationFor,
	severityForWire,
	severityStripeClass,
	streamDayKey,
	unacknowledgedFailureCount,
	useAgentStream,
	RAIL_COLLAPSED_STORAGE_KEY,
	TOAST_SCOPE_STORAGE_KEY,
	writeToastScope,
	type StreamEvent,
} from '@/shared/lib/agentStream';
import { clearToken, setToken, type EventResponse } from '@/shared/api';

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
					{/* The shell owns the stream-level effects (it outlives the rail). */}
					<ShellActivityEffects />
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
	// The rail ships collapsed; these tests exercise the open rail, so start
	// from a user who has expanded it (the default is asserted on its own).
	window.localStorage.setItem(RAIL_COLLAPSED_STORAGE_KEY, '0');
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
		expect(kindForType('credential.expired')).toBe('credential');
		expect(kindForType('agent.self_registered')).toBe('agent');
		expect(kindForType('webhook.delivered')).toBe('other');
	});

	it('isRetiredEventType flags the retired access_request namespace (theme-7)', () => {
		// Historical access-request events can still arrive from an old backlog
		// page or a reconnect redelivery — they're tolerated at ingestion (dropped,
		// never rendered) so an old event can't crash the feed.
		expect(isRetiredEventType('access_request.filed')).toBe(true);
		expect(isRetiredEventType('access_request.approved')).toBe(true);
		expect(isRetiredEventType('execution.failed')).toBe(false);
		// And should one slip past ingestion, it buckets into `other`, not a crash.
		expect(kindForType('access_request.filed')).toBe('other');
	});

	it('severityForWire normalises the enum + bare strings', () => {
		expect(severityForWire('critical')).toBe('critical');
		expect(severityForWire('error')).toBe('error');
		expect(severityForWire('warning')).toBe('warning');
		expect(severityForWire('info')).toBe('info');
	});

	// Issue #907: critical and error shared an IDENTICAL rail stripe
	// (`border-l-danger` for both, same width) — an operator had no visual way
	// to tell a single failure from a chronic-failure escalation without
	// opening the row. Critical now renders a wider stripe on top of the same
	// danger colour, so the two failure tiers stay visually related but not
	// indistinguishable.
	it('severityStripeClass gives critical a distinct treatment from error', () => {
		const critical = severityStripeClass('critical');
		const error = severityStripeClass('error');
		expect(critical).not.toBe(error);
		// Both stay in the danger colour family — they're still both failures.
		expect(critical).toContain('border-l-danger');
		expect(error).toContain('border-l-danger');
	});

	it('severityStripeClass gives warning and info their own colours', () => {
		expect(severityStripeClass('warning')).toContain('border-l-warning');
		expect(severityStripeClass('info')).toContain('border-l-primary');
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
				type: 'credential.expired',
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
		expect(primaryDestinationFor(ev)).toBe('/monitor?show=calls&execution_id=exec%20x');
	});

	it('primaryDestinationFor falls back to trace_id when an execution has no execution_id', () => {
		const ev = makeEvent({
			type: 'execution.failed',
			kind: 'execution',
			severity: 'error',
			tokens: { trace_id: 'tr_9' },
		});
		expect(primaryDestinationFor(ev)).toBe('/monitor?show=calls&trace_id=tr_9');
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
		expect(primaryDestinationFor(ev)).toBe('/monitor?show=jobs&job_id=job_7');
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

	it('buildGroupKey separates distinct agents', () => {
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
	});
});

describe('AgentRail — shell-mounted Activity surface', () => {
	it('renders the header + seeded backlog, and has no critical a11y violations', async () => {
		const { container } = renderRail(<AgentRail />);
		expect(await screen.findByRole('complementary', { name: 'Activity' })).toBeInTheDocument();
		expect(screen.getByRole('log', { name: 'Activity feed' })).toBeInTheDocument();
		// A seeded backlog event renders in the feed.
		expect(await screen.findByText(/Failed: slack\.postMessage/i)).toBeInTheDocument();
		await checkA11y(container);
	});

	it('writes rows in plain language: "<actor> · <summary>"', async () => {
		// The actor directory only loads for a signed-in session.
		setToken('test-token');
		onTestFinished(() => clearToken());
		renderRail(<AgentRail />);
		// The fixture's failure was caused by `invoice-bot`, which the actor
		// directory resolves to "Invoice Bot".
		const row = await screen.findByRole('link', {
			name: /^Invoice Bot: Execution failed: slack\.postMessage/,
		});
		expect(row).toBeInTheDocument();
	});

	it('has no search box, filter chips or event counter any more', async () => {
		renderRail(<AgentRail />);
		await screen.findByText(/Failed: slack\.postMessage/i);
		expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
		expect(screen.queryByLabelText('Filter rail events')).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'executions' })).not.toBeInTheDocument();
		expect(screen.queryByText(/\d+ events/)).not.toBeInTheDocument();
	});

	it('is a containing block, so sr-only descendants cannot leak scroll height (phantom-scroll pin)', async () => {
		// Regression pin: feed rows carry `sr-only` spans (position: absolute).
		// Absolute boxes are clipped only by CONTAINING-BLOCK ancestors — the
		// aside's static `overflow-hidden` didn't qualify, so those spans
		// escaped to the shell's sticky wrapper and added ~240px of phantom
		// document scroll on short pages. The aside must be `position: relative`.
		renderRail(
			<div style={{ display: 'flex', height: '320px' }}>
				<AgentRail />
			</div>,
		);
		const aside = await screen.findByRole('complementary', { name: 'Activity' });
		await screen.findByText(/Failed: slack\.postMessage/i);
		expect(getComputedStyle(aside).position).toBe('relative');
		expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(window.innerHeight);
	});

	it('starts collapsed to the strip on a first visit', async () => {
		window.localStorage.removeItem(RAIL_COLLAPSED_STORAGE_KEY);
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		const expand = await screen.findByRole('button', { name: /^Show live activity/ });
		expect(screen.queryByRole('log', { name: 'Activity feed' })).not.toBeInTheDocument();
		await user.click(expand);
		expect(await screen.findByRole('log', { name: 'Activity feed' })).toBeInTheDocument();
		await waitFor(() =>
			expect(window.localStorage.getItem(RAIL_COLLAPSED_STORAGE_KEY)).toBe('0'),
		);
	});

	it('collapses to a strip and persists the collapsed state to localStorage', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Failed: slack\.postMessage/i);

		await user.click(screen.getByRole('button', { name: 'Collapse activity' }));
		await waitFor(() =>
			expect(window.localStorage.getItem(RAIL_COLLAPSED_STORAGE_KEY)).toBe('1'),
		);
		// One expand control; its name carries the failure signal (red dot).
		expect(
			await screen.findByRole('button', {
				name: /^Show live activity.*1 unacknowledged failure/,
			}),
		).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByRole('log', { name: 'Activity feed' })).not.toBeInTheDocument(),
		);
	});

	it('hands keyboard focus across the collapse/expand swap instead of dropping it', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Failed: slack\.postMessage/i);
		await user.click(screen.getByRole('button', { name: 'Collapse activity' }));
		const expand = await screen.findByRole('button', { name: /^Show live activity/ });
		await waitFor(() => expect(expand).toHaveFocus());
		await user.keyboard('{Enter}');
		await waitFor(() =>
			expect(screen.getByRole('button', { name: 'Collapse activity' })).toHaveFocus(),
		);
	});

	it('navigates to the monitor when a feed row is clicked', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		const row = await screen.findByRole('link', {
			name: /Execution failed: slack\.postMessage.*Open detail/i,
		});
		await user.click(row);
		await waitFor(() =>
			expect(screen.getByTestId('location')).toHaveTextContent('/monitor?show=calls'),
		);
	});

	it('links the footer to the activity log in Monitor', async () => {
		renderRail(<AgentRail />);
		const link = await screen.findByRole('link', { name: 'Open in Monitor →' });
		expect(link).toHaveAttribute('href', '/monitor?view=activity');
	});

	it('"Failures only" hides non-failures and counts unacknowledged ones (#671)', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Imported petstore/i);

		const toggle = screen.getByRole('button', { name: /^Failures only/ });
		expect(toggle).toHaveAttribute('aria-pressed', 'false');
		expect(toggle).toHaveAccessibleName(/1 unacknowledged/);
		await user.click(toggle);
		expect(toggle).toHaveAttribute('aria-pressed', 'true');
		await waitFor(() =>
			expect(screen.queryByText(/Imported petstore/i)).not.toBeInTheDocument(),
		);
		expect(screen.getByText(/Failed: slack\.postMessage/i)).toBeInTheDocument();
	});

	it('acknowledging a failure dims the row (no "Acked" label) and clears the count', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Failed: slack\.postMessage/i);
		const ack = screen.getAllByRole('button', { name: 'Acknowledge' })[0];
		await user.click(ack);
		// The row re-renders as the compact line, so re-query it.
		await waitFor(() =>
			expect(
				screen.getByText(/Failed: slack\.postMessage/i).closest('.opacity-55'),
			).not.toBeNull(),
		);
		expect(screen.queryByText('Acked')).not.toBeInTheDocument();
		expect(screen.getByRole('button', { name: /^Failures only/ })).not.toHaveAccessibleName(
			/unacknowledged/,
		);
	});

	it('offers "Only this agent" on /agents/:agentId, and scoping fetches its backlog by actor', async () => {
		// The shortcut names the agent, which needs the (signed-in) actor directory.
		setToken('test-token');
		onTestFinished(() => clearToken());
		const user = userEvent.setup();
		const seen: string[] = [];
		worker.events.on('request:start', ({ request }) => {
			if (new URL(request.url).pathname === '/events') seen.push(request.url);
		});
		renderRail(<AgentRail />, '/agents/support-triage');
		const filter = await screen.findByRole('button', { name: /^Show activity for/ });
		// Navigating to an agent never changes the lens by itself…
		expect(filter).toHaveAccessibleName(/Everyone/);
		// …the Filter offers the shortcut instead.
		await user.click(filter);
		const panel = await screen.findByRole('dialog', { name: 'Filter activity' });
		expect(await within(panel).findByText('On this page')).toBeInTheDocument();
		await user.click(within(panel).getByRole('button', { name: 'Only this agent' }));
		expect(filter).toHaveAccessibleName(/Support Triage/);
		await waitFor(() =>
			expect(
				seen.some((u) => {
					const q = new URL(u).searchParams;
					return (
						q.get('actor_id') === 'support-triage' && q.get('actor_type') === 'agent'
					);
				}),
			).toBe(true),
		);
		worker.events.removeAllListeners();
		// Other actors' rows drop out; this agent's stay.
		expect(await screen.findByText(/github\.repos\.list/)).toBeInTheDocument();
		expect(screen.queryByText(/Failed: slack\.postMessage/)).not.toBeInTheDocument();
		// The shortcut goes away once it's applied.
		await user.click(filter);
		expect(
			within(await screen.findByRole('dialog', { name: 'Filter activity' })).queryByRole(
				'button',
				{ name: 'Only this agent' },
			),
		).not.toBeInTheDocument();
		// And the footer carries the lens to Monitor.
		expect(screen.getByRole('link', { name: 'Open in Monitor →' })).toHaveAttribute(
			'href',
			'/monitor?view=activity&actor_id=support-triage&actor_type=agent',
		);
	});

	it("offers the shortcut from /agents?agent=<id> and keeps the user's lens otherwise", async () => {
		setToken('test-token');
		onTestFinished(() => clearToken());
		const user = userEvent.setup();
		renderRail(<AgentRail />, '/agents?agent=invoice-bot');
		const filter = await screen.findByRole('button', { name: /^Show activity for/ });
		expect(await screen.findByText(/Imported petstore/)).toBeInTheDocument();
		expect(filter).toHaveAccessibleName(/Everyone/);
		await user.click(filter);
		await user.click(await screen.findByRole('button', { name: 'Only this agent' }));
		expect(filter).toHaveAccessibleName(/Invoice Bot/);
		await waitFor(() =>
			expect(screen.queryByText(/Imported petstore/)).not.toBeInTheDocument(),
		);
		// Widening back is the user's call, from the same control.
		await user.click(filter);
		await user.click(await screen.findByRole('button', { name: /^Everyone/ }));
		expect(filter).toHaveAccessibleName(/Everyone/);
		expect(await screen.findByText(/Imported petstore/)).toBeInTheDocument();
	});

	it('names the agent in an empty scoped feed and offers a way back to everyone', async () => {
		setToken('test-token');
		onTestFinished(() => clearToken());
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		const filter = await screen.findByRole('button', { name: /^Show activity for/ });
		await user.click(filter);
		const panel = await screen.findByRole('dialog', { name: 'Filter activity' });
		// People sit beside agents — the lens is anyone, not just agents.
		expect(await within(panel).findByText('People')).toBeInTheDocument();
		expect(
			within(panel).getAllByRole('button', { name: /^Admin User/ }).length,
		).toBeGreaterThan(0);
		await user.click(await within(panel).findByRole('button', { name: /^Nightly Reporter/ }));
		expect(await screen.findByText(/No recent activity from/)).toHaveTextContent(
			'No recent activity from Nightly Reporter.',
		);
		await user.click(screen.getByRole('button', { name: 'Show everyone' }));
		expect(filter).toHaveAccessibleName(/Everyone/);
		expect(await screen.findByText(/Imported petstore/)).toBeInTheDocument();
	});

	it('Escape closes the Filter popover and hands focus back to its trigger', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		const filter = await screen.findByRole('button', { name: /^Show activity for/ });
		await user.click(filter);
		const panel = await screen.findByRole('dialog', { name: 'Filter activity' });
		await user.click(within(panel).getByRole('button', { name: 'Calls' }));
		await user.keyboard('{Escape}');
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: 'Filter activity' }),
			).not.toBeInTheDocument(),
		);
		expect(filter).toHaveFocus();
	});

	it('filters by kind of activity from the Filter popover', async () => {
		const user = userEvent.setup();
		renderRail(<AgentRail />);
		await screen.findByText(/Imported petstore/);
		const filter = screen.getByRole('button', { name: /^Show activity for/ });
		await user.click(filter);
		const what = within(await screen.findByRole('group', { name: 'What' }));
		await user.click(what.getByRole('button', { name: 'Calls' }));
		expect(what.getByRole('button', { name: 'Calls' })).toHaveAttribute('aria-pressed', 'true');
		await waitFor(() =>
			expect(screen.queryByText(/Imported petstore/)).not.toBeInTheDocument(),
		);
		expect(screen.getByText(/Failed: slack\.postMessage/)).toBeInTheDocument();
		// The trigger reads the choice back.
		expect(filter).toHaveAccessibleName(/Everyone.*Calls/);
		await user.click(screen.getByRole('button', { name: 'Reset filters' }));
		expect(await screen.findByText(/Imported petstore/)).toBeInTheDocument();
	});

	it('counts what arrived while paused, and "N new" resumes the feed', async () => {
		const user = userEvent.setup();
		let push: ((frame: string) => void) | null = null;
		worker.use(
			http.get(
				'/events/stream',
				() =>
					new HttpResponse(
						new ReadableStream({
							start(controller) {
								const enc = new TextEncoder();
								push = (frame) => controller.enqueue(enc.encode(frame));
							},
						}),
						{ headers: { 'Content-Type': 'text/event-stream' } },
					),
			),
		);
		render(
			<QueryClientProvider
				client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
			>
				<MemoryRouter initialEntries={['/dashboard']}>
					<AgentStreamProvider live={true}>
						<AgentRail />
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);
		await screen.findByText(/Imported petstore/);
		await waitFor(() => expect(push).not.toBeNull());
		await user.click(screen.getByRole('button', { name: 'Pause live feed' }));
		const live = wireEvent({
			event_id: 'evt_while_paused',
			type: 'import.completed',
			summary: 'Import completed: weather-api',
			created_at: new Date().toISOString(),
		});
		act(() =>
			push?.(`event: ${live.type}\nid: ${live.event_id}\ndata: ${JSON.stringify(live)}\n\n`),
		);
		const pill = await screen.findByRole('button', { name: /1\s*new/ });
		expect(screen.queryByText(/Imported weather-api/)).not.toBeInTheDocument();
		await user.click(pill);
		expect(await screen.findByText(/Imported weather-api/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Pause live feed' })).toBeInTheDocument();
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: /\d+\s*new/ })).not.toBeInTheDocument(),
		);
	});

	it('keeps Failures only and pause across remounts and shares them with the Monitor panel', async () => {
		const user = userEvent.setup();
		// The rail unmounts on Monitor and remounts on the way back; its view
		// state lives in the provider, so it must come back as the user left it.
		function Host() {
			const [showRail, setShowRail] = useState(true);
			return (
				<>
					<button type="button" onClick={() => setShowRail((s) => !s)}>
						Toggle rail
					</button>
					{showRail && <AgentRail />}
					<ActivityStreamPanel />
				</>
			);
		}
		renderRail(<Host />);
		const rail = await screen.findByRole('complementary', { name: 'Activity' });
		await screen.findAllByText(/Imported petstore/);
		await user.click(within(rail).getByRole('button', { name: /^Failures only/ }));
		await user.click(within(rail).getByRole('button', { name: 'Pause live feed' }));
		// Monitor's All / Failures control is the same choice.
		const panelFilter = screen.getByRole('group', { name: 'Filter activity' });
		expect(within(panelFilter).getByRole('button', { name: 'Failures' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);

		await user.click(screen.getByRole('button', { name: 'Toggle rail' }));
		await waitFor(() =>
			expect(
				screen.queryByRole('complementary', { name: 'Activity' }),
			).not.toBeInTheDocument(),
		);
		await user.click(screen.getByRole('button', { name: 'Toggle rail' }));
		const back = await screen.findByRole('complementary', { name: 'Activity' });
		expect(within(back).getByRole('button', { name: /^Failures only/ })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		expect(within(back).getByRole('button', { name: 'Resume live feed' })).toBeInTheDocument();

		// And flipping it from the panel flips the rail.
		await user.click(within(panelFilter).getByRole('button', { name: 'All' }));
		expect(within(back).getByRole('button', { name: /^Failures only/ })).toHaveAttribute(
			'aria-pressed',
			'false',
		);
	});

	it('holds "Reconnecting…" while the stream keeps failing (no Offline/Connecting flicker)', async () => {
		worker.use(http.get('/events/stream', () => new HttpResponse(null, { status: 503 })));
		render(
			<QueryClientProvider
				client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
			>
				<MemoryRouter initialEntries={['/dashboard']}>
					<AgentStreamProvider live={true}>
						<AgentRail />
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);
		expect(await screen.findByText('Reconnecting…')).toBeInTheDocument();
		expect(screen.getByRole('img', { name: 'Stream offline' })).toBeInTheDocument();
		// Across the first retry (1s backoff) the label must not blink away.
		await new Promise((r) => setTimeout(r, 1500));
		expect(screen.getByText('Reconnecting…')).toBeInTheDocument();
		expect(screen.queryByRole('img', { name: 'Connecting to stream' })).not.toBeInTheDocument();
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
		useSingleFailureStream(failure);
		renderLiveRailWithToasts();

		// The failure toast pops, then the operator dismisses it.
		const dismiss = await screen.findByRole('button', { name: 'Dismiss toast' });
		await user.click(dismiss);
		await waitFor(() =>
			expect(screen.queryByRole('button', { name: 'Dismiss toast' })).not.toBeInTheDocument(),
		);

		// Flip the toast scope (this re-runs ToastHost's insert effect with the
		// SAME `latest`). The dismissed failure toast must NOT re-appear.
		// (The switch lives in the Notifications settings; flip it at the source.)
		act(() => writeToastScope('all'));
		expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('all');
		act(() => writeToastScope('critical'));
		expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('critical');
		expect(screen.queryByRole('button', { name: 'Dismiss toast' })).not.toBeInTheDocument();
	});

	it('re-toasts a failure that only TTL-expired (not operator-dismissed) after a scope change', async () => {
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
		useSingleFailureStream(failure);
		renderLiveRailWithToasts();

		// The failure toast pops. Key off the toast-only "Dismiss toast" control
		// rather than the title text (which the feed row shares).
		await screen.findByRole('button', { name: 'Dismiss toast' });

		// Let it AUTO-DISMISS via TTL (6s; the sweeper runs every 250ms).
		await waitFor(
			() =>
				expect(
					screen.queryByRole('button', { name: 'Dismiss toast' }),
				).not.toBeInTheDocument(),
			{ timeout: 9000 },
		);

		// Flip the toast scope — a TTL-expired failure must re-toast (its id was
		// NOT remembered as dismissed): #671 says a failure must never be missed.
		act(() => writeToastScope('all'));
		expect(window.localStorage.getItem(TOAST_SCOPE_STORAGE_KEY)).toBe('all');
		await screen.findByRole('button', { name: 'Dismiss toast' });
	}, 20000);

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
		await screen.findByText(/Failed: slack\.postMessage/i);
		expect(screen.queryByText('Platform')).not.toBeInTheDocument();
	});
});

/** Serve exactly one failure from both the backlog and the live stream. */
function useSingleFailureStream(
	failure: Record<string, unknown> & { type: string; event_id: string },
) {
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
}

function renderLiveRailWithToasts() {
	return render(
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
}

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
