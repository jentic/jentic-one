import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { page } from '@vitest/browser/context';
import type { ReactElement } from 'react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, userEvent, checkA11y } from '@/__tests__/test-utils';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { ToastHost } from '@/shared/app/rail/ToastHost';
import {
	AgentStreamProvider,
	adaptEvent,
	buildGroupKeyForTest,
	buildTraceBundle,
	inlineActionsFor,
	kindForType,
	matchesToastScope,
	primaryDestinationFor,
	severityForWire,
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
	// The rail is `hidden xl:flex` (xl = 1280px). Widen the page so the rail
	// and its controls join the accessibility tree; role queries skip
	// `display:none` content.
	await page.viewport(1440, 900);
});
afterEach(() => {
	window.localStorage.clear();
});

describe('agentStream — wire adaptation + pure helpers', () => {
	it('kindForType derives the namespace and buckets the unknown', () => {
		expect(kindForType('execution.failed')).toBe('execution');
		expect(kindForType('import.completed')).toBe('import');
		expect(kindForType('access_request.filed')).toBe('access_request');
		expect(kindForType('credential.expired')).toBe('credential');
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
		expect(primaryDestinationFor(ev)).toBe('/monitor?tab=executions&execution=exec%20x');
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
