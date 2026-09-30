import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { useLocation } from 'react-router';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import MonitorPage from '@/modules/monitor/pages/MonitorPage';
import { monitorHandlers } from '@/modules/monitor/mocks/handlers';

/** Mirrors the router location search string into the DOM for assertions. */
function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

/**
 * MonitorPage is rendered under AuthProvider so the org:admin-gated surfaces
 * (Usage, the Audit log, cancel job) resolve against the mocked `/users/me`
 * admin user. A seeded token makes the profile query fire.
 *
 * Defaults to the API calls source (`?show=calls`) because most of these
 * specs exercise the trace log; the Everything feed is the implicit landing
 * view (asserted separately).
 */
function renderMonitor(route = '/app/monitor?show=calls') {
	return renderWithProviders(
		<AuthProvider>
			<MonitorPage />
			<LocationProbe />
			<Toaster />
		</AuthProvider>,
		{ route },
	);
}

function currentParams() {
	return new URLSearchParams(screen.getByTestId('location-search').textContent ?? '');
}

/** The Breakdown table's own grouping toggle (the charts above have one each). */
function breakdownLens(name: string) {
	return toggle('Breakdown grouping', name);
}

/** A button inside one of the toolbar's labelled toggle groups. */
function toggle(group: string, name: string) {
	return within(screen.getByRole('group', { name: group })).getByRole('button', { name });
}

const MEMBER = {
	id: '00000000-0000-0000-0000-000000000002',
	email: 'member@local',
	first_name: 'Member',
	last_name: 'User',
	active: true,
	permissions: [],
	must_change_password: false,
	created_at: '2026-01-01T00:00:00Z',
	updated_at: null,
};

describe('MonitorPage', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		// `/executions`, `/events`(+stream), and `/audit` are also mocked by the
		// agents + Activity rail handlers, which register earlier in the global
		// table. Install Monitor's handlers at runtime so they take precedence
		// for this page's requests; MSW resets runtime handlers after each test.
		worker.use(...monitorHandlers);
	});

	it('lands an admin on the Overview: stat strip, charts and the docked live panel', async () => {
		renderMonitor('/app/monitor');
		expect(await screen.findByText('Execution Volume')).toBeInTheDocument();
		expect(screen.getByRole('region', { name: 'Usage at a glance' })).toBeInTheDocument();
		expect(screen.getByRole('region', { name: 'Live activity' })).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'Expand activity to the full log' }),
		).toBeInTheDocument();
		expect(screen.queryByRole('group', { name: 'Activity source' })).not.toBeInTheDocument();
	});

	it('expands the panel into the full log and folds it back, keeping the window', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?days=30');
		await screen.findByText('Execution Volume');

		await user.click(screen.getByRole('button', { name: 'Expand activity to the full log' }));
		expect(
			await screen.findByRole('link', { name: 'Execution failed: github-api' }),
		).toBeInTheDocument();
		expect(toggle('Activity source', 'Everything')).toHaveAttribute('aria-pressed', 'true');
		expect(currentParams().get('view')).toBe('activity');

		// A focused source stays expanded; going back to Everything too.
		await user.click(toggle('Activity source', 'API calls'));
		await screen.findByText('POST /v1/charges');
		await user.click(toggle('Activity source', 'Everything'));
		await screen.findByRole('link', { name: 'Execution failed: github-api' });

		await user.click(screen.getByRole('button', { name: 'Overview' }));
		expect(await screen.findByText('Execution Volume')).toBeInTheDocument();
		const params = currentParams();
		expect(params.get('view')).toBeNull();
		expect(params.get('show')).toBeNull();
		expect(params.get('days')).toBe('30');
	});

	it('gives non-admins the full log only — no Overview, no usage fetch', async () => {
		// Let the previous test's in-flight usage reads settle first, so they
		// can't land on this test's counting handler.
		await new Promise((resolve) => setTimeout(resolve, 250));
		let usageCalls = 0;
		worker.use(
			http.get('/users/me', () => HttpResponse.json(MEMBER)),
			http.get('/monitoring/usage', () => {
				usageCalls += 1;
				return new HttpResponse(null, { status: 403 });
			}),
		);
		renderMonitor('/app/monitor?tab=usage');
		expect(
			await screen.findByRole('link', { name: 'Execution failed: github-api' }),
		).toBeInTheDocument();
		await waitFor(() =>
			expect(
				screen.queryByRole('region', { name: 'Usage at a glance' }),
			).not.toBeInTheDocument(),
		);
		expect(screen.queryByRole('button', { name: 'Overview' })).not.toBeInTheDocument();
		expect(usageCalls).toBe(0);
	});

	it('offers non-admins no Audit log source and ignores ?show=audit', async () => {
		worker.use(http.get('/users/me', () => HttpResponse.json(MEMBER)));
		renderMonitor('/app/monitor?show=audit');
		await screen.findByRole('link', { name: 'Execution failed: github-api' });
		await waitFor(() =>
			expect(
				within(screen.getByRole('group', { name: 'Activity source' })).queryByRole(
					'button',
					{ name: 'Audit log' },
				),
			).not.toBeInTheDocument(),
		);
		expect(toggle('Activity source', 'Everything')).toHaveAttribute('aria-pressed', 'true');
	});

	it('renders the API calls source with trace rows', async () => {
		renderMonitor();
		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
		expect(screen.getByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();
		expect(screen.getAllByText('Completed').length).toBeGreaterThanOrEqual(1);
		expect(screen.getAllByText('Failed').length).toBeGreaterThanOrEqual(1);
	});

	it('filters API calls by terminal status (backend accepts only completed/failed)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('GET /repos/{owner}/{repo}');

		// Failed-only: the github 503 row stays, the completed charge row drops.
		await user.click(toggle('Status', 'Failed'));
		await waitFor(() => {
			expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();
		});
		expect(screen.getByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();
		expect(currentParams().get('status')).toBe('failed');

		// Succeeded-only: the inverse.
		await user.click(toggle('Status', 'Succeeded'));
		await waitFor(() => {
			expect(screen.queryByText('GET /repos/{owner}/{repo}')).not.toBeInTheDocument();
		});
		expect(screen.getByText('POST /v1/charges')).toBeInTheDocument();
		expect(currentParams().get('status')).toBe('completed');
	});

	// --- local-MCP 2-E2 (#1188): origin filter on API calls ----------------

	it('filters API calls by origin from the toolbar', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		const originSelect = screen.getByRole('combobox', { name: 'Filter by origin' });
		await user.selectOptions(originSelect, screen.getByRole('option', { name: 'MCP' }));

		expect(await screen.findByText('POST /v1/refunds')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();
		});
		expect(currentParams().get('origin')).toBe('mcp');

		await user.selectOptions(originSelect, screen.getByRole('option', { name: 'All origins' }));
		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
		expect(currentParams().get('origin')).toBeNull();
	});

	it('honours an ?origin deep-link and drops it on a source switch (calls-only scope)', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=calls&origin=mcp');

		expect(await screen.findByText('POST /v1/refunds')).toBeInTheDocument();
		expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();

		await user.click(toggle('Activity source', 'Everything'));
		await waitFor(() => {
			expect(currentParams().get('origin')).toBeNull();
		});
		expect(currentParams().get('show')).toBeNull();
		expect(
			screen.queryByRole('combobox', { name: 'Filter by origin' }),
		).not.toBeInTheDocument();
	});

	// --- theme-5 5d: retired ?toolkit_id= deep links (scrub is deletable in 6b)

	it('ignores a retired ?toolkit_id= deep link and scrubs it from the URL', async () => {
		renderMonitor('/app/monitor?show=calls&toolkit_id=tk_0123456789abcdef&days=7');

		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
		expect(screen.getByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();

		await waitFor(() => {
			expect(currentParams().get('toolkit_id')).toBeNull();
		});
		expect(currentParams().get('show')).toBe('calls');
		expect(currentParams().get('days')).toBe('7');
	});

	it('connects the feed live without crashing on heartbeat frames', async () => {
		renderMonitor('/app/monitor?view=activity');
		// The SSE mock interleaves a heartbeat frame (no severity) with real
		// events; the always-on stream must drop it rather than throw.
		await screen.findByRole('link', { name: 'Execution failed: github-api' });
		expect(await screen.findByText('Live')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Import completed' })).toBeInTheDocument();
	});

	it('pauses and resumes the live feed', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?view=activity');
		await screen.findByRole('link', { name: 'Execution failed: github-api' });

		await user.click(screen.getByRole('button', { name: 'Pause' }));
		expect(screen.getByText('Paused')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Resume' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);

		await user.click(screen.getByRole('button', { name: 'Resume' }));
		expect(screen.getByRole('button', { name: 'Pause' })).toHaveAttribute(
			'aria-pressed',
			'false',
		);
	});

	it('acknowledges an action event from its feed row', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?view=activity');
		const row = await screen.findByRole('link', { name: 'Execution failed: github-api' });

		await user.click(within(row).getByRole('button', { name: 'Acknowledge' }));

		expect(await screen.findByText('Event acknowledged')).toBeInTheDocument();
		// The ack button must not also open the row's detail sheet.
		expect(currentParams().get('trace_id')).toBeNull();
		expect(await within(row).findByText('Acknowledged')).toBeInTheDocument();
	});

	it('folds a run of successful calls into one expandable row', async () => {
		const user = userEvent.setup();
		const now = Date.now();
		const completed = (i: number) => ({
			_links: { self: `/events/evt_run_${i}`, execution: `/executions/exec_run_${i}` },
			acknowledged: false,
			acknowledged_at: null,
			acknowledged_by: null,
			created_at: new Date(now - i * 60_000).toISOString(),
			data: { execution_id: `exec_run_${i}` },
			detail: null,
			event_id: `evt_run_${i}`,
			requires_action: false,
			severity: 'info',
			summary: `Execution completed: op_${i}`,
			trace_id: null,
			type: 'execution.completed',
		});
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({
					data: [completed(1), completed(2), completed(3)],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderMonitor('/app/monitor?view=activity');

		const run = await screen.findByRole('link', { name: /3 successful calls — expand/ });
		expect(screen.queryByRole('link', { name: 'Execution completed: op_1' })).toBeNull();

		await user.click(run);
		expect(
			await screen.findByRole('link', { name: 'Execution completed: op_1' }),
		).toBeInTheDocument();
		expect(screen.getByRole('link', { name: /3 successful calls — collapse/ })).toBeVisible();
	});

	it('renders the stat strip + volume, bubble and breakdown charts from the usage endpoint (#561)', async () => {
		renderMonitor('/app/monitor');

		expect(await screen.findByText(/Last 7 days, colored by API/)).toBeInTheDocument();
		expect(screen.getByRole('link', { name: /^Calls/ })).toBeInTheDocument();
		expect(screen.getByText('Success rate')).toBeInTheDocument();
		expect(screen.getByText('p95 latency')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: /Failed/ })).toBeInTheDocument();

		expect(screen.getByText('APIs active')).toBeInTheDocument();
		expect(screen.getByRole('img', { name: /bubble chart/ })).toBeInTheDocument();
		const breakdown = screen.getByRole('region', { name: 'Breakdown' });
		expect(within(breakdown).getByText('stripe-api')).toBeInTheDocument();
		expect(within(breakdown).getByText('github-api')).toBeInTheDocument();
	});

	it('links the Failed KPI to failed API calls, preserving window and actor', async () => {
		renderMonitor('/app/monitor?days=30&actor_id=agent_billing&actor_type=agent');
		await screen.findByText(/Last 30 days/);
		const failed = screen.getByRole('link', { name: /Failed/ });
		const href = new URL(failed.getAttribute('href') ?? '', 'http://x');
		expect(href.searchParams.get('show')).toBe('calls');
		expect(href.searchParams.get('status')).toBe('failed');
		expect(href.searchParams.get('days')).toBe('30');
		expect(href.searchParams.get('actor_id')).toBe('agent_billing');
		expect(href.searchParams.get('actor_type')).toBe('agent');
	});

	it('fetches each grouping once, scoped by the actor, and nothing once expanded', async () => {
		// Grouped reads only: the expanded log's timeline makes its own
		// ungrouped histogram read, which isn't a Breakdown refetch.
		const seen: URLSearchParams[] = [];
		worker.events.on('request:start', ({ request }) => {
			const url = new URL(request.url);
			if (url.pathname.endsWith('/monitoring/usage') && url.searchParams.has('group_by'))
				seen.push(url.searchParams);
		});
		const user = userEvent.setup();
		renderMonitor('/app/monitor?actor_id=agent_billing&actor_type=agent');
		await screen.findByText('Breakdown');
		expect(seen.map((p) => p.get('group_by')).sort()).toEqual(['agent', 'api']);
		expect(seen.every((p) => p.get('agent_id') === 'agent_billing')).toBe(true);

		// Lens toggles are instant — every grouping is already loaded.
		await user.click(breakdownLens('Agents'));
		await user.click(screen.getByRole('button', { name: 'Expand activity to the full log' }));
		await screen.findByRole('group', { name: 'Activity source' });
		expect(seen).toHaveLength(2);
		worker.events.removeAllListeners();
	});

	it('regroups the Breakdown by Agents and drills a row into filtered API calls', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor');
		await screen.findByText('Breakdown');

		await user.click(breakdownLens('Agents'));
		expect(await screen.findByText('agent_billing')).toBeInTheDocument();
		expect(screen.getByText('Unattributed').closest('a')).toBeNull();

		await user.click(screen.getByRole('row', { name: /View executions for agent_billing/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('show')).toBe('calls');
			expect(params.get('actor_id')).toBe('agent_billing');
			expect(params.get('actor_type')).toBe('agent');
		});
	});

	it('drills an API row into API calls filtered by that API', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor');
		await screen.findByText('Breakdown');
		await user.click(screen.getByRole('row', { name: /View executions for github-api/ }));

		expect(
			await screen.findByRole('button', { name: /Clear API filter github\/github-api/ }),
		).toBeInTheDocument();
		expect(await screen.findByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();
		expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();
		expect(currentParams().get('api')).toBe('github:github-api');

		// Switching source drops the calls-only api filter.
		await user.click(toggle('Activity source', 'Jobs'));
		await waitFor(() => {
			expect(currentParams().get('api')).toBeNull();
		});
	});

	it('reloads the Overview when the window changes', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor');
		await screen.findByText(/Last 7 days/);

		// "All" isn't offered (the aggregate needs a bounded window).
		expect(
			within(screen.getByRole('group', { name: 'Time window' })).queryByRole('button', {
				name: 'All',
			}),
		).not.toBeInTheDocument();
		await user.click(toggle('Time window', '30d'));
		expect(await screen.findByText(/Last 30 days/)).toBeInTheDocument();

		await user.click(toggle('Time window', '24h'));
		expect(await screen.findByText(/Last 24 hours/)).toBeInTheDocument();
		expect(screen.queryByText('No executions yet')).not.toBeInTheDocument();
	});

	it('switches to the Jobs source', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		await user.click(toggle('Activity source', 'Jobs'));

		// Jobs read as sentences: kind + state.
		expect(await screen.findByText('Import running')).toBeInTheDocument();
		expect(screen.getByText('Execution failed')).toBeInTheDocument();
		expect(currentParams().get('show')).toBe('jobs');
	});

	it('opens a job and surfaces the org:admin Cancel action for an active job', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=jobs');

		const runningRow = await screen.findByText('job_import_1');
		await user.click(runningRow);

		const dialog = await screen.findByRole('dialog');
		expect(
			await within(dialog).findByRole('button', { name: 'Cancel job' }),
		).toBeInTheDocument();
	});

	it('switches to the Audit log source and shows actors', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		await user.click(toggle('Activity source', 'Audit log'));

		// Audit entries read as sentences, not raw action codes.
		expect(await screen.findByText('Started an execution')).toBeInTheDocument();
		expect(screen.getByText('Cancelled a job')).toBeInTheDocument();
		expect(screen.getAllByText('Admin User').length).toBeGreaterThanOrEqual(1);
		// The Audit log has no status axis.
		expect(screen.queryByRole('group', { name: 'Status' })).not.toBeInTheDocument();
	});

	it('filters the Audit log by trace_id deep-link param', async () => {
		renderMonitor('/app/monitor?show=audit&trace_id=trace_aaaaaaaa');
		expect(await screen.findByText('Started an execution')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.queryByText('Cancelled a job')).not.toBeInTheDocument();
		});
	});

	it('shows the trace actor read from the execution record (#375)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await user.click(await screen.findByText('POST /v1/charges'));

		const sheet = await screen.findByRole('dialog');
		expect(await within(sheet).findByText(/Billing Agent/)).toBeInTheDocument();
	});

	it('surfaces an error when the executions feed fails', async () => {
		worker.use(createErrorHandler('get', '/executions', { status: 500 }));
		renderMonitor();
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('surfaces an error when the event feed fails', async () => {
		worker.use(createErrorHandler('get', '/events', { status: 500 }));
		renderMonitor('/app/monitor?view=activity');
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('renders the toolbar with a window toggle and actor picker', async () => {
		renderMonitor();
		await screen.findByText('POST /v1/charges');

		const toolbar = screen.getByRole('toolbar', { name: 'Activity filters' });
		expect(within(toolbar).getByRole('button', { name: '7d' })).toBeInTheDocument();
		const actorSelect = within(toolbar).getByRole('combobox', { name: 'Filter by actor' });
		expect(actorSelect).toBeEnabled();
		expect(await within(actorSelect).findByText(/Billing Agent/)).toBeInTheDocument();
	});

	it('shows the window + actor filters on the Overview too', async () => {
		renderMonitor('/app/monitor');
		await screen.findByText('Breakdown');
		const toolbar = screen.getByRole('toolbar', { name: 'Monitor filters' });
		expect(within(toolbar).getByRole('combobox', { name: 'Filter by actor' })).toBeEnabled();
	});

	it('disables the actor picker on the Jobs source (no backend actor filter)', async () => {
		renderMonitor('/app/monitor?show=jobs');
		await screen.findByText('job_import_1');
		expect(screen.getByRole('combobox', { name: 'Filter by actor' })).toBeDisabled();
	});

	it('filters API calls by the selected actor', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('GET /repos/{owner}/{repo}');

		await user.selectOptions(
			screen.getByRole('combobox', { name: 'Filter by actor' }),
			screen.getByRole('option', { name: /Admin User/ }),
		);
		await waitFor(() => {
			expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();
		});
		expect(screen.getByText('GET /repos/{owner}/{repo}')).toBeInTheDocument();
	});

	it('pages API calls with the cursor pager (Older / Newer)', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await screen.findByText('POST /v1/charges');
		expect(screen.queryByText('POST /v1/refunds')).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Older results' }));
		expect(await screen.findByText('POST /v1/refunds')).toBeInTheDocument();
		expect(screen.queryByText('POST /v1/charges')).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Newer results' }));
		expect(await screen.findByText('POST /v1/charges')).toBeInTheDocument();
	});

	it('keeps window + actor but clears per-source status when switching source', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=calls&status=failed&days=30&actor_id=user_admin');
		await screen.findByText('GET /repos/{owner}/{repo}');

		await user.click(toggle('Activity source', 'Everything'));
		await waitFor(() => {
			expect(toggle('Activity source', 'Everything')).toHaveAttribute('aria-pressed', 'true');
		});

		const params = currentParams();
		expect(params.get('days')).toBe('30');
		expect(params.get('actor_id')).toBe('user_admin');
		expect(params.get('status')).toBeNull();
		expect(params.get('show')).toBeNull();
	});

	it('keeps window + actor but drops log params when folding back to the Overview', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=calls&status=failed&days=30&actor_id=user_admin');
		await screen.findByText('GET /repos/{owner}/{repo}');

		await user.click(screen.getByRole('button', { name: 'Overview' }));
		await screen.findByText(/Last 30 days/);

		const params = currentParams();
		expect(params.get('days')).toBe('30');
		expect(params.get('actor_id')).toBe('user_admin');
		expect(params.get('status')).toBeNull();
		expect(params.get('show')).toBeNull();
	});

	it('has no critical a11y violations on API calls', async () => {
		const { container } = renderMonitor();
		await screen.findByText('POST /v1/charges');
		await checkA11y(container);
	});

	it('has no critical a11y violations on the Everything feed', async () => {
		const { container } = renderMonitor('/app/monitor?view=activity');
		await screen.findByRole('link', { name: 'Execution failed: github-api' });
		await checkA11y(container);
	});
});

/**
 * Links from before the Activity/Usage redesign carried the five-tab
 * vocabulary. They're rewritten in place (no history entry) on arrival.
 */
describe('Monitor legacy ?tab= normalization', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
	});

	it.each([
		['tab=overview', { tab: null, show: null, view: null }],
		['tab=usage&lens=agents', { tab: null, view: null, lens: null }],
		['tab=activity', { tab: null, view: 'activity' }],
		['tab=executions&status=failed', { tab: null, show: 'calls', status: 'failed' }],
		['tab=jobs&job_id=job_import_1', { tab: null, show: 'jobs', job_id: 'job_import_1' }],
		[
			'tab=audit&trace_id=trace_aaaaaaaa',
			{ tab: null, show: 'audit', trace_id: 'trace_aaaaaaaa' },
		],
		[
			'tab=events&severity=error&status=x&live=1',
			{ tab: null, show: null, view: 'activity', severity: null, status: null, live: null },
		],
	])('rewrites ?%s', async (query, expected) => {
		renderMonitor(`/app/monitor?${query}`);
		await waitFor(() => {
			const params = currentParams();
			for (const [key, value] of Object.entries(expected)) {
				expect(params.get(key), key).toBe(value);
			}
		});
	});
});

/**
 * Cross-source deep-linking. Every Monitor surface can pivot to another via
 * the URL param vocabulary in lib/links.ts; these cover each direction and
 * the "unknown trace" degradation (the backend stores `trace_id="unknown"`
 * for header-less runs, which must never produce a broken trace/audit link).
 */
describe('Monitor inter-linking', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
	});

	it('API call row → trace sheet → "View in audit" carries trace_id', async () => {
		const user = userEvent.setup();
		renderMonitor();
		await user.click(await screen.findByText('POST /v1/charges'));

		await screen.findByRole('link', { name: /View trace .* in the audit log/ });
		expect(currentParams().get('trace_id')).toBe('trace_aaaaaaaa');

		await user.click(screen.getByRole('link', { name: /View trace .* in the audit log/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('show')).toBe('audit');
			expect(params.get('trace_id')).toBe('trace_aaaaaaaa');
		});
	});

	it('API call with unknown trace opens by execution_id, no audit link', async () => {
		renderMonitor('/app/monitor?show=calls&execution_id=exec_4');

		// The record leads with the operation; the raw id sits under it.
		const dialog = await screen.findByRole('dialog');
		expect(
			await within(dialog).findByRole('heading', { name: 'POST /chat.postMessage' }),
		).toBeInTheDocument();
		expect(within(dialog).getByText('Execution')).toBeInTheDocument();
		expect(within(dialog).getAllByText('exec_4').length).toBeGreaterThanOrEqual(1);
		expect(
			screen.queryByRole('link', { name: /View trace .* in the audit log/ }),
		).not.toBeInTheDocument();
		expect(currentParams().get('trace_id')).toBeNull();
	});

	it('Execution deep-link with a real trace opens the trace, not "no trace recorded"', async () => {
		renderMonitor('/app/monitor?show=calls&execution_id=exec_1');
		expect(await screen.findByText('Trace')).toBeInTheDocument();
		expect(screen.queryByText(/No trace recorded/)).not.toBeInTheDocument();
		expect(
			await screen.findByRole('link', { name: /View trace .* in the audit log/ }),
		).toBeInTheDocument();
	});

	it('Jobs row → job sheet → "View in audit" sends target_type + target_id (no 400)', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=jobs');
		await user.click(await screen.findByText('job_import_1'));

		const auditLink = await screen.findByRole('link', {
			name: /View job .* in the audit log/,
		});
		await user.click(auditLink);
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('show')).toBe('audit');
			expect(params.get('target_type')).toBe('job');
			expect(params.get('target_id')).toBe('job_import_1');
		});
		const badge = (await screen.findByText('target:')).closest('span, div') as HTMLElement;
		expect(within(badge).getByText('job_import_1')).toBeInTheDocument();
	});

	it('clears an Audit log deep-link filter from its badge', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=audit&target_type=job&target_id=job_import_1');
		await screen.findByText('target:');

		await user.click(screen.getByRole('button', { name: /Clear/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('target_type')).toBeNull();
			expect(params.get('target_id')).toBeNull();
			expect(params.get('show')).toBe('audit');
		});
		expect(await screen.findByText('Started an execution')).toBeInTheDocument();
	});

	it('Job sheet → linked execution deep-links by execution_id', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=jobs');
		await user.click(await screen.findByText('job_import_2'));
		await user.click(await screen.findByRole('link', { name: /Open execution exec_1/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('show')).toBe('calls');
			expect(params.get('execution_id')).toBe('exec_1');
		});
	});

	it('Audit row → API calls (trace) and → Jobs (job) links round-trip', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=audit');

		await user.click(await screen.findByRole('link', { name: /Open trace .* in API calls/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('show')).toBe('calls');
			expect(params.get('trace_id')).toBe('trace_aaaaaaaa');
		});

		await user.click(toggle('Activity source', 'Audit log'));
		await user.click(await screen.findByRole('link', { name: /Open job .* in Jobs/ }));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('show')).toBe('jobs');
			expect(params.get('job_id')).toBe('job_import_1');
		});
	});

	it('switching source clears every per-source deep-link param', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?show=audit&target_type=job&target_id=job_import_1&days=7');
		await screen.findByText('target:');

		await user.click(toggle('Activity source', 'API calls'));
		await waitFor(() => {
			const params = currentParams();
			expect(params.get('target_type')).toBeNull();
			expect(params.get('target_id')).toBeNull();
			expect(params.get('days')).toBe('7');
		});
	});

	it('the live stream subscribes with a `since` lower bound', async () => {
		let streamUrl: URL | null = null;
		worker.use(
			http.get('/events/stream', ({ request }) => {
				streamUrl = new URL(request.url);
				return new HttpResponse(': keep-alive\n\n', {
					headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
				});
			}),
		);

		renderMonitor('/app/monitor?view=activity&days=1');

		await waitFor(() => expect(streamUrl).not.toBeNull());
		// Anchored at mount so a backlog-replaying stream can't resurface history.
		const since = streamUrl!.searchParams.get('since');
		expect(since).toBeTruthy();
		expect(Date.now() - Date.parse(since!)).toBeLessThan(5 * 60_000);
	});

	it('feed row → clicking a failed event opens its trace detail sheet (#617)', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?view=activity');

		await user.click(await screen.findByRole('link', { name: 'Execution failed: github-api' }));

		await waitFor(() => {
			expect(currentParams().get('trace_id')).toBe('trace_bbbbbbbb');
		});
		expect(await screen.findByRole('dialog')).toBeInTheDocument();
	});

	it('feed row → a job event opens the job detail sheet in place', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?view=activity');

		await user.click(await screen.findByRole('link', { name: 'Import completed' }));

		await waitFor(() => {
			expect(currentParams().get('job_id')).toBe('job_import_2');
		});
		expect(await screen.findByRole('dialog')).toBeInTheDocument();
		expect(currentParams().get('show')).toBeNull();
	});

	it('feed row → a failure with only a _links.execution (no trace) opens by execution_id (#617)', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({
					data: [
						{
							_links: {
								self: '/events/evt_link',
								execution: '/executions/exec_99',
								job: null,
								action: null,
							},
							acknowledged: false,
							acknowledged_at: null,
							acknowledged_by: null,
							created_at: new Date().toISOString(),
							data: {},
							detail: 'Upstream 401 from api.example.com',
							event_id: 'evt_link',
							requires_action: true,
							severity: 'error',
							summary: 'Execution failed: example-api',
							trace_id: null,
							type: 'execution.failed',
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		renderMonitor('/app/monitor?view=activity');

		await user.click(
			await screen.findByRole('link', { name: 'Execution failed: example-api' }),
		);

		await waitFor(() => {
			expect(currentParams().get('execution_id')).toBe('exec_99');
		});
		expect(currentParams().get('trace_id')).toBeNull();
	});

	it('rail-style deep-link (execution_id) on API calls opens the sheet (#617)', async () => {
		renderMonitor('/app/monitor?show=calls&execution_id=exec_2');
		expect(await screen.findByRole('dialog')).toBeInTheDocument();
	});
});

/**
 * Everything-feed status chips. Failed maps to the backend's repeatable
 * `severity=` (error + critical); Needs you to unacknowledged
 * `requires_action`.
 */
describe('Monitor feed status filter', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
	});

	it('Failed narrows the feed to error/critical and writes ?status=failed', async () => {
		const seen: URLSearchParams[] = [];
		worker.events.on('request:start', ({ request }) => {
			const url = new URL(request.url);
			if (url.pathname === '/events') seen.push(url.searchParams);
		});
		const user = userEvent.setup();
		renderMonitor('/app/monitor?view=activity');
		await screen.findByRole('link', { name: 'Import completed' });

		await user.click(toggle('Status', 'Failed'));

		await waitFor(() => expect(currentParams().get('status')).toBe('failed'));
		await waitFor(() => {
			expect(
				screen.queryByRole('link', { name: 'Import completed' }),
			).not.toBeInTheDocument();
		});
		expect(
			screen.getByRole('link', { name: 'Execution failed: github-api' }),
		).toBeInTheDocument();
		expect(seen[seen.length - 1]?.getAll('severity').sort()).toEqual(['critical', 'error']);
		worker.events.removeAllListeners();
	});

	it('Needs you shows only unacknowledged action events', async () => {
		const user = userEvent.setup();
		renderMonitor('/app/monitor?view=activity');
		await screen.findByRole('link', { name: 'Import completed' });

		await user.click(toggle('Status', 'Needs you'));

		await waitFor(() => expect(currentParams().get('status')).toBe('action'));
		await waitFor(() => {
			expect(
				screen.queryByRole('link', { name: 'Import completed' }),
			).not.toBeInTheDocument();
		});
		expect(
			screen.getByRole('link', { name: 'Execution failed: github-api' }),
		).toBeInTheDocument();
	});

	it('an empty filtered feed offers "Show everything" and it clears the status', async () => {
		const user = userEvent.setup();
		worker.use(
			http.get('/events', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		renderMonitor('/app/monitor?view=activity&status=action');

		expect(await screen.findByText('Nothing matches')).toBeInTheDocument();
		expect(toggle('Status', 'Needs you')).toHaveAttribute('aria-pressed', 'true');

		await user.click(screen.getByRole('button', { name: 'Show everything' }));
		await waitFor(() => expect(currentParams().get('status')).toBeNull());
		expect(await screen.findByText('No activity yet')).toBeInTheDocument();
	});
});
